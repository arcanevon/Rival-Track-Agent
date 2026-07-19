"""Pipeline agent node implementations.

Each run_* function corresponds to one agent in the 5-agent DAG
(Collector → Analyst A + Analyst B → QA → Writer).  Nodes call the
DeepSeek API, broadcast state updates via WebSocket, and return
structured AgentNodeOutput objects.
"""

import json
import logging
from pathlib import Path

from src.agents.prompts import (
    COLLECTOR_SYSTEM, COLLECTOR_USER,
    ANALYST_A_SYSTEM, ANALYST_A_USER,
    ANALYST_B_SYSTEM, ANALYST_B_USER,
    QA_SYSTEM, QA_USER,
    QA_REFLECTION_SYSTEM, QA_REFLECTION_USER,
    WRITER_SYSTEM, WRITER_USER,
)
from src.agents.analysis_lenses import format_analysis_lenses
from src.client.deepseek import call_and_parse, DeepSeekError
from src.intake.plan import build_evidence_acquisition_plans, build_evidence_gaps
from src.intake.quality import build_source_quality_context
from src.models.output import AgentNodeOutput, AgentStatus, now_iso
from src.models.contracts import (
    expected_competitors_from_scores,
    filter_decision_output,
    matrix_disagreements,
    strip_analyst_overall_labels,
    strip_threat_target,
    validate_competitor_threat_assessment,
    validate_method_findings,
    validate_response_actions,
    validate_threat_matrix,
)
from src.server.ws import broadcast_node_update, broadcast_error

from .cache import _cache_with_evidence_plans
from .coverage import (
    _ensure_collector_evidence_coverage,
    _collector_confidence_cap,
    normalize_output_evidence_ids,
)
from .format import _format_cache_for_collector, _format_threat_target, _extract_shared_source_digest

logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


# ── 辅助函数 ────────────────────────────────────────────────────────────

def _log_output(output: AgentNodeOutput):
    """Append an agent output to the JSONL log file for post-mortem debugging."""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        log_path = LOG_DIR / "agent-outputs.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(output.model_dump_json() + "\n")
    except OSError:
        logger.warning("Failed to write agent output log", exc_info=True)


def _make_running_output(node_id: str, role: str, label: str, framework: str = "",
                         input_summary: str = "",
                         dependencies: list[str] | None = None) -> AgentNodeOutput:
    return AgentNodeOutput(
        node_id=node_id, role=role, status="running",
        label=label, framework=framework,
        input_summary=input_summary,
        dependencies=dependencies or [],
        timestamp=now_iso(),
    )


def _error_output(node_id: str, role: str, label: str, summary: str,
                  dependencies: list[str]) -> AgentNodeOutput:
    return AgentNodeOutput(
        node_id=node_id,
        role=role,
        status=AgentStatus.ERROR,
        label=label,
        output_summary=summary,
        dependencies=dependencies,
        timestamp=now_iso(),
    )


def _repair_prompt(user_prompt: str, errors: list[str], required: str) -> str:
    return (
        f"{user_prompt}\n\n"
        "=== SCHEMA REPAIR REQUEST ===\n"
        "Your previous JSON did not satisfy the required decision-table schema.\n"
        f"Missing/invalid fields: {json.dumps(errors, ensure_ascii=False)}\n"
        f"Required output: {required}\n"
        "Return one valid JSON object only. Do not explain outside JSON."
    )


def _friendly_exception_message(exc: Exception) -> str:
    raw = str(exc)
    lowered = raw.lower()
    if "service_unavailable" in lowered or "service is too busy" in lowered or "503" in lowered:
        return "模型服务繁忙，DeepSeek 返回 503。请稍后重试，或临时切换模型服务。"
    if "timeout" in lowered or "timed out" in lowered:
        return "模型服务响应超时。请稍后重试，或减少本轮竞品/证据数量。"
    return raw


# ═══════════════════════════════════════════════════════════════════════════
# 采集 Agent
# ═══════════════════════════════════════════════════════════════════════════

