"""DeepSeek 客户端：异步调用、重试、结构校验和降级处理。"""

import asyncio
import json
import logging
import os

from openai import AsyncOpenAI
from pydantic import ValidationError

from src.models.output import AgentNodeOutput, EvidenceRef, now_iso

logger = logging.getLogger(__name__)


class DeepSeekError(Exception):
    pass


class DeepSeekTimeout(DeepSeekError):
    pass


class DeepSeekSchemaError(DeepSeekError):
    pass


class DeepSeekRefusalError(DeepSeekError):
    pass


_REPORT_SECTION_LABELS = {
    "immediate_risks": "即时风险",
    "strategic_opportunities": "战略机会",
    "watch_signals": "观察信号",
    "competitor": "竞品",
    "positioning": "定位",
    "strongest_threat_dimension": "最强威胁维度",
    "evidence_sufficiency": "证据充分性",
    "uncertainty": "不确定性",
    "finding": "分析发现",
    "evidence": "证据",
    "score": "评分",
    "level": "等级",
    "action": "行动",
    "priority": "优先级",
    "reason": "原因",
    "conclusion": "结论",
}


def _contains_chinese(text: str) -> bool:
    """判断字段名是否已经包含中文，避免重复映射。"""
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _report_section_label(key: object, index: int) -> str:
    """把模型生成的内部字段名转换为前端可读的中文标签。"""
    text = str(key).strip()
    if text in _REPORT_SECTION_LABELS:
        return _REPORT_SECTION_LABELS[text]
    if _contains_chinese(text):
        return text
    return f"补充信息 {index}"


def _report_section_text(value: object) -> str:
    """将 Writer 偶发返回的数组或对象归一化为中文 Markdown。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            text = _report_section_text(item)
            if text:
                lines.append(f"- {text.replace(chr(10), chr(10) + '  ')}")
        return "\n".join(lines)
    if isinstance(value, dict):
        lines: list[str] = []
        for index, (key, item) in enumerate(value.items(), start=1):
            text = _report_section_text(item)
            if not text:
                continue
            label = _report_section_label(key, index)
            separator = "\n" if text.startswith("- ") else ""
            lines.append(f"**{label}**：{separator}{text}")
        return "\n\n".join(lines)
    return str(value).strip()


def _normalize_report_sections(value: object) -> dict[str, str]:
    """在模型响应边界收拢报告章节类型，保持下游字符串契约。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DeepSeekSchemaError("report_sections must be a JSON object")
    return {
        str(key): _report_section_text(section)
        for key, section in value.items()
    }


# 扩展拒答模式，覆盖中英文及边缘情况
_REFUSAL_PATTERNS: list[tuple[str, ...]] = [
    ("I'm sorry", "cannot"),
    ("I apologize", "unable to"),
    ("抱歉", "无法"),
    ("对不起", "不能"),
    ("很抱歉", "无法提供"),
    ("作为AI", "无法"),
    ("As an AI", "cannot"),
    ("I cannot", "provide"),
    ("content policy",),
    ("安全政策",),
    ("无法回答",),
    ("不能提供",),
    ("不符合", "规范"),
]


def _detect_refusal(text: str) -> bool:
    """Check if the response text matches any known refusal pattern."""
    head = text[:500]
    for pattern in _REFUSAL_PATTERNS:
        if all(p in head for p in pattern):
            return True
    return False


def get_client() -> AsyncOpenAI:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise DeepSeekError(
            "DEEPSEEK_API_KEY not set. Copy .env.example to .env and add your key."
        )
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


async def call_deepseek(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = "deepseek-chat",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    max_retries: int = 3,
    timeout: int = 30,
) -> str:
    """Call DeepSeek with retry and backoff. Returns the raw response text."""
    client = get_client()
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            content = response.choices[0].message.content
            if not content:
                raise DeepSeekRefusalError(
                    f"DeepSeek returned empty response (attempt {attempt})"
                )

            if _detect_refusal(content):
                raise DeepSeekRefusalError(
                    f"DeepSeek appears to have refused the request (attempt {attempt})"
                )
            return content

        except DeepSeekRefusalError:
            raise
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning("DeepSeek attempt %d failed: %s. Retrying in %ds...", attempt, e, wait)
                await asyncio.sleep(wait)

    raise DeepSeekTimeout(
        f"DeepSeek failed after {max_retries} attempts. Last error: {last_error}"
    )


