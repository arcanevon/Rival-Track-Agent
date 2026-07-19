"""Evidence coverage and confidence logic for the pipeline DAG.

Ensures every competitor has visible source references in Collector output,
converts raw source dicts to EvidenceRef objects, and caps Collector
confidence when source coverage is still weak.
"""

import re
import logging

from src.models.output import EvidenceRef, AgentNodeOutput
from src.intake.quality import source_coverage_for_competitor
from src.intake.acquisition import ensure_evidence_identity

logger = logging.getLogger(__name__)


def _source_to_evidence_ref(company: str, source_type: str, src: dict) -> EvidenceRef | None:
    """Convert a raw source dict into a structured EvidenceRef."""
    if not isinstance(src, dict):
        return None
    src = ensure_evidence_identity(src)
    label = str(src.get("label", "") or "source").strip()
    text = str(src.get("scraped_text", "") or src.get("note", "") or "").strip()
    url = str(src.get("url", "") or "").strip()
    if not label and not text and not url:
        return None
    if src.get("candidate_only") or src.get("evidence_status") in {
        "candidate_search",
        "candidate_text",
        "missing_url",
        "fetch_failed",
        "fetch_budget_deferred",
    }:
        relevance = f"{company} candidate {source_type} source; requires stronger validation before scoring."
    else:
        relevance = f"{company} {source_type} source supporting competitor coverage."
    quote = text[:260] if text else f"{company} source link prepared for validation."
    return EvidenceRef(
        evidence_id=str(src.get("evidence_id", "")),
        source_url=url,
        source_label=f"{company} · {label}",
        quote=quote,
        relevance=relevance,
        source_tier=source_type,
    )


def normalize_output_evidence_ids(
    result: AgentNodeOutput,
    cache_data: dict[str, dict],
) -> AgentNodeOutput:
    """把 URL、标签和 O/B/C/L 别名统一为账本中的稳定 evidence_id。"""
    aliases: dict[str, str] = {}
    company_aliases: dict[str, dict[str, str]] = {}
    bucket_codes = {
        "official_sources": "O", "benchmark_sources": "B",
        "community_sources": "C", "leading_sources": "L",
    }
    for company, item in cache_data.items():
        if not isinstance(item, dict):
            continue
        local_aliases = company_aliases.setdefault(company, {})
        for bucket, code in bucket_codes.items():
            for index, raw in enumerate(item.get(bucket, []) or [], start=1):
                if not isinstance(raw, dict):
                    continue
                source = ensure_evidence_identity(raw)
                evidence_id = str(source.get("evidence_id") or "")
                for value in (evidence_id, source.get("url"), source.get("evidence_url"), source.get("label")):
                    if value:
                        aliases[str(value).strip()] = evidence_id
                local_aliases[f"{code}{index}"] = evidence_id
    for evidence in result.evidence:
        if not evidence.evidence_id:
            evidence.evidence_id = aliases.get(evidence.source_url, "") or aliases.get(evidence.source_label, "")
    for finding in result.method_findings:
        refs = finding.get("evidence_refs")
        if not isinstance(refs, list):
            continue
        normalized: list[str] = []
        finding_aliases = {**aliases, **company_aliases.get(str(finding.get("competitor") or ""), {})}
        for ref in refs:
            evidence_id = finding_aliases.get(str(ref).strip(), "")
            if evidence_id and evidence_id not in normalized:
                normalized.append(evidence_id)
        finding["evidence_refs"] = normalized
    return result


def _collector_confidence_cap(cache_data: dict[str, dict]) -> float:
    """Cap Collector confidence when source coverage is still weak."""
    if not cache_data:
        return 0.5
    strong_count = 0
    weak_buckets = 0
    for data in cache_data.values():
        if not isinstance(data, dict):
            continue
        coverage = data.get("metadata", {}).get("source_coverage") if isinstance(data.get("metadata"), dict) else None
        if not isinstance(coverage, dict):
            coverage = source_coverage_for_competitor(data)
        for details in coverage.values():
            if not isinstance(details, dict):
                continue
            strong_count += int(details.get("strong_count", 0))
            if details.get("status") in {"missing", "candidate_only"}:
                weak_buckets += 1
    if strong_count == 0:
        return 0.55
    if strong_count < len(cache_data) * 2:
        return 0.75
    if weak_buckets:
        return 0.85
    return 1.0


def _ensure_collector_evidence_coverage(
    result: AgentNodeOutput,
    competitors: list[str],
    cache_data: dict[str, dict],
) -> AgentNodeOutput:
    """Ensure every competitor has visible source refs even if the LLM omitted them."""
    existing_labels = "\n".join(e.source_label for e in result.evidence).lower()
    existing_urls = {e.source_url for e in result.evidence if e.source_url}
    added: list[EvidenceRef] = []

    def _fuzzy_cache_lookup(company: str) -> dict | None:
        """When exact key lookup fails, find cache data by token overlap."""
        if not company:
            return None
        company_tokens = set(re.split(r"[\s·\-|,，、。（）()\[\]【】/\\]+", company.lower()))
        company_tokens.discard("")
        if not company_tokens:
            return None
        best_key = None
        best_score = 0
        for key in cache_data:
            key_tokens = set(re.split(r"[\s·\-|,，、。（）()\[\]【】/\\]+", key.lower()))
            key_tokens.discard("")
            overlap = len(company_tokens & key_tokens)
            if overlap > best_score:
                best_score = overlap
                best_key = key
        return cache_data.get(best_key) if best_key and best_score > 0 else None

    for company in competitors:
        if company.lower() in existing_labels:
            continue
        data = cache_data.get(company)
        if not data:
            data = _fuzzy_cache_lookup(company) or {}
        for bucket, source_type in (
            ("official_sources", "official"),
            ("benchmark_sources", "benchmark"),
            ("community_sources", "community"),
            ("leading_sources", "leading"),
        ):
            for src in data.get(bucket, []) or []:
                evidence = _source_to_evidence_ref(company, source_type, src)
                if evidence is None:
                    continue
                if evidence.source_url and evidence.source_url in existing_urls:
                    continue
                added.append(evidence)
                if evidence.source_url:
                    existing_urls.add(evidence.source_url)
                if len([e for e in added if e.source_label.startswith(company + " ·")]) >= 2:
                    break
            if len([e for e in added if e.source_label.startswith(company + " ·")]) >= 2:
                break
    if added:
        result.evidence.extend(added)
        result.output_summary = (
            (result.output_summary or "")
            + "\n\n系统补齐：为 "
            + str(len(set(e.source_label.split(" · ")[0] for e in added)))
            + " 个竞品补充了可见来源引用，避免多竞品时后置竞品在对比页显示 0 数据源。"
        ).strip()
    return result