async def run_collector(track: str, competitors: list[str],
                        cache_data: dict[str, dict],
                        threat_target: dict[str, object] | None = None,
                        long_term_memory: str = "",
                        tool_observations: str = "") -> AgentNodeOutput:
    """采集并整理缓存、历史记忆和本轮工具观察。"""
    node_id = "collector"
    output = _make_running_output(node_id, "collector", "采集 Agent", dependencies=[])
    await broadcast_node_update(output)

    if not cache_data:
        output.status = AgentStatus.ERROR
        output.output_summary = "No cache data found."
        await broadcast_node_update(output)
        return output

    cache_for_prompt = _cache_with_evidence_plans(cache_data, track)
    user_prompt = COLLECTOR_USER.format(
        track=track,
        competitors=", ".join(competitors),
        cache_data=_format_cache_for_collector(cache_for_prompt),
        threat_target=_format_threat_target(threat_target, track),
        source_quality_context=build_source_quality_context(cache_for_prompt),
        evidence_acquisition_plans=json.dumps(
            build_evidence_acquisition_plans(cache_for_prompt, track),
            ensure_ascii=False,
            indent=2,
        ),
        long_term_memory=long_term_memory or "（没有可用的历史分析记忆）",
        tool_observations=tool_observations or "（本轮没有调用工具）",
    )

    try:
        result = await call_and_parse(COLLECTOR_SYSTEM, user_prompt, "collector", node_id,
                                      max_tokens=16384, timeout=120)
        result = _ensure_collector_evidence_coverage(result, competitors, cache_for_prompt)
        result = normalize_output_evidence_ids(result, cache_for_prompt)
        result.evidence_gaps = build_evidence_gaps(cache_for_prompt, track)
        cap = _collector_confidence_cap(cache_for_prompt)
        if result.confidence > cap:
            result.confidence = cap
        result.dependencies = []
        await broadcast_node_update(result)
        _log_output(result)
        logger.info("Collector completed (confidence=%.2f, %d evidence items)",
                     result.confidence, len(result.evidence))
        return result
    except DeepSeekError as e:
        message = _friendly_exception_message(e)
        output.status = AgentStatus.ERROR
        output.output_summary = f"Collector failed: {message}"
        await broadcast_node_update(output)
        await broadcast_error(message, node_id)
        logger.error("Collector failed: %s", message)
        return output


# ═══════════════════════════════════════════════════════════════════════════
# 分析 Agent A（VRIO）
# ═══════════════════════════════════════════════════════════════════════════

async def run_analyst_a(collector_output: AgentNodeOutput,
                        cache_data: dict[str, dict],
                        threat_target: dict[str, object] | None = None,
                        rework_feedback: str = "",
                        track: str = "") -> AgentNodeOutput:
    """分析 Agent A：使用 VRIO 检查共享证据。"""
    node_id = "analyst-a"
    output = _make_running_output(
        node_id, "analyst-a", "能力持久性分析 Agent · VRIO",
        framework="VRIO",
        input_summary="Received the shared collector evidence set",
        dependencies=["collector"],
    )
    await broadcast_node_update(output)

    user_prompt = ANALYST_A_USER.format(
        collector_data=json.dumps(collector_output.model_dump(), ensure_ascii=False, indent=2),
        threat_target=_format_threat_target(threat_target),
        analysis_lenses=format_analysis_lenses(track, threat_target),
    )
    cache_for_prompt = _cache_with_evidence_plans(cache_data)
    shared_data = _extract_shared_source_digest(cache_for_prompt)
    user_prompt += (
        "\n\n=== SHARED SOURCE DIGEST "
        "(same evidence set as Analyst B; do not filter by source type) ===\n"
        f"{shared_data[:8000]}"
    )
    if rework_feedback:
        user_prompt += f"\n\n{rework_feedback}\n请针对上述问题重新检查 VRIO 判断和四维分数。"

    try:
        result = await call_and_parse(ANALYST_A_SYSTEM, user_prompt, "analyst-a", node_id,
                                      max_tokens=16384, timeout=120)
        result = strip_analyst_overall_labels(result)
        result = normalize_output_evidence_ids(result, cache_for_prompt)
        trace_errors = validate_method_findings(result, expected_competitors_from_scores(result))
        if trace_errors:
            logger.warning("Analyst A method trace incomplete: %s", trace_errors[:8])
        result.dependencies = ["collector"]
        await broadcast_node_update(result)
        _log_output(result)
        logger.info("Analyst A completed (confidence=%.2f, %d evidence items)",
                     result.confidence, len(result.evidence))
        return result
    except DeepSeekError as e:
        message = _friendly_exception_message(e)
        output.status = AgentStatus.ERROR
        output.output_summary = f"Analyst A failed: {message}"
        await broadcast_node_update(output)
        await broadcast_error(message, node_id)
        logger.error("Analyst A failed: %s", message)
        return output


