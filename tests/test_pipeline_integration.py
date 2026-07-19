"""Integration test: run_pipeline_custom with mocked DeepSeek responses."""

import pytest
from unittest.mock import AsyncMock, patch

from src.models import AgentNodeOutput, AgentRole, AgentStatus, EvidenceRef


_DEFAULT_ASSESSMENT = {
    "TestCompetitor": {"level": "medium", "score": 42, "evidence_strength": "moderate"},
}


def _make_output(node_id: str, role: str, label: str, confidence: float = 0.8,
                 threat_scores: dict | None = None,
                 threat_assessment: dict | str = _DEFAULT_ASSESSMENT) -> AgentNodeOutput:
    """Build a minimal valid AgentNodeOutput for a mock agent response."""
    return AgentNodeOutput(
        node_id=node_id,
        role=AgentRole(role),
        status=AgentStatus.COMPLETED,
        label=label,
        framework={"collector": "O/B/C/L", "analyst-a": "VRIO",
                   "analyst-b": "SWOT", "qa": "Evidence-Dialectic",
                   "writer": "Decision-Synthesis"}.get(role, ""),
        input_summary=f"{label} mock input",
        output_summary=f"{label} mock output",
        confidence=confidence,
        evidence=[
            EvidenceRef(
                source_url="https://example.com",
                source_label="Example Source",
                quote="Mock evidence quote",
                relevance="high",
                source_tier="O",
            )
        ],
        threat_scores=threat_scores or {
            "TestCompetitor": {
                "user_substitution": 45, "capability_catch_up": 55,
                "distribution": 30, "strategic_expansion": 40, "overall": 42,
            }
        },
        threat_assessment=threat_assessment,
        per_competitor_notes={"TestCompetitor": "mock notes"},
        method_findings=([{
            "competitor": "TestCompetitor",
            "criterion": "method criterion",
            "finding": "partially supported",
            "evidence_refs": ["O1"],
            "reasoning": "mock causal reasoning",
            "uncertainty": "mock uncertainty",
            "mapped_dimensions": ["capability_catch_up"],
        }] if role in {"analyst-a", "analyst-b"} else []),
        threat_target={"name": "OurProduct"},
        dependencies=[],
        response_actions=[
            {
                "priority": 1, "response_type": "monitor",
                "related_threat_dimension": "capability_catch_up",
                "competitor": "TestCompetitor",
                "concrete_action": "Track release notes monthly.",
            }
        ],
    )


@pytest.mark.anyio
async def test_run_pipeline_custom_returns_five_agent_outputs():
    """run_pipeline_custom should return 5 AgentNodeOutputs in DAG order."""
    collector = _make_output("collector", "collector", "Evidence Collector")
    analyst_a = _make_output("analyst-a", "analyst-a", "VRIO Analyst", threat_assessment="")
    analyst_b = _make_output("analyst-b", "analyst-b", "SWOT Analyst", threat_assessment="")
    qa = _make_output("qa", "qa", "QA Arbitrator")
    writer = _make_output("writer", "writer", "Decision Writer")

    reflected_qa = qa.model_copy(update={"output_summary": "QA reflected mock output"})
    mock_call_and_parse = AsyncMock(
        side_effect=[collector, analyst_a, analyst_b, qa, reflected_qa, writer]
    )

    user_data = [{
        "company": "TestCompetitor",
        "official_sources": [{"url": "https://example.com", "label": "Official Site",
                              "evidence_url": "https://example.com", "authority": "high",
                              "direct_evidence": True, "channel": "official"}],
        "benchmark_sources": [],
        "community_sources": [],
        "leading_sources": [],
    }]

    threat_target = {
        "name": "OurProduct",
        "positioning": "Test product",
        "target_users": "developers",
        "core_capabilities": "testing",
        "competitive_concern": "competitive threat analysis",
    }

    with (
        patch("src.pipeline.nodes.call_and_parse", mock_call_and_parse),
        patch("src.pipeline.nodes.broadcast_node_update", AsyncMock()),
        patch("src.pipeline.nodes.broadcast_error", AsyncMock()),
        patch("src.pipeline.nodes._log_output"),
        patch("src.pipeline.nodes.build_evidence_gaps", return_value=[]),
        patch("src.pipeline.dag._LONG_TERM_MEMORY.recall", return_value="no memory"),
        patch("src.pipeline.dag._LONG_TERM_MEMORY.remember"),
    ):
        from src.pipeline import run_pipeline_custom
        results = await run_pipeline_custom("Test Track", user_data, threat_target)

    assert len(results) == 5
    roles = [r.role for r in results]
    assert roles == ["collector", "analyst-a", "analyst-b", "qa", "writer"]

    for r in results:
        assert r.status == AgentStatus.COMPLETED
        assert r.threat_scores

    assert mock_call_and_parse.call_count == 6
    assert "Reflection phase" in mock_call_and_parse.await_args_list[4].args[0]
