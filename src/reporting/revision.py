"""批注驱动的章节修订、证据白名单与可复现差异。"""

from __future__ import annotations

import difflib
import uuid
from datetime import datetime, timezone

from src.client.deepseek import call_deepseek


ANNOTATION_INTENTS = {
    "highlight_only": "仅高亮文本",
    "comment_only": "仅添加批注",
    "supplement_evidence": "补充证据",
    "challenge": "质疑结论",
    "rewrite": "改写表达",
    "shorten": "压缩内容",
    "compare": "增加对比",
}


def compile_annotation_gap(annotation: dict) -> dict[str, object]:
    """把自然语言批注编译为可被采集层消费的结构化缺口。"""
    intent = str(annotation.get("intent") or "comment_only")
    return {
        "section_id": str(annotation.get("section_id") or ""),
        "competitors": list(annotation.get("competitors") or []),
        "dimensions": list(annotation.get("dimensions") or []),
        "source_types": ["official", "benchmark", "community", "leading"]
        if intent in {"supplement_evidence", "challenge"} else [],
        "query": f"{annotation.get('quote', '')} {annotation.get('comment', '')}".strip(),
        "requires_research": intent in {"supplement_evidence", "challenge"},
    }


def section_diff(original: str, revised: str) -> list[dict[str, str]]:
    """返回前端可直接渲染的逐行 Diff。"""
    rows = []
    for line in difflib.ndiff(original.splitlines(), revised.splitlines()):
        if line.startswith("? "):
            continue
        rows.append({"kind": {"  ": "equal", "- ": "remove", "+ ": "add"}[line[:2]], "text": line[2:]})
    return rows


async def propose_revision(
    section_id: str,
    original: str,
    annotation: dict,
    approved_evidence: list[dict],
) -> dict[str, object]:
    """仅使用已批准证据生成建议稿；建议稿不会自动覆盖报告。"""
    evidence_text = "\n".join(
        f"[{row.get('evidence_id')}] {row.get('source_label')}: {row.get('quote')}"
        for row in approved_evidence[:24]
    ) or "（没有可用证据，不得新增事实）"
    prompt = f"""章节：{section_id}\n原文：\n{original}\n\n用户批注：{annotation.get('comment', '')}
意图：{ANNOTATION_INTENTS.get(str(annotation.get('intent')), '改写表达')}\n\n允许引用的证据：\n{evidence_text}
请只返回修订后的中文章节正文。不得虚构事实，不得使用列表外的证据编号。"""
    revised = (await call_deepseek(
        "你是竞品情报报告修订编辑。严格保持证据可追溯性。",
        prompt, temperature=0.2, max_tokens=2200,
    )).strip()
    approved_ids = {str(row.get("evidence_id")) for row in approved_evidence}
    used_ids = [value for value in approved_ids if value and value in revised]
    return {
        "revision_id": f"rev_{uuid.uuid4().hex[:12]}",
        "section_id": section_id, "original_text": original,
        "proposed_text": revised, "diff": section_diff(original, revised),
        "used_evidence_ids": sorted(used_ids), "decision": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["ANNOTATION_INTENTS", "compile_annotation_gap", "propose_revision", "section_diff"]
