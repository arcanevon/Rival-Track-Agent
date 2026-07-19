"""跨领域采集质量基准。

该工具只运行搜索、正文抓取、证据验收和覆盖率计算，不调用付费大模型。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from src.config.router import resolve_industry_keyword
from src.intake.constants import SOURCE_BUCKETS, is_candidate_source_only, source_bucket_for_candidate
from src.intake.discovery import build_source_candidates, build_source_candidates_with_search
from src.intake.hydrate import hydrate_sources_for_analysis


DEFAULT_SCENARIOS = PROJECT_ROOT / "data" / "domain-benchmark-scenarios.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "benchmark"


def load_scenarios(path: Path) -> list[dict]:
    """读取并校验跨领域样例。"""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("基准样例必须是非空 JSON 数组")
    required = {"id", "industry", "track", "threat_target", "competitor"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError(f"第 {index + 1} 个样例缺少必要字段")
        resolved = resolve_industry_keyword(str(row["track"]), str(row["competitor"]))
        if resolved != row["industry"]:
            raise ValueError(f"样例 {row['id']} 行业路由不一致：期望 {row['industry']}，实际 {resolved}")
    return rows


def _source_priority(source: dict) -> tuple[int, int]:
    """稳定来源优先，其次使用搜索相关性分。"""
    stable_bonus = 200 if source.get("type") != "web-search-result" else 0
    return stable_bonus + int(source.get("search_score", 0) or 0), len(str(source.get("search_snippet", "")))


def prepare_competitor(scenario: dict, sources: list[dict], max_per_bucket: int = 3) -> dict:
    """候选先放临时桶；正文验收后，采集层会按实际来源类型重新分桶。"""
    entry: dict = {
        "company": scenario["competitor"],
        "track": scenario["track"],
        "metadata": {"candidate_sources": []},
        **{bucket: [] for bucket in SOURCE_BUCKETS},
    }
    grouped = {bucket: [] for bucket in SOURCE_BUCKETS}
    for source in sources:
        if not isinstance(source, dict):
            continue
        if is_candidate_source_only(source):
            entry["metadata"]["candidate_sources"].append(source)
        else:
            grouped[source_bucket_for_candidate(source)].append(source)
    for bucket, candidates in grouped.items():
        ranked = sorted(candidates, key=_source_priority, reverse=True)
        entry[bucket] = ranked[:max_per_bucket]
        for deferred in ranked[max_per_bucket:]:
            entry["metadata"]["candidate_sources"].append({
                **deferred,
                "candidate_only": True,
                "deferred_by_budget": True,
                "evidence_status": "deferred_by_benchmark_budget",
            })
    return entry


def quality_gate(result: dict) -> tuple[str, list[str]]:
    """应用统一门禁，避免凭一个可读页面宣布采集成功。"""
    reasons: list[str] = []
    if result["strong_sources"] < 2:
        reasons.append("强证据少于 2 条")
    if result["covered_buckets"] < 2:
        reasons.append("O/B/C/L 覆盖少于 2 类")
    if result["precision_at_5"] < 0.6:
        reasons.append("前五条证据相关率低于 60%")
    if result["bad_domain_leakage"] > 0.2:
        reasons.append("低质量域名泄漏率高于 20%")
    if not reasons:
        return "pass", []
    return ("review" if result["strong_sources"] > 0 else "fail"), reasons


def summarize_scenario(scenario: dict, entry: dict, elapsed_seconds: float, search_message: str) -> dict:
    """生成可保存、可比较的单场景指标。"""
    metadata = entry.get("metadata", {})
    coverage = metadata.get("source_coverage", {})
    relevance = metadata.get("evidence_relevance", {})
    accepted_sources = [
        source for bucket in SOURCE_BUCKETS for source in entry.get(bucket, []) or []
        if isinstance(source, dict) and (source.get("evidence_verdict") or {}).get("accepted") is True
    ]
    rejected_sources = [
        source for source in metadata.get("candidate_sources", []) or [] if isinstance(source, dict)
    ]
    rejected_sources.sort(
        key=lambda source: (
            isinstance(source.get("evidence_verdict"), dict),
            source.get("type") == "web-search-result",
            not source.get("deferred_by_budget", False),
        ),
        reverse=True,
    )
    rejection_reasons = Counter(
        str((source.get("evidence_verdict") or {}).get("reject_reason")
            or source.get("evidence_status")
            or (source.get("source_quality") or {}).get("status", "候选来源"))
        for source in rejected_sources
    )
    independent_domains = {
        urlparse(str(source.get("url") or source.get("evidence_url") or "")).netloc.lower()
        for source in accepted_sources
    } - {""}
    result = {
        **scenario,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "search_message": search_message,
        "coverage": coverage,
        "covered_buckets": sum(
            1 for details in coverage.values()
            if isinstance(details, dict) and details.get("status") == "covered"
        ),
        "strong_sources": len(accepted_sources),
        "independent_domains": len(independent_domains),
        "evaluated_sources": int(relevance.get("evaluated_sources", 0) or 0),
        "precision_at_5": float(relevance.get("precision_at_5", 0) or 0),
        "bad_domain_leakage": float(relevance.get("bad_domain_leakage", 0) or 0),
        "candidate_sources": len(rejected_sources),
        "search_candidates": sum(source.get("type") == "web-search-result" for source in rejected_sources),
        "deferred_by_budget": sum(bool(source.get("deferred_by_budget")) for source in rejected_sources),
        "rejection_reasons": dict(rejection_reasons.most_common()),
        "accepted": [
            {
                "label": source.get("label", ""),
                "url": source.get("url") or source.get("evidence_url", ""),
                "source_type": (source.get("evidence_verdict") or {}).get("actual_source_type", ""),
                "relevance_score": (source.get("evidence_verdict") or {}).get("relevance_score", 0),
                "text_length": len(str(source.get("scraped_text", ""))),
            }
            for source in accepted_sources
        ],
        "rejected": [
            {
                "label": source.get("label", ""),
                "url": source.get("url") or source.get("evidence_url", ""),
                "reason": (source.get("evidence_verdict") or {}).get("reject_reason")
                or (source.get("source_quality") or {}).get("status", "候选来源"),
            }
            for source in rejected_sources[:12]
        ],
    }
    result["gate_status"], result["gate_reasons"] = quality_gate(result)
    return result


def aggregate_results(results: list[dict], elapsed_seconds: float) -> dict:
    """汇总与 Verda 指标面板相近的效率、覆盖和质量指标。"""
    total = max(1, len(results))
    passed = sum(row["gate_status"] == "pass" for row in results)
    return {
        "scenario_count": len(results),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "passed": passed,
        "review": sum(row["gate_status"] == "review" for row in results),
        "failed": sum(row["gate_status"] == "fail" for row in results),
        "pass_rate": round(passed / total, 3),
        "average_strong_sources": round(sum(row["strong_sources"] for row in results) / total, 2),
        "average_bucket_coverage": round(sum(row["covered_buckets"] for row in results) / (total * 4), 3),
        "average_precision_at_5": round(sum(row["precision_at_5"] for row in results) / total, 3),
        "average_bad_domain_leakage": round(sum(row["bad_domain_leakage"] for row in results) / total, 3),
        "independent_domains": sum(row["independent_domains"] for row in results),
    }


async def run_scenario(
    session: ClientSession,
    scenario: dict,
    use_search: bool,
    max_per_bucket: int = 3,
) -> dict:
    """运行单个行业样例。"""
    started = time.perf_counter()
    if use_search:
        sources, message = await build_source_candidates_with_search(
            session, scenario["competitor"], scenario["track"], count=4,
        )
    else:
        sources = build_source_candidates(scenario["competitor"], scenario["track"])
        message = "离线候选来源"
    hydrated = await hydrate_sources_for_analysis(
        [prepare_competitor(scenario, sources, max_per_bucket=max_per_bucket)], scenario["track"]
    )
    return summarize_scenario(scenario, hydrated[0], time.perf_counter() - started, message)


def print_result(result: dict) -> None:
    """输出适合终端阅读的摘要。"""
    print(
        f"[{result['gate_status'].upper():6}] {result['industry']:<18} "
        f"{result['competitor']:<16} 强证据={result['strong_sources']} "
        f"覆盖={result['covered_buckets']}/4 P@5={result['precision_at_5']:.0%} "
        f"低质泄漏={result['bad_domain_leakage']:.0%} 用时={result['elapsed_seconds']:.1f}s",
        flush=True,
    )
    for reason in result["gate_reasons"]:
        print(f"         - {reason}", flush=True)


async def async_main(args: argparse.Namespace) -> Path:
    scenarios = load_scenarios(Path(args.scenarios))
    if args.limit:
        scenarios = scenarios[: args.limit]
    started = time.perf_counter()
    output = Path(args.output) if args.output else DEFAULT_OUTPUT_DIR / f"domains-{datetime.now():%Y%m%d-%H%M%S}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    if args.resume and output.is_file():
        previous = json.loads(output.read_text(encoding="utf-8"))
        results = [row for row in previous.get("results", []) if isinstance(row, dict)]
    completed_ids = {str(row.get("id", "")) for row in results}
    pending = [row for row in scenarios if str(row["id"]) not in completed_ids]
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def bounded_run(session: ClientSession, scenario: dict) -> dict:
        async with semaphore:
            return await run_scenario(session, scenario, args.search, args.max_per_bucket)

    async with ClientSession(timeout=ClientTimeout(total=args.timeout)) as session:
        tasks = [asyncio.create_task(bounded_run(session, scenario)) for scenario in pending]
        for task in asyncio.as_completed(tasks):
            result = await task
            results.append(result)
            print_result(result)
            checkpoint = {
                "benchmark": "cross-domain-evidence-quality",
                "partial": True,
                "search_enabled": args.search,
                "summary": aggregate_results(results, time.perf_counter() - started),
                "results": results,
            }
            output.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "benchmark": "cross-domain-evidence-quality",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "search_enabled": args.search,
        "summary": aggregate_results(results, time.perf_counter() - started),
        "results": results,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"报告已保存：{output}")
    return output


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行跨领域竞品证据采集基准")
    parser.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS), help="样例 JSON 文件")
    parser.add_argument("--output", default="", help="结果 JSON 文件")
    parser.add_argument("--search", action="store_true", help="启用真实搜索 API")
    parser.add_argument("--limit", type=int, default=0, help="只运行前 N 个样例")
    parser.add_argument("--max-per-bucket", type=int, default=3, help="每类最多抓取多少个候选页面")
    parser.add_argument("--concurrency", type=int, default=2, help="并发运行的行业场景数")
    parser.add_argument("--resume", action="store_true", help="从已有输出文件继续未完成场景")
    parser.add_argument("--timeout", type=int, default=90, help="单次 HTTP 请求总超时秒数")
    args = parser.parse_args(argv)
    asyncio.run(async_main(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
