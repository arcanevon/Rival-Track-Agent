"""证据采集批次、稳定身份、返工写回与采集追踪。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Iterable

from langchain_core.messages import ToolMessage

from .constants import SOURCE_BUCKETS, canonicalize_url
from .enrich import enrich_competitor_inputs_with_search
from .evidence_relevance import classify_actual_source_type, evaluate_evidence
from .hydrate import hydrate_sources_for_analysis
from .quality import assess_source_quality, source_coverage_for_competitor


@dataclass(frozen=True)
class AcquisitionBudget:
    """单次分析的统一采集预算。"""

    max_competitor_concurrency: int = 2
    max_search_calls: int = 20
    max_fetch_attempts_per_competitor: int = 12
    max_browser_attempts_per_competitor: int = 2
    max_accepted_sources_per_competitor: int = 12
    wall_clock_seconds: float = 120.0


def evidence_fingerprint(source: dict) -> str:
    """以规范 URL 为主、内容为辅，生成跨阶段稳定的证据指纹。"""
    url = canonicalize_url(str(source.get("evidence_url") or source.get("url") or ""))
    if url:
        return url
    text = str(source.get("scraped_text") or source.get("text") or source.get("label") or "").strip()
    return text[:1000]


def ensure_evidence_identity(source: dict) -> dict:
    """复制来源并补充稳定 evidence_id，不改变调用方对象。"""
    row = dict(source)
    fingerprint = evidence_fingerprint(row)
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    row.setdefault("evidence_id", f"ev_{digest}")
    row["evidence_grade"] = evidence_grade(row)
    return row


def evidence_grade(source: dict) -> str:
    """严格区分搜索线索、可验证元数据和可引用正文。"""
    if source.get("candidate_only") or source.get("is_search_entry"):
        return "candidate_lead"
    text = str(source.get("scraped_text") or source.get("text") or "").strip()
    method = str(source.get("fetch_method") or "")
    if len(text) >= 120 and method not in {"search", "search_entry", "search_snippet"}:
        return "citable_content"
    if source.get("title") and (source.get("published_at") or source.get("author")):
        return "verifiable_metadata"
    return "candidate_lead"


def stamp_evidence_identities(competitor: dict) -> dict:
    """为竞品所有来源桶补齐身份，并跨桶去除重复 URL。"""
    item = dict(competitor)
    seen: set[str] = set()
    for bucket in SOURCE_BUCKETS:
        rows: list[dict] = []
        for source in item.get(bucket, []) or []:
            if not isinstance(source, dict):
                continue
            row = ensure_evidence_identity(source)
            fingerprint = evidence_fingerprint(row)
            if fingerprint and fingerprint in seen:
                continue
            if fingerprint:
                seen.add(fingerprint)
            rows.append(row)
        item[bucket] = rows
    return item


def _trace(stage: str, *, competitor: str = "", outcome: str = "ok",
           started: float | None = None, **details: object) -> dict[str, object]:
    return {
        "stage": stage,
        "competitor": competitor,
        "outcome": outcome,
        "latency_ms": round((time.monotonic() - started) * 1000) if started is not None else 0,
        **details,
    }


async def acquire_competitor_inputs(
    user_data: list[dict],
    track: str,
    budget: AcquisitionBudget | None = None,
) -> tuple[list[dict], list[dict[str, object]]]:
    """在后台以有界并发完成搜索增强和跨竞品正文读取。"""
    budget = budget or AcquisitionBudget()
    semaphore = asyncio.Semaphore(max(1, budget.max_competitor_concurrency))
    traces: list[dict[str, object]] = []
    batch_started = time.monotonic()

    async def acquire_one(index: int, raw: dict) -> tuple[int, dict]:
        company = str(raw.get("company") or raw.get("name") or f"竞品{index + 1}")
        started = time.monotonic()
        async with semaphore:
            try:
                # 总预算按竞品公平分配，并至少覆盖官网、社区、权威媒体三类来源。
                per_competitor_search_budget = max(
                    3, min(8, budget.max_search_calls // max(1, len(user_data)))
                )
                enriched = await enrich_competitor_inputs_with_search(
                    [raw], track,
                    max_search_queries_per_competitor=per_competitor_search_budget,
                )
                traces.append(_trace("search_complete", competitor=company, started=started))
                hydrated = await hydrate_sources_for_analysis(
                    enriched or [raw], track,
                    max_fetch_attempts_per_competitor=budget.max_fetch_attempts_per_competitor,
                    max_browser_attempts_per_competitor=budget.max_browser_attempts_per_competitor,
                    max_accepted_sources_per_competitor=budget.max_accepted_sources_per_competitor,
                )
                item = stamp_evidence_identities((hydrated or enriched or [raw])[0])
                outcome = "accepted"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                item = stamp_evidence_identities(raw)
                outcome = "partial"
                traces.append(_trace("competitor_failed", competitor=company, outcome=outcome,
                                     started=started, error=type(exc).__name__))
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            event = _trace("competitor_complete", competitor=company, outcome=outcome, started=started)
            traces.append(event)
            item["metadata"] = {
                **metadata,
                "acquisition_budget": asdict(budget),
                "acquisition_trace": [e for e in traces if e.get("competitor") == company],
                "source_coverage": source_coverage_for_competitor(item),
            }
            return index, item

    tasks = [asyncio.create_task(acquire_one(index, raw)) for index, raw in enumerate(user_data)]
    done, pending = await asyncio.wait(tasks, timeout=max(1.0, budget.wall_clock_seconds))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        traces.append(_trace("batch_timeout", outcome="partial", pending=len(pending)))
    results: dict[int, dict] = {}
    for task in done:
        try:
            index, item = task.result()
            results[index] = item
        except Exception as exc:
            traces.append(_trace("task_failed", outcome="partial", error=type(exc).__name__))
    for index, raw in enumerate(user_data):
        results.setdefault(index, stamp_evidence_identities(raw))
    traces.append(_trace("batch_complete", started=batch_started,
                         outcome="partial" if pending else "accepted",
                         competitors=len(user_data)))
    return [results[index] for index in range(len(user_data))], traces


def _bucket_for_result(result: dict, tool: str, company: str) -> str:
    if tool == "community_search" or result.get("social_platform"):
        return "community_sources"
    requested = result.get("requested_source_types") or []
    if isinstance(requested, str):
        requested = [requested]
    preferred = next((value for value in requested if value in {"official", "benchmark", "community", "leading"}), "")
    actual = preferred or classify_actual_source_type(result, company)
    return f"{actual}_sources" if f"{actual}_sources" in SOURCE_BUCKETS else "benchmark_sources"


def _refresh_competitor_metadata(item: dict) -> None:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    item["metadata"] = {**metadata, "source_coverage": source_coverage_for_competitor(item)}


def merge_tool_observations(
    cache_data: dict,
    messages: Iterable[ToolMessage],
    processed_tool_call_ids: list[str],
) -> tuple[dict, list[str], list[dict[str, object]]]:
    """把未处理 ToolMessage 原子合并到证据缓存，并返回可审计 Trace。"""
    cache = copy.deepcopy(cache_data)
    processed = list(processed_tool_call_ids)
    processed_set = set(processed)
    traces: list[dict[str, object]] = []
    for message in messages:
        call_id = str(getattr(message, "tool_call_id", "") or "")
        if not call_id or call_id in processed_set:
            continue
        started = time.monotonic()
        try:
            payload = json.loads(str(message.content))
        except (TypeError, json.JSONDecodeError):
            processed.append(call_id)
            processed_set.add(call_id)
            traces.append(_trace("tool_merge", outcome="invalid", started=started, tool_call_id=call_id))
            continue
        company = str(payload.get("competitor") or "").strip()
        tool = str(payload.get("tool") or "")
        item = cache.setdefault(company, {"company": company}) if company else None
        if not isinstance(item, dict):
            continue
        for bucket in SOURCE_BUCKETS:
            item.setdefault(bucket, [])
        accepted = 0
        rows = payload.get("results") if isinstance(payload.get("results"), list) else []
        if tool == "reader" and payload.get("url"):
            rows = [{
                "url": payload.get("url"), "evidence_url": payload.get("url"),
                "label": payload.get("title") or company, "scraped_text": payload.get("text", ""),
                "fetch_method": payload.get("fetch_method", ""),
                "candidate_only": payload.get("status") != "ok",
                "source_quality": payload.get("source_quality") or {},
            }]
        existing = {
            evidence_fingerprint(source)
            for bucket in SOURCE_BUCKETS for source in item.get(bucket, []) or []
            if isinstance(source, dict)
        }
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            row = ensure_evidence_identity({
                **raw,
                "label": raw.get("label") or raw.get("title") or company,
                "evidence_url": raw.get("evidence_url") or raw.get("url") or "",
                "evidence_status": "strong_text" if not raw.get("candidate_only") and raw.get("scraped_text") else "candidate_text",
            })
            fingerprint = evidence_fingerprint(row)
            if not fingerprint:
                continue
            if fingerprint in existing:
                if row.get("scraped_text"):
                    for existing_bucket in SOURCE_BUCKETS:
                        for index, current in enumerate(item.get(existing_bucket, []) or []):
                            if isinstance(current, dict) and evidence_fingerprint(current) == fingerprint:
                                upgraded = ensure_evidence_identity({**current, **row})
                                verdict = evaluate_evidence(
                                    upgraded, company, str(upgraded.get("evidence_slot") or "")
                                )
                                upgraded["evidence_verdict"] = asdict(verdict)
                                upgraded["source_quality"] = assess_source_quality(upgraded)
                                item[existing_bucket][index] = upgraded
                                accepted += int(not upgraded.get("candidate_only"))
                                break
                continue
            bucket = _bucket_for_result(row, tool, company)
            verdict = evaluate_evidence(row, company, str(row.get("evidence_slot") or ""))
            row["evidence_verdict"] = asdict(verdict)
            row["source_quality"] = row.get("source_quality") or assess_source_quality(row)
            item[bucket].append(row)
            existing.add(fingerprint)
            accepted += int(not row.get("candidate_only"))
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        query = str(payload.get("query") or "")
        query_fingerprints = list(metadata.get("query_fingerprints") or [])
        if query:
            fingerprint = f"{company}|{query}"
            if fingerprint not in query_fingerprints:
                query_fingerprints.append(fingerprint)
        item["metadata"] = {**metadata, "query_fingerprints": query_fingerprints}
        _refresh_competitor_metadata(item)
        processed.append(call_id)
        processed_set.add(call_id)
        traces.append(_trace("tool_merge", competitor=company,
                             outcome="accepted" if accepted else "candidate",
                             started=started, tool_call_id=call_id,
                             accepted_sources=accepted, result_count=len(rows)))
    return cache, processed, traces


__all__ = [
    "AcquisitionBudget", "acquire_competitor_inputs", "ensure_evidence_identity",
    "evidence_fingerprint", "evidence_grade", "merge_tool_observations", "stamp_evidence_identities",
]