# ═══════════════════════════════════════════════════════════════════════════
# 分析 Agent B（SWOT）
# ═══════════════════════════════════════════════════════════════════════════

async def run_analyst_b(collector_output: AgentNodeOutput,
                        cache_data: dict[str, dict],
                        threat_target: dict[str, object] | None = None,
                        rework_feedback: str = "",
                        track: str = "") -> AgentNodeOutput:
    """分析 Agent B：使用 SWOT 检查共享证据。"""
    node_id = "analyst-b"
    output = _make_running_output(
        node_id, "analyst-b", "市场动态与用户替代分析 Agent · SWOT",
        framework="SWOT Analysis",
        input_summary="Received the shared collector evidence set",
        dependencies=["collector"],
    )
    await broadcast_node_update(output)

    user_prompt = ANALYST_B_USER.format(
        collector_data=json.dumps(collector_output.model_dump(), ensure_ascii=False, indent=2),
        threat_target=_format_threat_target(threat_target),
        analysis_lenses=format_analysis_lenses(track, threat_target),
    )
    cache_for_prompt = _cache_with_evidence_plans(cache_data)
    shared_data = _extract_shared_source_digest(cache_for_prompt)
    user_prompt += (
        "\n\n=== SHARED SOURCE DIGEST "
        "(same evidence set as Analyst A; do not filter by source type) ===\n"
        f"{shared_data[:8000]}"
    )
    if rework_feedback:
        user_prompt += f"\n\n{rework_feedback}\n请针对上述问题重新检查 SWOT 判断和四维分数。"

    try:
        result = await call_and_parse(ANALYST_B_SYSTEM, user_prompt, "analyst-b", node_id,
                                      max_tokens=16384, timeout=120)
        result = strip_analyst_overall_labels(result)
        result = normalize_output_evidence_ids(result, cache_for_prompt)
        trace_errors = validate_method_findings(result, expected_competitors_from_scores(result))
        if trace_errors:
            logger.warning("Analyst B method trace incomplete: %s", trace_errors[:8])
        result.dependencies = ["collector"]
        await broadcast_node_update(result)
        _log_output(result)
        logger.info("Analyst B completed (confidence=%.2f, %d evidence items)",
                     result.confidence, len(result.evidence))
        return result
    except DeepSeekError as e:
        message = _friendly_exception_message(e)
        output.status = AgentStatus.ERROR
        output.output_summary = f"Analyst B failed: {message}"
        await broadcast_node_update(output)
        await broadcast_error(message, node_id)
        logger.error("Analyst B failed: %s", message)
        return output


# ═══════════════════════════════════════════════════════════════════════════
# 质检 Agent（交叉验证与反思）
# ═══════════════════════════════════════════════════════════════════════════

