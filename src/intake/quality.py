"""Source quality assessment.

Scores individual sources, computes per-competitor coverage, and provides
context-building helpers for prompt assembly.
"""

import logging
import re
from urllib.parse import urlparse

from .constants import (
    SOURCE_BUCKETS,
    WEAK_EVIDENCE_STATUSES,
    is_candidate_source_only,
    is_search_entry_url,
    source_status_label,
    source_ui_group,
)
from .evidence_relevance import is_low_value_page


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 单条来源质量
# ---------------------------------------------------------------------------

def assess_source_quality(source: dict) -> dict:
    """Score one source and explain whether it is usable for threat scoring."""
    url = str(source.get("evidence_url") or source.get("url") or "").strip()
    host = urlparse(url).netloc.lower()
    text = str(source.get("scraped_text") or source.get("text") or "").strip()
    verdict = source.get("evidence_verdict")
    if isinstance(verdict, dict):
        accepted = verdict.get("accepted") is True
        return {
            "status": "strong_text" if accepted else "rejected_irrelevant",
            "score": int(round(float(verdict.get("relevance_score", 0)))),
            "reasons": [str(verdict.get("reject_reason") or "通过抓取后相关性验收")],
            "usable_for_scoring": accepted,
            "entity_relevance": verdict.get("entity_relevance", 0),
            "claim_alignment": verdict.get("claim_alignment", 0),
            "content_quality": verdict.get("content_quality", 0),
            "source_authority": verdict.get("source_authority", 0),
            "actual_source_type": verdict.get("actual_source_type", "unknown"),
        }
    group = source.get("source_group") or source_ui_group(source)
    status = source.get("evidence_status") or source.get("source_status") or ""
    score = 50
    reasons: list[str] = []

    if is_low_value_page(url, str(source.get("label") or source.get("title") or ""), text):
        return {
            "status": "rejected_irrelevant",
            "score": 0,
            "reasons": ["下载、安装或攻略聚合页"],
            "usable_for_scoring": False,
        }

    if not url:
        score -= 35
        reasons.append("missing url")
    if is_search_entry_url(url):
        score -= 45
        reasons.append("search entry only")
    if source.get("candidate_only") or status in WEAK_EVIDENCE_STATUSES:
        score -= 35
        reasons.append("candidate or weak evidence status")
    if source.get("direct_evidence") is True:
        score += 15
        reasons.append("direct evidence page")
    if source.get("authority") == "high":
        score += 15
        reasons.append("high authority hint")
    if group == "leading":
        score += 5
        reasons.append("leading indicator source")
    if "baike.baidu.com" in host:
        score -= 25
        reasons.append("background encyclopedia")
    if any(domain in host for domain in ("xlhs.com", "pc6.com", "downza.cn", "onlinedown.net", "mydown.com")):
        score -= 50
        reasons.append("low quality download domain")
    if len(text) >= 200:
        score += 20
        reasons.append("readable text over 200 chars")
    elif text:
        score += 5
        reasons.append("short readable text")

    score = max(0, min(100, score))
    if score >= 70:
        quality_status = "strong_text"
    elif score >= 40:
        quality_status = "candidate_text"
    else:
        quality_status = "weak_or_missing"
    return {
        "status": quality_status,
        "score": score,
        "reasons": reasons,
        "usable_for_scoring": score >= 70,
    }


def source_text_matches_competitor(src: dict, company: str) -> bool:
    """Guard against relevant-looking titles whose fetched body is an unrelated page."""
    text = str(src.get("scraped_text", "") or src.get("text", "")).lower()
    url = str(src.get("url", "") or src.get("evidence_url", "")).lower()
    company_text = str(company or "").lower()
    tokens = [
        token
        for token in re.split(r"[\s/\\,，、|·:：()（）\[\]【】\-]+", company_text)
        if len(token) >= 2
    ]
    if company_text and company_text in text:
        return True
    return any(token in text or token in url for token in tokens)


# ---------------------------------------------------------------------------
# 候选来源分组
# ---------------------------------------------------------------------------

def split_candidate_sources(sources: list[dict], company: str = "") -> tuple[list[dict], list[dict]]:
    """Partition *sources* into evidence-ready and candidate-only lists."""
    evidence_sources: list[dict] = []
    candidate_sources: list[dict] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source.setdefault("source_group", source_ui_group(source))
        source.setdefault("source_status", source_status_label(source))
        source["source_quality"] = assess_source_quality(source)
        has_text = bool(str(source.get("scraped_text", "") or source.get("text", "")).strip())
        if is_candidate_source_only(source) or (company and has_text and not source_text_matches_competitor(source, company)):
            candidate_sources.append(source)
        else:
            evidence_sources.append(source)
    return evidence_sources, candidate_sources


# ---------------------------------------------------------------------------
# 来源覆盖率
# ---------------------------------------------------------------------------

