"""为竞品威胁分析提供可持久化的长期记忆。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.models.output import AgentNodeOutput


logger = logging.getLogger(__name__)

DEFAULT_MEMORY_PATH = Path(__file__).resolve().parents[2] / "logs" / "long-term-memory.json"


class LongTermMemoryStore:
    """保存并检索历史分析摘要，供后续分析作为背景上下文使用。"""

    def __init__(self, path: Path | str = DEFAULT_MEMORY_PATH, max_records: int = 20):
        self.path = Path(path)
        self.max_records = max(1, max_records)

    def recall(self, track: str, threat_target: dict[str, object] | None) -> str:
        """返回同一赛道或同一威胁目标的近期记忆。"""
        records = self._read_records()
        target_name = str((threat_target or {}).get("name", "")).strip().casefold()
        track_key = track.strip().casefold()
        matched = [
            record
            for record in records
            if (
                target_name
                and str(record.get("threat_target", "")).strip().casefold() == target_name
            )
            or (
                track_key
                and str(record.get("track", "")).strip().casefold() == track_key
            )
        ][-3:]
        if not matched:
            return "（没有可用的历史分析记忆）"

        lines = [
            "以下内容来自历史运行，只能用于发现变化和提出待验证假设，不能替代本轮证据："
        ]
        for record in matched:
            lines.append(
                f"- {record.get('timestamp', '')} | {record.get('threat_target', '')} | "
                f"{record.get('summary', '')}"
            )
        return "\n".join(lines)

    def remember(
        self,
        track: str,
        threat_target: dict[str, object] | None,
        outputs: list[AgentNodeOutput],
    ) -> None:
        """持久化一次成功运行的精简结论，避免保存完整提示词和网页正文。"""
        completed = [output for output in outputs if output.status.value == "completed"]
        if not completed:
            return
        final = next((output for output in reversed(completed) if output.role.value == "writer"), completed[-1])
        target_name = str((threat_target or {}).get("name", "")).strip()
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "track": track,
            "threat_target": target_name,
            "summary": final.output_summary[:1200],
            "threat_scores": final.threat_scores,
            "response_actions": final.response_actions[:5],
        }
        records = self._read_records()
        records.append(record)
        self._write_records(records[-self.max_records :])

    def _read_records(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("长期记忆文件无法读取，将使用空记忆。", exc_info=True)
            return []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _write_records(self, records: list[dict]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            logger.warning("长期记忆文件无法写入，本轮结果不会持久化。", exc_info=True)