def _parse_agent_output(raw_text: str, role: str, node_id: str) -> AgentNodeOutput:
    """解析 DeepSeek JSON，并转换为统一的智能体输出。"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        try:
            if "```json" in raw_text:
                block = raw_text.split("```json")[1].split("```")[0]
                data = json.loads(block)
            elif "```" in raw_text:
                block = raw_text.split("```")[1].split("```")[0]
                data = json.loads(block)
            else:
                raise exc
        except (IndexError, json.JSONDecodeError) as block_exc:
            raise DeepSeekSchemaError(
                "DeepSeek response is not valid complete JSON.\n"
                f"First 300 chars: {raw_text[:300]}"
            ) from block_exc

    if not isinstance(data, dict):
        raise DeepSeekSchemaError("DeepSeek response root must be a JSON object")

    evidence = []
    for i, ev in enumerate(data.get("evidence", [])):
        ev_url = ev.get("source_url", ev.get("url", ev.get("u", "")))
        ev_quote = ev.get("quote", ev.get("q", ""))
        if not ev_url or not ev_quote:
            logger.warning(
                "Evidence item %d in %s output missing %s — LLM may have used unrecognized field names.",
                i, role, "source_url" if not ev_url else "quote",
            )
        evidence.append(EvidenceRef(
            evidence_id=ev.get("evidence_id", ev.get("id", "")),
            source_url=ev_url,
            source_label=ev.get("source_label", ev.get("label", ev.get("l", ""))),
            quote=ev_quote,
            relevance=ev.get("relevance", ev.get("r", "")),
            source_tier=ev.get("source_tier", ev.get("t", "")),
        ))

    return AgentNodeOutput(
        node_id=node_id,
        role=role,
        status="completed",
        label=data.get("label", role),
        framework=data.get("framework", ""),
        input_summary=data.get("input_summary", ""),
        output_summary=data.get("output_summary", ""),
        confidence=float(data.get("confidence", 0.5)),
        evidence=evidence,
        evidence_gaps=data.get("evidence_gaps", []),
        dependencies=data.get("dependencies", []),
        disagreements=data.get("disagreements", []),
        threat_assessment=data.get("threat_assessment", ""),
        threat_target=data.get("threat_target", {}),
        threat_scores=data.get("threat_scores", {}),
        per_competitor_notes=data.get("per_competitor_notes", {}),
        method_findings=data.get("method_findings", []),
        response_actions=data.get("response_actions", []),
        expansion_likelihood=float(data.get("expansion_likelihood", 0)),
        report_sections=_normalize_report_sections(data.get("report_sections", {})),
        timestamp=now_iso(),
    )


def parse_agent_output(raw_text: str, role: str, node_id: str) -> AgentNodeOutput:
    """解析并校验模型输出，将类型错误统一转为可重试的结构错误。"""
    try:
        return _parse_agent_output(raw_text, role, node_id)
    except DeepSeekError:
        raise
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise DeepSeekSchemaError(
            f"DeepSeek response failed schema validation for role {role}: {exc}"
        ) from exc


async def call_and_parse(
    system_prompt: str,
    user_prompt: str,
    role: str,
    node_id: str,
    **kwargs,
) -> AgentNodeOutput:
    """执行模型调用、解析和校验，并返回统一的智能体输出。"""
    parse_attempts = int(kwargs.pop("parse_attempts", 2))
    last_error: DeepSeekSchemaError | None = None
    prompt = user_prompt
    for attempt in range(1, parse_attempts + 1):
        raw = await call_deepseek(system_prompt, prompt, **kwargs)
        try:
            return parse_agent_output(raw, role, node_id)
        except DeepSeekSchemaError as exc:
            last_error = exc
            if attempt >= parse_attempts:
                break
            prompt = (
                user_prompt
                + "\n\n上一次回复不是完整可解析 JSON。请只返回一个完整 JSON 对象，"
                  "不要使用 Markdown，不要省略字段，不要在字符串中截断内容。"
            )
    raise last_error or DeepSeekSchemaError("DeepSeek response could not be parsed.")
