"""
Shared Pydantic models: the contract between backend modules and the frontend.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class AgentRole(str, Enum):
    COLLECTOR = "collector"
    ANALYST_A = "analyst-a"
    ANALYST_B = "analyst-b"
    QA = "qa"
    WRITER = "writer"


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class EvidenceRef(BaseModel):
    evidence_id: str = ""
    source_url: str
    source_label: str
    quote: str
    relevance: str
    source_tier: str = ""


class AgentNodeOutput(BaseModel):
    node_id: str
    role: AgentRole
    status: AgentStatus = AgentStatus.PENDING
    label: str = ""
    framework: str = ""
    input_summary: str = ""
    output_summary: str = ""
    confidence: float = 0.0
    evidence: list[EvidenceRef] = []
    evidence_gaps: list[dict] = []
    dependencies: list[str] = []
    disagreements: list[dict] = []
    threat_assessment: str | dict[str, object] = ""
    threat_target: dict[str, object] = {}
    threat_scores: dict[str, object] = {}
    per_competitor_notes: dict[str, str] = {}
    method_findings: list[dict[str, object]] = []
    response_actions: list[dict] = []
    expansion_likelihood: float = 0.0
    report_sections: dict[str, str] = {}
    quality_metrics: dict[str, object] = {}
    rework_history: list[dict[str, object]] = []
    timestamp: str = ""


class WSMessageType(str, Enum):
    NODE_UPDATE = "node_update"
    PIPELINE_COMPLETE = "pipeline_complete"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    FULL_STATE = "full_state"


class WSMessage(BaseModel):
    type: WSMessageType
    timestamp: str
    payload: AgentNodeOutput | list[AgentNodeOutput] | dict[str, object] | None = None


class CompetitorCache(BaseModel):
    company: str
    track: str = ""
    official_sources: list[dict[str, str]] = []
    community_sources: list[dict[str, str]] = []
    screenshots: list[str] = []
    metadata: dict[str, object] = {}


class PipelineState(BaseModel):
    track: str = ""
    threat_target: dict[str, object] = {}
    competitors: list[str] = []
    collector_output: Optional[dict] = None
    analyst_a_output: Optional[AgentNodeOutput] = None
    analyst_b_output: Optional[AgentNodeOutput] = None
    qa_output: Optional[AgentNodeOutput] = None
    writer_output: Optional[AgentNodeOutput] = None
    errors: list[dict] = []
    started_at: str = ""
    completed_at: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
