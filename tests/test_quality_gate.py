"""QA Quality Gate、条件返工和复审指标的回归测试。"""

import pytest
from unittest.mock import AsyncMock

from src.models import AgentNodeOutput, AgentRole, AgentStatus
from src.pipeline.dag import _lg_quality_gate, _route_after_quality_gate, build_pipeline_dag
from src.pipeline.quality_gate import evaluate_quality_gate


def _qa_output(**updates) -> AgentNodeOutput:
    output = AgentNodeOutput(
        node_id="qa",
        role=AgentRole.QA,
        status=AgentStatus.COMPLETED,
        label="质检 Agent",
        confidence=0.8,
        threat_scores={
            "竞品甲": {
                "user_substitution": 70,
                "capability_catch_up": 65,
                "distribution": 60,
                "strategic_expansion": 55,
                "overall": 63,
            }
        },
        threat_assessment={
            "竞品甲": {
                "level": "medium",
                "score": 63,
                "evidence_strength": "moderate",
            }
        },
    )
    return output.model_copy(update=updates)


def test_quality_gate_passes_complete_qa_output():
    result = evaluate_quality_gate(
        _qa_output(), ["竞品甲"],
        rework_round=0, max_rework_rounds=1, tools_enabled=True,
    )

    assert result.route == "write"
    assert result.passed is True
    assert result.metrics["matrix_completeness"] == 1.0
    assert result.metrics["quality_score"] > 0


def test_quality_gate_routes_evidence_gaps_to_collection():
    result = evaluate_quality_gate(
        _qa_output(evidence_gaps=[{"competitor": "竞品甲", "slot": "official_pricing"}]),
        ["竞品甲"],
        rework_round=0, max_rework_rounds=1, tools_enabled=True,
    )

    assert result.route == "collect"
    assert result.passed is False
    assert result.metrics["evidence_gap_count"] == 1


def test_quality_gate_routes_low_relevance_metrics_to_collection():
    result = evaluate_quality_gate(
        _qa_output(), ["竞品甲"],
        rework_round=0,
        max_rework_rounds=1,
        tools_enabled=True,
        evidence_metrics={
            "evaluated_sources": 5,
            "precision_at_5": 0.4,
            "claim_answer_rate": 0.5,
            "bad_domain_leakage": 0.2,
        },
    )

    assert result.route == "collect"
    assert result.metrics["relevance_precision_at_5"] == 0.4
    assert result.metrics["bad_domain_leakage"] == 0.2


def test_quality_gate_routes_invalid_matrix_to_both_analysts():
    result = evaluate_quality_gate(
        _qa_output(threat_scores={}), ["竞品甲"],
        rework_round=0, max_rework_rounds=1, tools_enabled=True,
    )

    assert result.route == "analyze"
    assert result.metrics["structural_error_count"] > 0


def test_quality_gate_routes_incomplete_method_trace_to_both_analysts():
    analyst_a = AgentNodeOutput(node_id="analyst-a", role="analyst-a")
    analyst_b = AgentNodeOutput(
        node_id="analyst-b",
        role="analyst-b",
        method_findings=[{
            "competitor": "竞品甲",
            "criterion": "用户替代路径",
            "finding": "部分成立",
            "evidence_refs": ["C1"],
            "reasoning": "存在切换信号",
            "uncertainty": "缺少迁移率",
            "mapped_dimensions": ["user_substitution"],
        }],
    )
    result = evaluate_quality_gate(
        _qa_output(), ["竞品甲"],
        rework_round=0,
        max_rework_rounds=1,
        tools_enabled=True,
        analyst_outputs=(analyst_a, analyst_b),
    )

    assert result.route == "analyze"
    assert result.metrics["method_trace_coverage"] == 0.5


def test_quality_gate_routes_material_disagreement_to_both_analysts():
    result = evaluate_quality_gate(
        _qa_output(disagreements=[{
            "competitor": "竞品甲",
            "dimension": "distribution",
            "delta": 0.4,
            "conflict_level": "high",
        }]),
        ["竞品甲"],
        rework_round=0, max_rework_rounds=1, tools_enabled=True,
    )

    assert result.route == "analyze"
    assert result.metrics["actionable_disagreement_count"] == 1


