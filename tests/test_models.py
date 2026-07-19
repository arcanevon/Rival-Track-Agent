"""Tests for Pydantic models — the contract between backend and frontend."""

import pytest
from src.models import (
    AgentNodeOutput,
    AgentRole,
    AgentStatus,
    EvidenceRef,
    PipelineState,
    WSMessage,
    WSMessageType,
    now_iso,
)


class TestEvidenceRef:
    def test_creates_valid_evidence(self):
        ev = EvidenceRef(
            source_url="https://example.com/report",
            source_label="Q1 2026 Report",
            quote="Revenue grew 20%",
            relevance="Shows competitor growth trajectory",
        )
        assert ev.source_url == "https://example.com/report"
        assert ev.source_label == "Q1 2026 Report"

    def test_empty_fields_allowed_for_partial_data(self):
        ev = EvidenceRef(source_url="", source_label="", quote="", relevance="")
        assert ev.source_url == ""


class TestAgentNodeOutput:
    def test_defaults_apply_correctly(self):
        node = AgentNodeOutput(node_id="test-1", role="collector")
        assert node.status == AgentStatus.PENDING
        assert node.label == ""
        assert node.confidence == 0.0
        assert node.evidence == []
        assert node.dependencies == []
        assert node.disagreements == []
        assert node.threat_target == {}
        assert node.threat_scores == {}
        assert node.method_findings == []
        assert node.response_actions == []

    def test_full_construction_with_evidence(self):
        evidence = [
            EvidenceRef(
                source_url="https://a.com",
                source_label="Source A",
                quote="text",
                relevance="key data",
            )
        ]
        node = AgentNodeOutput(
            node_id="analyst-a",
            role="analyst-a",
            status="completed",
            label="Analyst A Report",
            framework="VRIO",
            output_summary="Competitive threat is moderate",
            confidence=0.85,
            evidence=evidence,
            dependencies=["collector"],
            disagreements=[{"target_node_id": "analyst-b", "dimension": "threat level", "delta": 0.3}],
            threat_target={"name": "Our Product"},
            threat_scores={"Competitor A": {"user_substitution": 72, "overall": 72}},
            method_findings=[{"competitor": "Competitor A", "criterion": "价值性"}],
            response_actions=[{"priority": 80, "response_type": "product"}],
        )
        assert node.confidence == 0.85
        assert len(node.evidence) == 1
        assert len(node.disagreements) == 1
        assert node.threat_target["name"] == "Our Product"
        assert node.threat_scores["Competitor A"]["overall"] == 72
        assert node.method_findings[0]["criterion"] == "价值性"
        assert node.response_actions[0]["response_type"] == "product"

    def test_all_agent_roles_accepted(self):
        for role in AgentRole:
            node = AgentNodeOutput(node_id=role.value, role=role)
            assert node.role == role

    def test_threat_target_allows_confirmation_list(self):
        node = AgentNodeOutput(
            node_id="collector",
            role="collector",
            threat_target={
                "name": "Our Product",
                "needs_confirmation": ["positioning", "target_users"],
            },
        )

        assert node.threat_target["needs_confirmation"] == ["positioning", "target_users"]


class TestPipelineState:
    def test_empty_state_defaults(self):
        state = PipelineState()
        assert state.track == ""
        assert state.threat_target == {}
        assert state.competitors == []
        assert state.collector_output is None
        assert state.errors == []

    def test_with_competitors(self):
        state = PipelineState(track="AI Code Assistants", competitors=["Codium", "Tabnine"])
        assert len(state.competitors) == 2


class TestWSMessage:
    def test_node_update_message(self):
        node = AgentNodeOutput(node_id="c1", role="collector", status="running")
        msg = WSMessage(type=WSMessageType.NODE_UPDATE, timestamp=now_iso(), payload=node)
        assert msg.type == WSMessageType.NODE_UPDATE

    def test_error_message_with_dict_payload(self):
        msg = WSMessage(
            type=WSMessageType.ERROR,
            timestamp=now_iso(),
            payload={"message": "timeout", "node_id": "qa"},
        )
        assert msg.type == WSMessageType.ERROR


class TestNowIso:
    def test_returns_non_empty_string(self):
        ts = now_iso()
        assert isinstance(ts, str)
        assert len(ts) > 0
        assert "T" in ts  # ISO 8601 separator