async def run_qa(analyst_a: AgentNodeOutput,
                 analyst_b: AgentNodeOutput,
                 threat_target: dict[str, object] | None = None,
                 expected_competitors: list[str] | None = None,
                 source_quality_context: str = "") -> AgentNodeOutput:
    """质检 Agent：交叉验证双分析结果，并执行一次 Reflection。"""
    node_id = "qa"
    output = _make_running_output(
        node_id, "qa", "质检 Agent", framework="交叉验证",
        input_summary="Received Analyst A and Analyst B outputs",
        dependencies=["analyst-a", "analyst-b"],
    )
    await broadcast_node_update(output)

    user_prompt = QA_USER.format(
        analyst_a_output=json.dumps(analyst_a.model_dump(), ensure_ascii=False, indent=2),
        analyst_b_output=json.dumps(analyst_b.model_dump(), ensure_ascii=False, indent=2),
        threat_target=_format_threat_target(threat_target),
        source_quality_context=source_quality_context or "(no source quality context available)",
    )

    expected_competitors = expected_competitors or expected_competitors_from_scores(analyst_a, analyst_b)

    try:
        result = await call_and_parse(QA_SYSTEM, user_prompt, "qa", node_id, max_tokens=16384, timeout=120)
        result = strip_threat_target(result, threat_target)
        result = filter_decision_output(result, expected_competitors)
        matrix_errors = (
            validate_threat_matrix(result, expected_competitors)
            + validate_competitor_threat_assessment(result, expected_competitors)
        )
        if matrix_errors:
            logger.warning("QA threat matrix invalid, retrying once: %s", matrix_errors)
            repair_prompt = _repair_prompt(
                user_prompt,
                matrix_errors,
                "threat_scores keyed by every competitor, each with four dimensions and overall",
            )
            result = await call_and_parse(QA_SYSTEM, repair_prompt, "qa", node_id, max_tokens=16384, timeout=120)
            result = strip_threat_target(result, threat_target)
            result = filter_decision_output(result, expected_competitors)
            matrix_errors = (
                validate_threat_matrix(result, expected_competitors)
                + validate_competitor_threat_assessment(result, expected_competitors)
            )
            if matrix_errors:
                failed = _error_output(
                    node_id, "qa", "质检 Agent",
                    "威胁矩阵生成失败",
                    ["analyst-a", "analyst-b"],
                )
                await broadcast_node_update(failed)
                await broadcast_error("威胁矩阵生成失败: " + "; ".join(matrix_errors), node_id)
                _log_output(failed)
                return failed

        reflection_prompt = QA_REFLECTION_USER.format(
            analyst_a_output=json.dumps(analyst_a.model_dump(), ensure_ascii=False, indent=2),
            analyst_b_output=json.dumps(analyst_b.model_dump(), ensure_ascii=False, indent=2),
            threat_target=_format_threat_target(threat_target),
            source_quality_context=source_quality_context or "(no source quality context available)",
            qa_draft=json.dumps(result.model_dump(), ensure_ascii=False, indent=2),
        )
        try:
            reflected = await call_and_parse(
                QA_REFLECTION_SYSTEM,
                reflection_prompt,
                "qa",
                node_id,
                max_tokens=16384,
                timeout=120,
            )
            reflected = strip_threat_target(reflected, threat_target)
            reflected = filter_decision_output(reflected, expected_competitors)
            reflection_errors = (
                validate_threat_matrix(reflected, expected_competitors)
                + validate_competitor_threat_assessment(reflected, expected_competitors)
            )
            if reflection_errors:
                logger.warning("QA Reflection 结果无效，保留初稿：%s", reflection_errors)
            else:
                if not reflected.evidence_gaps:
                    reflected.evidence_gaps = result.evidence_gaps
                result = reflected
        except DeepSeekError as reflection_error:
            logger.warning("QA Reflection 调用失败，保留已校验的初稿：%s", reflection_error)
        result.dependencies = ["analyst-a", "analyst-b"]

        disagreements = matrix_disagreements(analyst_a, analyst_b, expected_competitors)

        if disagreements:
            result.disagreements = disagreements
        elif analyst_a.output_summary != analyst_b.output_summary:
            result.disagreements = [
                {"target_node_id": "analyst-a", "dimension": "分析结论", "delta": 0.2},
                {"target_node_id": "analyst-b", "dimension": "分析结论", "delta": 0.2},
            ]

        await broadcast_node_update(result)
        _log_output(result)
        logger.info("QA completed (confidence=%.2f)", result.confidence)
        return result
    except DeepSeekError as e:
        message = _friendly_exception_message(e)
        output.status = AgentStatus.ERROR
        output.output_summary = f"QA failed: {message}"
        await broadcast_node_update(output)
        await broadcast_error(message, node_id)
        logger.error("QA failed: %s", message)
        return output


