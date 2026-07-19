"""Benchmark script evaluating the collection agent's ability to discover strong evidence links.

Usage:
    python src/tools/benchmark_links.py --tracks ai-coding new-energy-vehicles milktea --search
    python src/tools/benchmark_links.py --tracks ai-coding           # offline mode
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.intake.constants import SOURCE_BUCKETS
from src.intake.discovery import build_source_candidates_with_search
from src.intake.hydrate import hydrate_sources_for_analysis
from src.intake.plan import build_evidence_acquisition_plan, build_evidence_gaps
from src.intake.quality import assess_source_quality, source_coverage_for_competitor
from src.pipeline.cache import CACHE_DIR, _cache_with_evidence_plans, load_cache

DATA_DIR = PROJECT_ROOT / "data"
BENCHMARK_DIR = DATA_DIR / "benchmark"


def _track_competitors(track: str) -> list[dict]:
    """Load competitor entries for a track from cached JSON files."""
    track_dir = CACHE_DIR / track
    if not track_dir.is_dir():
        print(f"  [WARN] Track directory not found: {track_dir}")
        return []
    entries: list[dict] = []
    for fpath in sorted(track_dir.glob("*.json")):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            entries.append(data)
        except Exception as exc:
            print(f"  [WARN] Skipping {fpath.name}: {exc}")
    return entries


def print_coverage_table(track: str, results: list[dict]):
    """Print a terminal table of O/B/C/L coverage per competitor."""
    print(f"\n{'='*80}")
    print(f"  Track: {track}")
    print(f"{'='*80}")
    header = f"  {'Competitor':<20} {'O':>10} {'B':>10} {'C':>10} {'L':>10} {'Strong':>8} {'P@5':>7} {'Leak':>7} {'Gaps':>6}"
    print(header)
    print(f"  {'-'*76}")

    for entry in results:
        company = entry.get("company", "?")
        cov = entry.get("metadata", {}).get("source_coverage", {})
        strong_total = 0
        candidate_total = 0
        relevance = entry.get("metadata", {}).get("evidence_relevance", {})
        gap_count = len(entry.get("metadata", {}).get("evidence_gaps", []))

        o = cov.get("official", {})
        b = cov.get("benchmark", {})
        c = cov.get("community", {})
        l = cov.get("leading", {})

        def bucket_str(bucket: dict) -> str:
            strong = bucket.get("strong_count", 0)
            candidate = bucket.get("candidate_count", 0)
            status = bucket.get("status", "?")
            if status == "covered":
                return f"✓ {strong}s"
            elif status == "candidate_only":
                return f"~ {candidate}c"
            return "MISS"

        o_str = bucket_str(o)
        b_str = bucket_str(b)
        c_str = bucket_str(c)
        l_str = bucket_str(l)

        for bucket_name in ("official", "benchmark", "community", "leading"):
            bkt = cov.get(bucket_name, {})
            strong_total += bkt.get("strong_count", 0)
            candidate_total += bkt.get("candidate_count", 0)

        precision = float(relevance.get("precision_at_5", 0) or 0)
        leakage = float(relevance.get("bad_domain_leakage", 0) or 0)
        print(f"  {company:<20} {o_str:>10} {b_str:>10} {c_str:>10} {l_str:>10} {strong_total:>8} {precision:>7.0%} {leakage:>7.0%} {gap_count:>6}")

    # 汇总行
    all_covered = sum(
        1 for r in results
        for b in ("official", "benchmark", "community", "leading")
        if r.get("metadata", {}).get("source_coverage", {}).get(b, {}).get("status") == "covered"
    )
    all_missing = sum(
        1 for r in results
        for b in ("official", "benchmark", "community", "leading")
        if r.get("metadata", {}).get("source_coverage", {}).get(b, {}).get("status") == "missing"
    )
    total_buckets = len(results) * 4
    print(f"  {'-'*76}")
    print(f"  {'TOTAL':<20}  covered={all_covered}/{total_buckets}  missing={all_missing}/{total_buckets}")
    print()


def print_gaps_detail(results: list[dict]):
    """Print evidence gap details for competitors with missing slots."""
    for entry in results:
        gaps = entry.get("metadata", {}).get("evidence_gaps", [])
        if not gaps:
            continue
        company = entry.get("company", "?")
        print(f"  [{company}] evidence gaps ({len(gaps)}):")
        for gap in gaps:
            if isinstance(gap, dict):
                print(f"    - {gap.get('slot','?')}: {gap.get('rationale','')} [query: {gap.get('query','')}]")
    print()


async def benchmark_track(track: str, use_search: bool, session: ClientSession) -> list[dict]:
    """Run the full source-intake pipeline for one track and return results."""
    print(f"\n  Benchmarking track: {track}")
    competitors = _track_competitors(track)
    if not competitors:
        print(f"  No competitors found for track '{track}'")
        return []

    entries_with_coverage: list[dict] = []
    for comp in competitors:
        company = comp.get("company", "?")
        print(f"  Processing: {company}")

        # 基于现有缓存计算初始覆盖率
        cov = source_coverage_for_competitor(comp)
        entry = {**comp, "metadata": {**comp.get("metadata", {}), "source_coverage": cov}}

        if use_search:
            try:
                search_sources, msg = await build_source_candidates_with_search(
                    session, company, track, count=4
                )
                # 将搜索结果合并到对应来源桶
                for src in search_sources:
                    src.setdefault("source_group", "direct")
                    src.setdefault("source_status", "需抓取验证")
                    if src.get("candidate_only"):
                        existing = entry.get("metadata", {}).get("candidate_sources", [])
                        entry.setdefault("metadata", {})["candidate_sources"] = list(existing) + [src]
                        continue
                    bucket = "official_sources"
                    dim = src.get("threat_dimension", "")
                    slot = src.get("evidence_slot", "")
                    if "community" in slot or "community" in dim:
                        bucket = "community_sources"
                    elif "benchmark" in slot or "benchmark" in dim:
                        bucket = "benchmark_sources"
                    elif "leading" in slot or "github" in slot:
                        bucket = "leading_sources"
                    entry.setdefault(bucket, []).append(src)
                print(f"    Search: {msg}")
            except Exception as exc:
                print(f"    Search failed: {exc}")

        entries_with_coverage.append(entry)

    # 为所有来源补全正文
    print(f"  Hydrating sources...")
    hydrated = await hydrate_sources_for_analysis(entries_with_coverage, track)

    # 附加结构化证据缺口
    for entry in hydrated:
        company = entry.get("company", "")
        comp_for_plan = {"company": company, **entry}
        plan = build_evidence_acquisition_plan(comp_for_plan, track)
        entry.setdefault("metadata", {})["evidence_acquisition_plan"] = {
            "needed_slots": plan.get("needed_slots", []),
            "required_source_types": plan.get("required_source_types", []),
            "queries": plan.get("queries", []),
        }
        entry["metadata"]["evidence_gaps"] = [
            {
                "slot": q.get("slot", "") if isinstance(q, dict) else "",
                "dimension": q.get("dimension", "") if isinstance(q, dict) else "",
                "query": q.get("query", "") if isinstance(q, dict) else "",
                "rationale": (
                    plan.get("slot_rationale", {}).get(q.get("slot", ""), "")
                    if isinstance(q, dict)
                    else ""
                ),
            }
            for q in plan.get("queries", [])
        ]

        # 正文补全后重新计算覆盖率
        entry["metadata"]["source_coverage"] = source_coverage_for_competitor(entry)

    return hydrated


async def main():
    parser = argparse.ArgumentParser(description="Benchmark collection agent link discovery")
    parser.add_argument(
        "--tracks", nargs="+", default=["ai-coding"],
        help="Track directories under data/competitor-data/ to benchmark"
    )
    parser.add_argument(
        "--search", action="store_true",
        help="Enable live search API calls (requires BOCHA_SEARCH_API_KEY)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Custom output path for the benchmark JSON report"
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = Path(args.output) if args.output else BENCHMARK_DIR / f"{timestamp}.json"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Benchmark: Collection Agent Link Discovery")
    print(f"  Tracks: {args.tracks}")
    print(f"  Search: {'enabled' if args.search else 'offline'}")
    print(f"  Output: {output_path}")

    timeout = ClientTimeout(total=60)
    all_results: dict[str, list[dict]] = {}

    async with ClientSession(timeout=timeout) as session:
        for track in args.tracks:
            results = await benchmark_track(track, args.search, session)
            all_results[track] = results
            print_coverage_table(track, results)
            print_gaps_detail(results)

    report = {
        "benchmark": "collection-agent-link-discovery",
        "timestamp": timestamp,
        "search_enabled": args.search,
        "tracks": args.tracks,
        "results": {
            track: [
                {
                    "company": r.get("company", "?"),
                    "source_coverage": r.get("metadata", {}).get("source_coverage", {}),
                    "evidence_gaps": r.get("metadata", {}).get("evidence_gaps", []),
                    "evidence_acquisition_plan": r.get("metadata", {}).get("evidence_acquisition_plan", {}),
                    "evidence_relevance": r.get("metadata", {}).get("evidence_relevance", {}),
                    "strong_source_count": sum(
                        1
                        for b in SOURCE_BUCKETS
                        for s in (r.get(b, []) or [])
                        if isinstance(s, dict) and not s.get("candidate_only") and s.get("fetch_method")
                    ),
                    "total_fetched": sum(
                        1
                        for b in SOURCE_BUCKETS
                        for s in (r.get(b, []) or [])
                        if isinstance(s, dict) and s.get("fetch_method")
                    ),
                }
                for r in results
            ]
            for track, results in all_results.items()
        },
    }

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