def source_coverage_for_competitor(competitor: dict) -> dict[str, dict[str, object]]:
    """Compute O/B/C/L coverage dict for a single competitor."""
    coverage: dict[str, dict[str, object]] = {}
    for bucket in SOURCE_BUCKETS:
        sources = [
            source for source in competitor.get(bucket, []) or []
            if isinstance(source, dict)
        ]
        strong = 0
        candidate = 0
        for source in sources:
            quality = source.get("source_quality")
            if not isinstance(quality, dict):
                quality = assess_source_quality(source)
            if quality.get("usable_for_scoring"):
                strong += 1
            else:
                candidate += 1
        if strong:
            status = "covered"
        elif candidate:
            status = "candidate_only"
        else:
            status = "missing"
        coverage[bucket.removesuffix("_sources")] = {
            "status": status,
            "strong_count": strong,
            "candidate_count": candidate,
            "total_count": len(sources),
        }
    return coverage


def _covered_source_types(coverage: dict[str, dict[str, object]]) -> set[str]:
    """Return the set of source-type keys that have at least one strong source."""
    return {
        source_type
        for source_type, details in coverage.items()
        if isinstance(details, dict) and details.get("status") == "covered"
    }


def _strong_source_count(coverage: dict[str, dict[str, object]]) -> int:
    """Return the total number of strong sources across all buckets."""
    return sum(
        int(details.get("strong_count", 0))
        for details in coverage.values()
        if isinstance(details, dict)
    )


# ---------------------------------------------------------------------------
# 正文检查
# ---------------------------------------------------------------------------

def source_has_content(src: dict, company: str) -> bool:
    """Return whether a source has enough concrete text to count as evidence."""
    quality = src.get("source_quality")
    text = str(src.get("scraped_text", "")).strip()
    url = str(src.get("url", "")).strip()
    if src.get("candidate_only") or src.get("evidence_status") in WEAK_EVIDENCE_STATUSES:
        return False
    if src.get("fetch_method") in {"search_entry", "candidate_search"}:
        return False
    if "自动发现候选来源" in text or "候选强证据线索" in text:
        return False
    if text and not source_text_matches_competitor(src, company):
        return False
    if isinstance(quality, dict) and quality.get("usable_for_scoring") is True:
        return True
    if url and len(text) >= 24:
        return True
    return len(text) >= 80 and text != company


# ---------------------------------------------------------------------------
# 提示词上下文构建
# ---------------------------------------------------------------------------

def build_source_quality_context(cache_data: dict[str, dict], max_chars: int = 8000) -> str:
    """Build a compact source quality and O/B/C/L coverage summary for prompts."""
    if not cache_data:
        return "(no source quality context available)"

    lines: list[str] = []
    for company, data in cache_data.items():
        if not isinstance(data, dict):
            continue
        coverage = data.get("metadata", {}).get("source_coverage") if isinstance(data.get("metadata"), dict) else None
        if not isinstance(coverage, dict):
            coverage = source_coverage_for_competitor(data)
        lines.append(f"## {company}")
        lines.append(f"coverage: {coverage}")
        plan = data.get("metadata", {}).get("evidence_acquisition_plan") if isinstance(data.get("metadata"), dict) else None
        if isinstance(plan, dict):
            lines.append(
                "evidence_acquisition_plan: "
                f"needed_slots={plan.get('needed_slots', [])}; "
                f"required_source_types={plan.get('required_source_types', [])}; "
                f"minimum_strong_sources={plan.get('minimum_strong_sources', 2)}; "
                f"current_strong_sources={plan.get('current_strong_sources', 0)}"
            )
        candidate_sources = data.get("metadata", {}).get("candidate_sources") if isinstance(data.get("metadata"), dict) else None
        if isinstance(candidate_sources, list) and candidate_sources:
            lines.append("candidate_sources_only (clues for retry; never use for scoring):")
            prioritized = sorted(
                (src for src in candidate_sources if isinstance(src, dict)),
                key=lambda src: bool(src.get("degraded_summary") or src.get("search_snippet")),
                reverse=True,
            )
            for src in prioritized[:4]:
                summary = str(src.get("degraded_summary") or src.get("search_snippet") or "")[:240]
                lines.append(
                    f"  - label={src.get('label', 'candidate')}; "
                    f"status={src.get('evidence_status', 'candidate')}; "
                    f"summary={summary}; usable_for_scoring=False"
                )
        for bucket in SOURCE_BUCKETS:
            bucket_label = bucket.removesuffix("_sources")
            sources = [src for src in data.get(bucket, []) or [] if isinstance(src, dict)]
            if not sources:
                continue
            lines.append(f"- {bucket_label}:")
            for src in sources[:3]:
                quality = src.get("source_quality")
                if not isinstance(quality, dict):
                    quality = assess_source_quality(src)
                label = str(src.get("label", "source"))
                url = str(src.get("url", ""))
                status = str(src.get("evidence_status") or src.get("source_status") or quality.get("status", ""))
                usable = quality.get("usable_for_scoring")
                score = quality.get("score")
                reasons = ", ".join(str(reason) for reason in quality.get("reasons", [])[:3])
                lines.append(
                    f"  - {label}; status={status}; quality_score={score}; "
                    f"usable_for_scoring={usable}; reasons={reasons}; url={url}"
                )
        if len("\n".join(lines)) >= max_chars:
            break
    return "\n".join(lines)[:max_chars]
