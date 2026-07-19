"""Evidence acquisition planning.

Analyses per-competitor source coverage and determines which evidence slots
still need concrete sources, producing query plans that downstream agents
can execute.
"""

import logging

from src.config import load_query_templates_config
from src.config.router import resolve_industry_keyword

from .constants import THREAT_EVIDENCE_SLOTS, _is_developer_tool_context
from .quality import _covered_source_types, _strong_source_count, source_coverage_for_competitor


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 证据槽位配置加载
# ---------------------------------------------------------------------------

def _get_evidence_slot_configs(industry_type: str = "") -> dict[str, dict]:
    """Load evidence slot configs from ``query_templates.yaml``, falling back to built-in ``THREAT_EVIDENCE_SLOTS``."""
    cfg = (
        load_query_templates_config(industry_type)
        if industry_type
        else load_query_templates_config()
    )
    slots_cfg = cfg.get("slots", {}) if cfg else {}
    if slots_cfg:
        result: dict[str, dict] = {}
        for slot_key, slot_cfg in slots_cfg.items():
            if not isinstance(slot_cfg, dict):
                continue
            source_types = slot_cfg.get("source_types", [])
            if isinstance(source_types, str):
                source_types = [source_types]
            result[slot_key] = {
                "dimension": slot_cfg.get("dimension", ""),
                "source_types": source_types,
                "template": slot_cfg.get("template", ""),
                "exclude_terms": slot_cfg.get("exclude_terms", []),
                "freshness": slot_cfg.get("freshness", "noLimit"),
            }
        return result
    return THREAT_EVIDENCE_SLOTS


# ---------------------------------------------------------------------------
# 单个竞品的证据计划
# ---------------------------------------------------------------------------

def build_evidence_acquisition_plan(
    competitor: dict,
    track: str = "",
    minimum_strong_sources: int = 2,
) -> dict[str, object]:
    """Plan missing evidence tasks before Collector and Analysts reason over sources."""
    company = str(competitor.get("company") or competitor.get("name") or "Unknown").strip() or "Unknown"
    coverage = competitor.get("metadata", {}).get("source_coverage") if isinstance(competitor.get("metadata"), dict) else None
    if not isinstance(coverage, dict):
        coverage = source_coverage_for_competitor(competitor)

    covered = _covered_source_types(coverage)
    strong_count = _strong_source_count(coverage)
    needed_slots: list[str] = []
    required_source_types: set[str] = set()
    slot_rationale: dict[str, str] = {}

    industry_type = resolve_industry_keyword(track, company) or "software_saas"
    slot_configs = _get_evidence_slot_configs(industry_type)

    for slot, config in slot_configs.items():
        source_types = set(config["source_types"])
        if source_types.isdisjoint(covered):
            needed_slots.append(slot)
            required_source_types.update(source_types)
            dimension = config["dimension"]
            missing = ", ".join(sorted(source_types - covered)) or ", ".join(sorted(source_types))
            slot_rationale[slot] = f"{dimension} needs stronger {missing} evidence"

    if _is_developer_tool_context(company, track) and "leading" not in covered:
        if "github_release_velocity" not in needed_slots:
            needed_slots.append("github_release_velocity")
            required_source_types.add("leading")
            slot_rationale["github_release_velocity"] = "strategic_expansion needs concrete GitHub release or issue evidence"

    queries = []
    for slot in needed_slots:
        config = slot_configs.get(slot, {})
        tmpl = config.get("template") or " ".join(config.get("query_terms", []))
        exclude_terms = config.get("exclude_terms", [])
        query_str = tmpl.format(name=company, track=track, repo_owner="", repo_name="")
        if exclude_terms:
            exclude_clause = " ".join(f"-{t}" for t in exclude_terms)
            query_str = f"{query_str} {exclude_clause}"
        query_str = query_str.strip()
        queries.append({
            "slot": slot,
            "dimension": config.get("dimension", ""),
            "source_types": list(config.get("source_types", [])),
            "freshness": config.get("freshness", "noLimit"),
            "query": query_str,
            "avoid": "search entry pages; resolve to concrete readable pages before scoring",
        })

    return {
        "competitor": company,
        "needed_slots": needed_slots,
        "queries": queries,
        "required_source_types": sorted(required_source_types),
        "minimum_strong_sources": minimum_strong_sources,
        "current_strong_sources": strong_count,
        "coverage": coverage,
        "slot_rationale": slot_rationale,
    }


# ---------------------------------------------------------------------------
# 批量证据计划
# ---------------------------------------------------------------------------

def build_evidence_acquisition_plans(
    cache_data: dict[str, dict],
    track: str = "",
    minimum_strong_sources: int = 2,
) -> dict[str, dict[str, object]]:
    """Build per-competitor evidence acquisition plans for the current cache."""
    plans: dict[str, dict[str, object]] = {}
    for company, data in cache_data.items():
        if not isinstance(data, dict):
            continue
        competitor = {"company": company, **data}
        plans[company] = build_evidence_acquisition_plan(
            competitor,
            track or str(data.get("track", "")),
            minimum_strong_sources,
        )
    return plans


# ---------------------------------------------------------------------------
# 结构化证据缺口
# ---------------------------------------------------------------------------

def build_evidence_gaps(cache_data: dict[str, dict], track: str = "") -> list[dict[str, object]]:
    """Return structured evidence gaps that downstream agents can turn into actions."""
    gaps: list[dict[str, object]] = []
    for company, plan in build_evidence_acquisition_plans(cache_data, track).items():
        for query in plan.get("queries", []):
            if not isinstance(query, dict):
                continue
            slot = query.get("slot", "")
            gaps.append({
                "competitor": company,
                "slot": slot,
                "dimension": query.get("dimension", ""),
                "query": query.get("query", ""),
                # 每个缺口只携带本条查询所需的来源类型，避免官网查询被误路由到社区工具。
                "source_types": list(query.get("source_types", [])),
                "required_source_types": list(query.get("source_types", [])),
                "freshness": query.get("freshness", "noLimit"),
                "minimum_strong_sources": plan.get("minimum_strong_sources", 2),
                "current_strong_sources": plan.get("current_strong_sources", 0),
                "rationale": plan.get("slot_rationale", {}).get(slot, ""),
            })
    return gaps