# ═══════════════════════════════════════════════════════════════════════════
# 撰写 Agent
# ═══════════════════════════════════════════════════════════════════════════

async def run_writer(qa_output: AgentNodeOutput,
                     threat_target: dict[str, object] | None = None,
                     source_quality_context: str = "") -> AgentNodeOutput:
    """撰写 Agent：把质检结论整理为报告和行动清单。"""
    node_id = "writer"
    output = _make_running_output(
        node_id, "writer", "撰写 Agent", framework="综合分析",
        input_summary="Received QA verdict",
        dependencies=["qa"],
    )
    await broadcast_node_update(output)

    if qa_output.status == AgentStatus.ERROR:
        failed = _error_output(
            node_id, "writer", "Decision Writer",
            "行动建议生成失败",
            ["qa"],
        )
        await broadcast_node_update(failed)
        await broadcast_error("行动建议生成失败: QA 未生成有效威胁矩阵", node_id)
        _log_output(failed)
        return failed

    user_prompt = WRITER_USER.format(
        qa_output=json.dumps(qa_output.model_dump(), ensure_ascii=False, indent=2),
        threat_target=_format_threat_target(threat_target),
        source_quality_context=source_quality_context or "(no source quality context available)",
    )

    expected_competitors = list(qa_output.threat_scores.keys()) if isinstance(qa_output.threat_scores, dict) else []

    try:
        result = await call_and_parse(WRITER_SYSTEM, user_prompt, "writer", node_id, max_tokens=16384, timeout=120)
        result = strip_threat_target(result, threat_target)
        result = filter_decision_output(result, expected_competitors)
        writer_errors = (
            validate_threat_matrix(result, expected_competitors)
            + validate_competitor_threat_assessment(result, expected_competitors)
            + validate_response_actions(result)
        )
        if writer_errors:
            logger.warning("Writer decision output invalid, retrying once: %s", writer_errors)
            repair_prompt = _repair_prompt(
                user_prompt,
                writer_errors,
                "threat_scores plus response_actions; every action needs priority, type, dimension, competitor, and concrete_action",
            )
            result = await call_and_parse(WRITER_SYSTEM, repair_prompt, "writer", node_id, max_tokens=16384, timeout=120)
            result = strip_threat_target(result, threat_target)
            result = filter_decision_output(result, expected_competitors)
            writer_errors = (
                validate_threat_matrix(result, expected_competitors)
                + validate_competitor_threat_assessment(result, expected_competitors)
                + validate_response_actions(result)
            )
            if writer_errors:
                failed = _error_output(
                    node_id, "writer", "Decision Writer",
                    "行动建议生成失败",
                    ["qa"],
                )
                await broadcast_node_update(failed)
                await broadcast_error("行动建议生成失败: " + "; ".join(writer_errors), node_id)
                _log_output(failed)
                return failed
        result.dependencies = ["qa"]
        await broadcast_node_update(result)
        _log_output(result)
        logger.info("Writer completed (confidence=%.2f)", result.confidence)
        return result
    except DeepSeekError as e:
        message = _friendly_exception_message(e)
        output.status = AgentStatus.ERROR
        output.output_summary = f"Writer failed: {message}"
        await broadcast_node_update(output)
        await broadcast_error(message, node_id)
        logger.error("Writer failed: %s", message)
        return output
