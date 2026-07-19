"""记忆、Reflection 和 ToolNode 能力的回归测试。"""

import json
import asyncio

from langgraph.checkpoint.memory import MemorySaver

from src.client.deepseek import parse_agent_output
from src.memory import LongTermMemoryStore
from src.models import AgentNodeOutput, AgentRole, AgentStatus
from src.pipeline.dag import (
    _outputs_from_graph_state,
    _plan_collector_tool_calls,
    build_pipeline_dag,
)
from src.agents.tools import _hydrate_ranked_results


def _writer_output(summary: str = "历史结论") -> AgentNodeOutput:
    return AgentNodeOutput(
        node_id="writer",
        role=AgentRole.WRITER,
        status=AgentStatus.COMPLETED,
        output_summary=summary,
        threat_scores={"竞品甲": {"overall": 70}},
        response_actions=[{"competitor": "竞品甲", "concrete_action": "验证替代路径"}],
    )


def test_long_term_memory_persists_and_recalls(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.json")
    target = {"name": "我方产品"}

    store.remember("测试赛道", target, [_writer_output()])

    recalled = store.recall("测试赛道", target)
    assert "历史结论" in recalled
    assert "不能替代本轮证据" in recalled


def test_pipeline_uses_short_term_checkpointer():
    checkpointer = MemorySaver()
    graph = build_pipeline_dag(checkpointer=checkpointer)
    assert graph.checkpointer is checkpointer


def test_graph_outputs_are_rebuilt_in_topology_order():
    collector = _writer_output("采集结果").model_copy(
        update={"node_id": "collector", "role": AgentRole.COLLECTOR}
    )
    writer = _writer_output("撰写结果")
    state = {
        "writer_output": writer.model_dump(),
        "collector_output": collector.model_dump(),
    }

    outputs = _outputs_from_graph_state(state)

    assert [output.node_id for output in outputs] == ["collector", "writer"]


def test_tool_planner_creates_search_and_reader_actions(monkeypatch):
    monkeypatch.setattr("src.pipeline.dag.search_api_provider", lambda: "bocha")
    monkeypatch.setattr(
        "src.pipeline.dag.build_evidence_acquisition_plans",
        lambda cache, track: {
            "竞品甲": {
                "queries": [
                    {"query": "竞品甲 定价", "freshness": "oneMonth"},
                ]
            }
        },
    )
    state = {
        "agent_tools_enabled": True,
        "track": "测试赛道",
        "cache_data": {
            "竞品甲": {
                "official_sources": [
                    {
                        "url": "https://example.com/pricing",
                        "evidence_status": "fetch_failed",
                    }
                ]
            }
        },
    }

    calls = _plan_collector_tool_calls(state)
    assert {call["name"] for call in calls} == {
        "search_competitor_evidence",
        "read_evidence_page",
    }


def test_tool_planner_uses_community_search_for_community_gap(monkeypatch):
    monkeypatch.setattr("src.pipeline.dag.search_api_provider", lambda: "bocha")
    monkeypatch.setattr(
        "src.pipeline.dag.build_evidence_acquisition_plans",
        lambda cache, track: {
            "竞品甲": {
                "queries": [{
                    "query": "竞品甲 用户评价 使用体验",
                    "freshness": "oneMonth",
                    "source_types": ["community"],
                }]
            }
        },
    )
    calls = _plan_collector_tool_calls({
        "agent_tools_enabled": True,
        "track": "测试赛道",
        "cache_data": {"竞品甲": {}},
    })

    assert calls[0]["name"] == "search_community_evidence"


def test_tool_planner_rotates_competitors_during_rework(monkeypatch):
    """返工轮次应先覆盖首轮预算外的后续竞品。"""
    monkeypatch.setattr("src.pipeline.dag.search_api_provider", lambda: "bocha")
    monkeypatch.setattr(
        "src.pipeline.dag.build_evidence_acquisition_plans",
        lambda cache, track: {
            name: {"queries": [{"query": f"{name} 官方公告", "source_types": ["official"]}]}
            for name in cache
        },
    )
    calls = _plan_collector_tool_calls({
        "agent_tools_enabled": True,
        "track": "游戏与互动娱乐",
        "rework_round": 1,
        "cache_data": {name: {} for name in ("竞品甲", "竞品乙", "竞品丙", "竞品丁", "竞品戊", "竞品己")},
    })

    searched = [call["args"]["competitor"] for call in calls if call["name"].startswith("search_")]
    assert len(searched) == 6
    assert set(searched) == {"竞品甲", "竞品乙", "竞品丙", "竞品丁", "竞品戊", "竞品己"}


def test_tool_planner_covers_four_competitors_in_first_round(monkeypatch):
    monkeypatch.setattr("src.pipeline.dag.search_api_provider", lambda: "bocha")
    monkeypatch.setattr(
        "src.pipeline.dag.build_evidence_acquisition_plans",
        lambda cache, track: {
            name: {"queries": [{"query": f"{name} 官方公告", "source_types": ["official"]}]}
            for name in cache
        },
    )
    calls = _plan_collector_tool_calls({
        "agent_tools_enabled": True,
        "track": "游戏与互动娱乐",
        "rework_round": 0,
        "cache_data": {name: {} for name in ("明日方舟", "原神", "重返未来：1999", "崩坏：星穹铁道")},
    })

    searched = [call["args"]["competitor"] for call in calls if call["name"].startswith("search_")]
    assert searched == ["明日方舟", "原神", "重返未来：1999", "崩坏：星穹铁道"]


def test_search_tool_hydrates_ranked_content_pages(monkeypatch):
    """搜索工具应把候选内容页读成正文，而不是只把 URL 交给 Collector。"""
    async def mock_fetch(session, url):
        return {
            "url": url,
            "title": "游戏版本公告",
            "text": "官方版本公告介绍新角色、新地图、活动玩法和版本更新计划。" * 12,
            "fetch_method": "jina_reader",
            "candidate_only": False,
            "source_quality": {"quality_score": 90},
        }

    monkeypatch.setattr("src.agents.tools.fetch_readable_source", mock_fetch)
    rows = asyncio.run(_hydrate_ranked_results(
        object(),
        [{"title": "公告", "url": "https://example.com/news/1", "snippet": "版本更新"}],
        "测试游戏",
    ))

    assert rows[0]["scraped_text"].startswith("官方版本公告")
    assert rows[0]["candidate_only"] is False
    assert rows[0]["fetch_method"] == "jina_reader"


def test_parser_preserves_evidence_gaps():
    raw = json.dumps(
        {
            "label": "质检 Agent",
            "confidence": 0.8,
            "evidence_gaps": [
                {"competitor": "竞品甲", "slot": "community_pain"},
            ],
        },
        ensure_ascii=False,
    )
    output = parse_agent_output(raw, "qa", "qa")
    assert output.evidence_gaps == [
        {"competitor": "竞品甲", "slot": "community_pain"},
    ]
