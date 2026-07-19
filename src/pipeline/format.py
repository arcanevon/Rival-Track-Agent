"""Data formatting for agent prompts in the pipeline DAG.

Formats cache data into prompt-friendly text, builds shared source digests,
and serialises threat target objects. Used by Collector and both Analysts.
"""

import json
import logging

from src.intake.quality import assess_source_quality, source_coverage_for_competitor

logger = logging.getLogger(__name__)

# 格式化时遍历 O/B/C/L 四类来源所用的“缓存键—短标签”映射
_SOURCE_TIER_PAIRS = (
    ("official_sources", "official"),
    ("benchmark_sources", "benchmark"),
    ("community_sources", "community"),
    ("leading_sources", "leading"),
)


def _default_threat_target(track: str) -> dict[str, object]:
    return {
        "name": "我方产品",
        "positioning": f"{track} 赛道中的被分析主体",
        "target_users": "",
        "core_capabilities": "",
        "competitive_concern": "识别竞品对我方产品的威胁",
    }


def _format_threat_target(threat_target: dict[str, object] | None, track: str = "") -> str:
    """Serialise the threat target dict into an indented JSON string."""
    target = threat_target or _default_threat_target(track)
    return json.dumps(target, ensure_ascii=False, indent=2)


def _extract_shared_source_digest(cache_data: dict[str, dict]) -> str:
    """Build a readable source digest shared by both analysts."""
    parts: list[str] = []
    for company, data in cache_data.items():
        parts.append(f"\n### {company}")
        coverage = data.get("metadata", {}).get("source_coverage") if isinstance(data.get("metadata"), dict) else None
        if not isinstance(coverage, dict):
            coverage = source_coverage_for_competitor(data)
        plan = data.get("metadata", {}).get("evidence_acquisition_plan") if isinstance(data.get("metadata"), dict) else None
        parts.append(f"coverage: {coverage}")
        if isinstance(plan, dict):
            parts.append(
                "evidence_acquisition_plan: "
                f"needed_slots={plan.get('needed_slots', [])}; "
                f"required_source_types={plan.get('required_source_types', [])}; "
                f"current_strong_sources={plan.get('current_strong_sources', 0)}"
            )
        for source_type, label in _SOURCE_TIER_PAIRS:
            sources = data.get(source_type, [])
            if sources:
                parts.append(f"- {label}:")
            for src in sources:
                if not isinstance(src, dict):
                    continue
                quality = src.get("source_quality") if isinstance(src, dict) else None
                if not isinstance(quality, dict):
                    quality = assess_source_quality(src)
                parts.append(
                    f"  - [{src.get('label', 'source')}]({src.get('url', '')}): "
                    f"quality_score={quality.get('score', '')}; "
                    f"usable_for_scoring={quality.get('usable_for_scoring', '')}; "
                    f"status={src.get('evidence_status') or src.get('source_status') or quality.get('status', '')}; "
                    f"{src.get('scraped_text', '')[:800]}"
                )
    return "\n".join(parts) if parts else "(no readable source data)"


def _format_cache_for_collector(cache_data: dict[str, dict], max_chars: int = 40000) -> str:
    """Format cache data without starving later competitors when prompts are large."""
    if not cache_data:
        return "{}"
    per_competitor_budget = max(1800, max_chars // max(len(cache_data), 1))
    blocks: list[str] = []
    for company, data in cache_data.items():
        block: list[str] = [
            f"## {company}",
            f"track: {data.get('track', '')}",
        ]
        coverage = data.get("metadata", {}).get("source_coverage") if isinstance(data.get("metadata"), dict) else None
        if not isinstance(coverage, dict):
            coverage = source_coverage_for_competitor(data)
        block.append(f"source_coverage={coverage}")
        plan = data.get("metadata", {}).get("evidence_acquisition_plan") if isinstance(data.get("metadata"), dict) else None
        if isinstance(plan, dict):
            block.append(
                "evidence_acquisition_plan="
                f"needed_slots={plan.get('needed_slots', [])}; "
                f"required_source_types={plan.get('required_source_types', [])}; "
                f"minimum_strong_sources={plan.get('minimum_strong_sources', 2)}; "
                f"current_strong_sources={plan.get('current_strong_sources', 0)}"
            )
        used = sum(len(part) for part in block)
        for source_type, label in _SOURCE_TIER_PAIRS:
            sources = data.get(source_type, [])
            if not sources:
                continue
            block.append(f"{label}:")
            used += len(label) + 1
            for src in sources[:3]:
                if not isinstance(src, dict):
                    continue
                remaining = per_competitor_budget - used
                if remaining <= 120:
                    break
                text = str(src.get("scraped_text", "") or src.get("note", "") or "")
                text = text.replace("\n", " ").strip()
                text = text[: min(650, max(120, remaining))]
                quality = src.get("source_quality")
                if not isinstance(quality, dict):
                    quality = assess_source_quality(src)
                line = (
                    f"- label={src.get('label', 'source')}; "
                    f"url={src.get('url', '')}; "
                    f"status={src.get('evidence_status', '')}; "
                    f"candidate_only={src.get('candidate_only', False)}; "
                    f"quality_score={quality.get('score', '')}; "
                    f"usable_for_scoring={quality.get('usable_for_scoring', '')}; "
                    f"text={text}"
                )
                block.append(line)
                used += len(line)
        blocks.append("\n".join(block))
    formatted = "\n\n".join(blocks)
    return formatted[:max_chars]
