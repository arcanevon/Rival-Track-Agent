from src.memory.evidence_workspace import EvidenceWorkspaceStore
from src.models.output import AgentNodeOutput, AgentRole, AgentStatus, EvidenceRef


def _output() -> AgentNodeOutput:
    return AgentNodeOutput(
        node_id="writer", role=AgentRole.WRITER, status=AgentStatus.COMPLETED,
        evidence=[EvidenceRef(evidence_id="ev_same", source_url="https://example.com/a",
                              source_label="来源", quote="原始摘录", relevance="相关")],
        report_sections={"executive_summary": "结论"},
    )


def test_workspace_deduplicates_evidence_across_reports(tmp_path) -> None:
    store = EvidenceWorkspaceStore(tmp_path / "workspace.json")
    store.save_report("r1", "游戏", [_output()])
    store.save_report("r2", "游戏", [_output()])
    rows = store.list_evidence(track="游戏")
    assert len(rows) == 1
    assert rows[0]["report_ids"] == ["r1", "r2"]
    store.review_evidence("ev_same", "rejected", "来源不可靠")
    assert store.list_evidence(status="rejected")[0]["quote"] == "原始摘录"
    assert store.metrics()["证据驳回率"] == 1.0


def test_accepted_revision_creates_persistent_report_version(tmp_path) -> None:
    store = EvidenceWorkspaceStore(tmp_path / "workspace.json")
    store.save_report("r1", "游戏", [_output()])
    store.add_revision("r1", {
        "revision_id": "rev1", "section_id": "executive_summary",
        "original_text": "结论", "proposed_text": "经证据校正后的结论", "decision": "pending",
    })
    store.decide_revision("r1", "rev1", "accepted")
    writer = store.get_report("r1")["outputs"][0]
    assert writer["report_sections"]["executive_summary"] == "经证据校正后的结论"
    assert store.metrics()["AI修订采用率"] == 1.0
