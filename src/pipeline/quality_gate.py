"""QA Quality Gate 的判定与复审指标。

该模块把质量规则集中在一个内部 Interface 后面。DAG 只需要提交 QA 输出和
返工预算，即可得到下一跳、判定原因以及可展示的复审指标。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from src.models.contracts import (
    THREAT_SCORE_DIMENSIONS,
    expected_competitors_from_scores,
    score_errors,
    validate_competitor_threat_assessment,
    validate_threat_matrix,
)
from src.models.output import AgentNodeOutput, AgentStatus


QualityRoute = Literal["collect", "analyze", "write"]


@dataclass(frozen=True)
class QualityGateResult:
    """一次 Quality Gate 评估的完整结果。"""

    route: QualityRoute
    reason: str
    passed: bool
    forced_completion: bool
    metrics: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        """转换为可写入 LangGraph 状态和最终 JSON 的普通字典。"""
        return asdict(self)


def _matrix_completeness(output: AgentNodeOutput, competitors: list[str]) -> float:
    """计算具有完整五项分数的竞品行比例。"""
    expected = competitors or expected_competitors_from_scores(output)
    if not expected:
        return 0.0
    valid_rows = 0
    for competitor in expected:
        scores = output.threat_scores.get(competitor) if isinstance(output.threat_scores, dict) else None
        if not score_errors(scores):
            valid_rows += 1
    return round(valid_rows / len(expected), 3)


def evaluate_quality_gate(
    qa_output: AgentNodeOutput,
    competitors: list[str],
    *,
    rework_round: int,
    max_rework_rounds: int,
    tools_enabled: bool,
    previous_metrics: dict[str, object] | None = None,
    evidence_metrics: dict[str, object] | None = None,
    analyst_outputs: tuple[AgentNodeOutput, ...] = (),
) -> QualityGateResult:
    """评估 QA 结果，并在采集返工、分析返工和写作之间选择下一跳。"""
    expected = competitors or expected_competitors_from_scores(qa_output)
    matrix_errors = validate_threat_matrix(qa_output, expected)
    assessment_errors = validate_competitor_threat_assessment(qa_output, expected)
    structural_errors = [*matrix_errors, *assessment_errors]
    evidence_gap_count = len(qa_output.evidence_gaps)
    disagreement_count = len(qa_output.disagreements)
    actionable_disagreements = [
        item
        for item in qa_output.disagreements
        if isinstance(item, dict)
        and (
            item.get("conflict_level") in {"medium", "high"}
            or (
                isinstance(item.get("delta"), (int, float))
                and float(item["delta"]) >= 0.25
            )
        )
    ]
    actionable_disagreement_count = len(actionable_disagreements)
    completeness = _matrix_completeness(qa_output, expected)
    confidence = max(0.0, min(1.0, float(qa_output.confidence or 0.0)))
    evidence_metrics = evidence_metrics or {}
    evaluated_sources = int(evidence_metrics.get("evaluated_sources", 0) or 0)
    relevance_precision = float(evidence_metrics.get("precision_at_5", 0) or 0)
    claim_answer_rate = float(evidence_metrics.get("claim_answer_rate", 0) or 0)
    bad_domain_leakage = float(evidence_metrics.get("bad_domain_leakage", 0) or 0)
    relevance_problem = bool(
        evaluated_sources > 0
        and (relevance_precision < 0.5 or claim_answer_rate < 0.8 or bad_domain_leakage > 0)
    )

    # 两名分析师都应为每个竞品留下至少一条可审计的方法推导记录。
    expected_trace_count = len(expected) * len(analyst_outputs)
    expected_lower = {name.lower() for name in expected}
    traced_pairs = {
        (output.node_id, str(item.get("competitor", "")).lower())
        for output in analyst_outputs
        for item in output.method_findings
        if isinstance(item, dict)
        and str(item.get("competitor", "")).lower() in expected_lower
        and all(item.get(field) not in (None, "", []) for field in (
            "criterion", "finding", "evidence_refs", "reasoning", "uncertainty", "mapped_dimensions"
        ))
        and isinstance(item.get("evidence_refs"), list)
        and isinstance(item.get("mapped_dimensions"), list)
        and all(dim in THREAT_SCORE_DIMENSIONS for dim in item["mapped_dimensions"])
    }
    method_trace_coverage = (
        round(len(traced_pairs) / expected_trace_count, 3)
        if expected_trace_count else 1.0
    )
    method_trace_problem = method_trace_coverage < 1.0

    expected_scale = max(1, len(expected) * 2)
    evidence_readiness = max(0.0, 1.0 - evidence_gap_count / expected_scale)
    agreement = max(0.0, 1.0 - actionable_disagreement_count / expected_scale)
    relevance_factor = relevance_precision if evaluated_sources else 0.7
    quality_score = round(
        100 * (
            0.30 * completeness
            + 0.20 * confidence
            + 0.15 * evidence_readiness
            + 0.10 * agreement
            + 0.25 * relevance_factor
        ),
        1,
    )
    previous_score = (previous_metrics or {}).get("quality_score")
    score_delta = (
        round(quality_score - float(previous_score), 1)
        if isinstance(previous_score, (int, float))
        else None
    )
    metrics: dict[str, object] = {
        "evaluation_round": rework_round,
        "quality_score": quality_score,
        "score_delta": score_delta,
        "matrix_completeness": completeness,
        "qa_confidence": confidence,
        "evidence_gap_count": evidence_gap_count,
        "disagreement_count": disagreement_count,
        "actionable_disagreement_count": actionable_disagreement_count,
        "structural_error_count": len(structural_errors),
        "structural_errors": structural_errors[:8],
        "evaluated_sources": evaluated_sources,
        "relevance_precision_at_5": relevance_precision,
        "claim_answer_rate": claim_answer_rate,
        "bad_domain_leakage": bad_domain_leakage,
        "method_trace_coverage": method_trace_coverage,
    }

    has_problem = bool(
        qa_output.status == AgentStatus.ERROR
        or structural_errors
        or evidence_gap_count
        or actionable_disagreement_count
        or relevance_problem
        or method_trace_problem
    )
    if not has_problem:
        return QualityGateResult("write", "质量门通过，进入报告撰写。", True, False, metrics)

    if qa_output.status == AgentStatus.ERROR:
        return QualityGateResult(
            "write", "QA 未产生有效结果，停止自动返工并交由 Writer 降级处理。",
            False, True, metrics,
        )

    if rework_round >= max(0, max_rework_rounds):
        return QualityGateResult(
            "write", f"已达到最大返工次数 {max_rework_rounds}，保留未解决问题并继续写作。",
            False, True, metrics,
        )

    if structural_errors:
        return QualityGateResult(
            "analyze", "威胁矩阵或逐竞品判断不完整，返回双分析 Agent 重新计算。",
            False, False, metrics,
        )

    if relevance_problem and tools_enabled:
        return QualityGateResult(
            "collect",
            "已抓取证据的相关性、Claim 可回答率或低质域名泄漏未达门槛，返回采集阶段替换来源。",
            False,
            False,
            metrics,
        )

    if evidence_gap_count and tools_enabled:
        return QualityGateResult(
            "collect", f"仍有 {evidence_gap_count} 个证据缺口，返回工具规划和采集 Agent。",
            False, False, metrics,
        )

    if method_trace_problem:
        return QualityGateResult(
            "analyze",
            "分析师的方法推导记录不完整，返回双分析 Agent 补充准则、证据、推导、不确定性和影响维度。",
            False, False, metrics,
        )

    if actionable_disagreement_count:
        return QualityGateResult(
            "analyze", f"仍有 {actionable_disagreement_count} 个显著方法分歧，返回双分析 Agent 复核。",
            False, False, metrics,
        )

    limitation = (
        "证据相关性指标未达门槛"
        if relevance_problem
        else "存在证据缺口"
    )
    return QualityGateResult(
        "write", f"{limitation}，但当前任务未启用联网工具；带限制说明进入写作。",
        False, True, metrics,
    )