def test_quality_gate_stops_when_rework_budget_is_exhausted():
    result = evaluate_quality_gate(
        _qa_output(evidence_gaps=[{"competitor": "竞品甲", "slot": "community_pain"}]),
        ["竞品甲"],
        rework_round=1,
        max_rework_rounds=1,
        tools_enabled=True,
        previous_metrics={"quality_score": 60.0},
    )

    assert result.route == "write"
    assert result.forced_completion is True
    assert result.metrics["score_delta"] is not None


@pytest.mark.anyio
async def test_quality_gate_node_persists_history_and_routes_analysis(monkeypatch):
    qa = _qa_output(threat_scores={})
    monkeypatch.setattr("src.pipeline.dag.broadcast_node_update", lambda output: _async_none())
    state = {
        "qa_output": qa.model_dump(),
        "competitors": ["竞品甲"],
        "agent_tools_enabled": True,
        "rework_round": 0,
        "max_rework_rounds": 1,
        "quality_history": [],
    }

    updates = await _lg_quality_gate(state)
    routed_state = {**state, **updates}

    assert updates["rework_round"] == 1
    assert len(updates["quality_history"]) == 1
    assert _route_after_quality_gate(routed_state) == ["analyst_a", "analyst_b"]
    updated_qa = AgentNodeOutput(**updates["qa_output"])
    assert updated_qa.quality_metrics["structural_error_count"] > 0
    assert len(updated_qa.rework_history) == 1


@pytest.mark.anyio
async def test_langgraph_reworks_both_analysts_then_records_review_delta(monkeypatch):
    collector = _qa_output().model_copy(update={
        "node_id": "collector",
        "role": AgentRole.COLLECTOR,
        "threat_assessment": "",
    })
    analyst_a = collector.model_copy(update={"node_id": "analyst-a", "role": AgentRole.ANALYST_A})
    analyst_b = collector.model_copy(update={"node_id": "analyst-b", "role": AgentRole.ANALYST_B})
    invalid_qa = _qa_output(threat_scores={})
    reviewed_qa = _qa_output(confidence=0.9)
    writer = reviewed_qa.model_copy(update={"node_id": "writer", "role": AgentRole.WRITER})

    collector_mock = AsyncMock(return_value=collector)
    analyst_a_mock = AsyncMock(return_value=analyst_a)
    analyst_b_mock = AsyncMock(return_value=analyst_b)
    qa_mock = AsyncMock(side_effect=[invalid_qa, reviewed_qa])
    writer_mock = AsyncMock(return_value=writer)
    monkeypatch.setattr("src.pipeline.dag.run_collector", collector_mock)
    monkeypatch.setattr("src.pipeline.dag.run_analyst_a", analyst_a_mock)
    monkeypatch.setattr("src.pipeline.dag.run_analyst_b", analyst_b_mock)
    monkeypatch.setattr("src.pipeline.dag.run_qa", qa_mock)
    monkeypatch.setattr("src.pipeline.dag.run_writer", writer_mock)
    monkeypatch.setattr("src.pipeline.dag.broadcast_node_update", AsyncMock())

    app = build_pipeline_dag()
    final_state = await app.ainvoke(
        {
            "track": "测试赛道",
            "threat_target": {"name": "我方产品"},
            "competitors": ["竞品甲"],
            "cache_data": {"竞品甲": {}},
            "agent_tools_enabled": False,
            "long_term_memory": "",
            "messages": [],
            "rework_round": 0,
            "max_rework_rounds": 1,
            "rework_feedback": "",
            "quality_gate_decision": {},
            "quality_history": [],
        },
        config={"configurable": {"thread_id": "quality-loop-test"}},
    )

    assert collector_mock.await_count == 1
    assert analyst_a_mock.await_count == 2
    assert analyst_b_mock.await_count == 2
    assert qa_mock.await_count == 2
    assert writer_mock.await_count == 1
    assert len(final_state["quality_history"]) == 2
    assert final_state["quality_history"][0]["route"] == "analyze"
    assert final_state["quality_history"][1]["route"] == "write"
    assert final_state["quality_history"][1]["metrics"]["score_delta"] > 0


async def _async_none():
    return None
