"""统一证据采集、返工写回与追踪的回归测试。"""

import asyncio
import json
from pathlib import Path

from langchain_core.messages import ToolMessage

from src.intake.acquisition import (
    AcquisitionBudget,
    acquire_competitor_inputs,
    ensure_evidence_identity,
    merge_tool_observations,
)
from src.pipeline.dag import _plan_collector_tool_calls


def test_stable_evidence_id_is_deterministic() -> None:
    first = ensure_evidence_identity({"url": "https://example.com/a?utm_source=x", "label": "A"})
    second = ensure_evidence_identity({"url": "https://example.com/a", "label": "changed"})
    assert first["evidence_id"].startswith("ev_")
    assert first["evidence_id"] == second["evidence_id"]


def test_tool_observation_is_merged_into_cache_and_traced() -> None:
    cache = {"星穹铁道": {"company": "星穹铁道", "official_sources": [],
                              "benchmark_sources": [], "community_sources": [],
                              "leading_sources": [], "metadata": {}}}
    message = ToolMessage(
        tool_call_id="search-0-0",
        content=json.dumps({
            "status": "ok", "tool": "community_search", "competitor": "星穹铁道",
            "query": "星穹铁道 用户评价", "results": [{
                "url": "https://www.zhihu.com/question/1",
                "title": "玩家如何评价星穹铁道",
                "scraped_text": "星穹铁道玩家讨论游戏剧情、战斗和养成体验。" * 10,
                "candidate_only": False, "fetch_method": "html_parser",
            }],
        }, ensure_ascii=False),
    )
    merged, processed, trace = merge_tool_observations(cache, [message], [])
    sources = merged["星穹铁道"]["community_sources"]
    assert len(sources) == 1
    assert sources[0]["evidence_id"].startswith("ev_")
    assert processed == ["search-0-0"]
    assert trace[-1]["outcome"] == "accepted"
    assert merged["星穹铁道"]["metadata"]["source_coverage"]["community"]["total_count"] == 1


def test_planner_uses_structured_gap_and_skips_processed_query(monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.dag.search_api_provider", lambda: "bocha")
    state = {
        "agent_tools_enabled": True, "track": "游戏与互动娱乐",
        "cache_data": {"原神": {}},
        "evidence_gaps": [{"competitor": "原神", "dimension": "user_substitution",
                           "query": "原神 知乎 用户评价", "required_source_types": ["community"]}],
        "acquisition_ledger": {"query_fingerprints": []},
    }
    calls = _plan_collector_tool_calls(state)
    assert calls[0]["name"] == "search_community_evidence"
    assert calls[0]["args"]["query"] == "原神 知乎 用户评价"
    state["acquisition_ledger"]["query_fingerprints"] = ["原神|原神 知乎 用户评价"]
    calls = _plan_collector_tool_calls(state)
    assert not [call for call in calls if call["name"].startswith("search_")]


def test_acquisition_runs_competitors_with_bounded_concurrency(monkeypatch) -> None:
    active = 0
    peak = 0

    async def fake_enrich(rows, track, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return rows

    async def fake_hydrate(rows, track, **kwargs):
        await asyncio.sleep(0.01)
        return rows

    monkeypatch.setattr("src.intake.acquisition.enrich_competitor_inputs_with_search", fake_enrich)
    monkeypatch.setattr("src.intake.acquisition.hydrate_sources_for_analysis", fake_hydrate)
    rows, trace = asyncio.run(acquire_competitor_inputs(
        [{"company": f"竞品{i}"} for i in range(5)], "测试赛道",
        AcquisitionBudget(max_competitor_concurrency=2, wall_clock_seconds=2),
    ))
    assert len(rows) == 5
    assert peak == 2
    assert all(item["metadata"]["acquisition_trace"] for item in rows)
    assert any(event["stage"] == "batch_complete" for event in trace)


def test_standard_budget_gives_each_of_five_competitors_four_queries(monkeypatch) -> None:
    observed: list[int] = []

    async def fake_enrich(rows, track, **kwargs):
        observed.append(kwargs["max_search_queries_per_competitor"])
        return rows

    async def fake_hydrate(rows, track, **kwargs):
        return rows

    monkeypatch.setattr("src.intake.acquisition.enrich_competitor_inputs_with_search", fake_enrich)
    monkeypatch.setattr("src.intake.acquisition.hydrate_sources_for_analysis", fake_hydrate)
    asyncio.run(acquire_competitor_inputs(
        [{"company": f"competitor-{index}"} for index in range(5)], "restaurant",
        AcquisitionBudget(max_search_calls=20, wall_clock_seconds=2),
    ))
    assert observed == [4, 4, 4, 4, 4]


def test_official_gap_is_not_routed_to_community_tool(monkeypatch) -> None:
    monkeypatch.setattr("src.pipeline.dag.search_api_provider", lambda: "bocha")
    state = {
        "agent_tools_enabled": True, "track": "restaurant", "cache_data": {"brand": {}},
        "evidence_gaps": [{"competitor": "brand", "query": "brand official website",
                           "source_types": ["official"], "required_source_types": ["official"]}],
        "acquisition_ledger": {"query_fingerprints": []},
    }
    calls = _plan_collector_tool_calls(state)
    assert calls[0]["name"] != "search_community_evidence"


def test_analyze_handler_does_not_wait_for_enrichment() -> None:
    source = Path("src/main.py").read_text(encoding="utf-8")
    handler = source[source.index("async def api_analyze_handler"):source.index("def _normalise_names")]
    runner = source[source.index("async def _run_custom_and_broadcast"):]
    assert "await enrich_competitor_inputs_with_search" not in handler
    assert "acquire_competitor_inputs" in runner
