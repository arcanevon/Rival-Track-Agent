"""跨报告证据工作区与人工修正事件存储。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.models.output import AgentNodeOutput


DEFAULT_WORKSPACE_PATH = Path(__file__).resolve().parents[2] / "logs" / "evidence-workspace.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceWorkspaceStore:
    """原子持久化报告、证据、修订版本和人工反馈。"""

    def __init__(self, path: Path | str = DEFAULT_WORKSPACE_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    def save_report(self, report_id: str, track: str, outputs: list[AgentNodeOutput]) -> None:
        """保存报告快照，并按稳定证据 ID 跨报告去重。"""
        with self._lock:
            data = self._read()
            serialized = [item.model_dump(mode="json") for item in outputs]
            data["reports"][report_id] = {
                "report_id": report_id, "track": track, "created_at": _now(),
                "outputs": serialized, "annotations": [], "revisions": [],
            }
            for output in serialized:
                for evidence in output.get("evidence", []):
                    evidence_id = str(evidence.get("evidence_id") or "").strip()
                    if not evidence_id:
                        continue
                    current = data["evidence"].get(evidence_id, {})
                    report_ids = list(current.get("report_ids", []))
                    if report_id not in report_ids:
                        report_ids.append(report_id)
                    # 原始摘录首次写入后不被人工反馈覆盖。
                    data["evidence"][evidence_id] = {
                        **evidence, **current, "evidence_id": evidence_id,
                        "first_seen": current.get("first_seen") or _now(),
                        "last_seen": _now(), "report_ids": report_ids,
                        "human_status": current.get("human_status", "unreviewed"),
                        "human_note": current.get("human_note", ""),
                    }
            self._write(data)

    def get_report(self, report_id: str) -> dict | None:
        return self._read()["reports"].get(report_id)

    def list_evidence(self, *, track: str = "", status: str = "") -> list[dict]:
        data = self._read()
        report_ids = {
            report_id for report_id, report in data["reports"].items()
            if not track or str(report.get("track", "")) == track
        }
        rows = []
        for row in data["evidence"].values():
            if status and row.get("human_status") != status:
                continue
            if track and not report_ids.intersection(row.get("report_ids", [])):
                continue
            rows.append(row)
        return sorted(rows, key=lambda item: str(item.get("last_seen", "")), reverse=True)

    def upsert_collected_evidence(self, report_id: str, track: str, rows: list[dict]) -> list[str]:
        """把批注补采取得的正文证据合并到工作区，并返回新增证据编号。"""
        accepted_ids: list[str] = []
        with self._lock:
            data = self._read()
            for row in rows:
                evidence_id = str(row.get("evidence_id") or "").strip()
                if not evidence_id or row.get("evidence_grade") != "citable_content":
                    continue
                current = data["evidence"].get(evidence_id, {})
                report_ids = list(current.get("report_ids", []))
                if report_id not in report_ids:
                    report_ids.append(report_id)
                data["evidence"][evidence_id] = {
                    "evidence_id": evidence_id,
                    "source_url": row.get("evidence_url") or row.get("url", ""),
                    "source_label": row.get("label") or row.get("title", ""),
                    "quote": str(row.get("scraped_text") or row.get("text") or "")[:1200],
                    "relevance": row.get("relevance", "批注定向补采"),
                    "source_tier": row.get("actual_source_type", "web"),
                    **current,
                    "first_seen": current.get("first_seen") or _now(), "last_seen": _now(),
                    "report_ids": report_ids, "track": track,
                    "human_status": current.get("human_status", "unreviewed"),
                }
                accepted_ids.append(evidence_id)
            self._write(data)
        return accepted_ids

    def review_evidence(self, evidence_id: str, status: str, note: str = "") -> dict | None:
        if status not in {"accepted", "rejected", "edited", "unreviewed"}:
            raise ValueError("无效的人工审核状态")
        with self._lock:
            data = self._read()
            row = data["evidence"].get(evidence_id)
            if not row:
                return None
            row["human_status"] = status
            row["human_note"] = note
            row["reviewed_at"] = _now()
            data["events"].append({
                "kind": "evidence_review", "evidence_id": evidence_id,
                "decision": status, "timestamp": _now(),
            })
            self._write(data)
            return row

    def add_annotation(self, report_id: str, annotation: dict) -> dict:
        with self._lock:
            data = self._read()
            report = data["reports"].get(report_id)
            if not report:
                raise KeyError(report_id)
            report["annotations"].append(annotation)
            self._write(data)
            return annotation

    def add_revision(self, report_id: str, revision: dict) -> dict:
        with self._lock:
            data = self._read()
            report = data["reports"].get(report_id)
            if not report:
                raise KeyError(report_id)
            report["revisions"].append(revision)
            self._write(data)
            return revision

    def decide_revision(self, report_id: str, revision_id: str, decision: str) -> dict | None:
        if decision not in {"accepted", "rejected", "edited"}:
            raise ValueError("无效的修订决策")
        with self._lock:
            data = self._read()
            report = data["reports"].get(report_id)
            if not report:
                return None
            revision = next((r for r in report["revisions"] if r.get("revision_id") == revision_id), None)
            if not revision:
                return None
            revision["decision"] = decision
            revision["decided_at"] = _now()
            if decision in {"accepted", "edited"}:
                writer = next((item for item in report.get("outputs", []) if item.get("role") == "writer"), None)
                if writer is not None:
                    sections = writer.setdefault("report_sections", {})
                    sections[str(revision.get("section_id", ""))] = str(revision.get("proposed_text", ""))
            data["events"].append({
                "kind": "revision", "report_id": report_id,
                "revision_id": revision_id, "decision": decision, "timestamp": _now(),
            })
            self._write(data)
            return revision

    def metrics(self) -> dict[str, object]:
        events = self._read()["events"]
        revision_decisions = [event.get("decision") for event in events if event.get("kind") == "revision"]
        evidence_decisions = [event.get("decision") for event in events if event.get("kind") == "evidence_review"]
        decisions = revision_decisions + evidence_decisions
        total = len(decisions)
        counts = {key: decisions.count(key) for key in ("accepted", "rejected", "edited")}
        return {
            "人工反馈总数": total,
            "接受数": counts["accepted"], "拒绝数": counts["rejected"],
            "人工编辑数": counts["edited"],
            "AI修订采用率": round(revision_decisions.count("accepted") / len(revision_decisions), 4)
            if revision_decisions else 0,
            "证据驳回率": round(evidence_decisions.count("rejected") / len(evidence_decisions), 4)
            if evidence_decisions else 0,
        }

    def _read(self) -> dict:
        empty = {"reports": {}, "evidence": {}, "events": []}
        if not self.path.exists():
            return empty
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty
        if not isinstance(value, dict):
            return empty
        return {key: value.get(key, default) for key, default in empty.items()}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


__all__ = ["DEFAULT_WORKSPACE_PATH", "EvidenceWorkspaceStore"]
