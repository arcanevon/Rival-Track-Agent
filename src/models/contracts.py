"""Decision contract rules for the competitive threat workflow.

This module is the interface for validating and normalising the Threat Matrix,
Threat Assessment, and Response Action List emitted by agents.
"""

from src.models.output import AgentNodeOutput


THREAT_SCORE_DIMENSIONS = (
    "user_substitution",
    "capability_catch_up",
    "distribution",
    "strategic_expansion",
)

ACTION_REQUIRED_FIELDS = (
    "priority",
    "response_type",
    "related_threat_dimension",
    "competitor",
    "concrete_action",
)

METHOD_FINDING_REQUIRED_FIELDS = (
    "competitor",
    "criterion",
    "finding",
    "evidence_refs",
    "reasoning",
    "uncertainty",
    "mapped_dimensions",
)


def score_errors(scores: object) -> list[str]:
    if not isinstance(scores, dict):
        return ["scores must be an object"]
    errors: list[str] = []
    for dim in THREAT_SCORE_DIMENSIONS:
        value = scores.get(dim)
        if not isinstance(value, (int, float)) or not 0 <= value <= 100:
            errors.append(f"missing or invalid {dim}")
    overall = scores.get("overall")
    if not isinstance(overall, (int, float)) or not 0 <= overall <= 100:
        errors.append("missing or invalid overall")
    return errors


def expected_competitors_from_scores(*outputs: AgentNodeOutput) -> list[str]:
    names: list[str] = []
    for output in outputs:
        if not isinstance(output.threat_scores, dict):
            continue
        for key, value in output.threat_scores.items():
            if key in (*THREAT_SCORE_DIMENSIONS, "overall"):
                continue
            if isinstance(value, dict) and key not in names:
                names.append(key)
    return names


def validate_threat_matrix(
    output: AgentNodeOutput,
    expected_competitors: list[str] | None = None,
) -> list[str]:
    if not isinstance(output.threat_scores, dict) or not output.threat_scores:
        return ["missing threat_scores"]

    errors: list[str] = []
    matrix_keys = [
        key for key, value in output.threat_scores.items()
        if isinstance(value, dict) and key not in (*THREAT_SCORE_DIMENSIONS, "overall")
    ]
    if not matrix_keys:
        return ["threat_scores must be keyed by competitor name"]

    expected = expected_competitors or matrix_keys
    for name in expected:
        scores = output.threat_scores.get(name)
        if scores is None:
            lower_match = next(
                (value for key, value in output.threat_scores.items()
                 if key.lower() == name.lower()),
                None,
            )
            scores = lower_match
        score_issues = score_errors(scores)
        if score_issues:
            errors.append(f"{name}: {', '.join(score_issues)}")
    return errors


def validate_competitor_threat_assessment(
    output: AgentNodeOutput,
    expected_competitors: list[str] | None = None,
) -> list[str]:
    assessment = output.threat_assessment
    if not isinstance(assessment, dict) or not assessment:
        return ["threat_assessment must be an object keyed by competitor name"]

    errors: list[str] = []
    expected = expected_competitors or list(assessment.keys())
    for name in expected:
        value = assessment.get(name)
        if not isinstance(value, dict):
            errors.append(f"{name}: missing threat assessment object")
            continue
        for field in ("level", "score", "evidence_strength"):
            if value.get(field) in (None, ""):
                errors.append(f"{name}: missing {field}")
    return errors


def validate_response_actions(output: AgentNodeOutput) -> list[str]:
    if not isinstance(output.response_actions, list) or not output.response_actions:
        return ["missing response_actions"]
    errors: list[str] = []
    for idx, action in enumerate(output.response_actions, start=1):
        if not isinstance(action, dict):
            errors.append(f"action {idx} must be an object")
            continue
        for field in ACTION_REQUIRED_FIELDS:
            value = action.get(field)
            if value is None or value == "":
                errors.append(f"action {idx} missing {field}")
        priority = action.get("priority")
        if not isinstance(priority, (int, float)):
            errors.append(f"action {idx} priority must be numeric")
    return errors


def validate_method_findings(
    output: AgentNodeOutput,
    expected_competitors: list[str] | None = None,
) -> list[str]:
    """校验分析师是否留下了可供 QA 复审的方法推导记录。"""
    findings = output.method_findings
    if not isinstance(findings, list) or not findings:
        return ["missing method_findings"]

    errors: list[str] = []
    covered: set[str] = set()
    expected = set(expected_competitors or [])
    for index, item in enumerate(findings, start=1):
        if not isinstance(item, dict):
            errors.append(f"method finding {index} must be an object")
            continue
        for field in METHOD_FINDING_REQUIRED_FIELDS:
            if item.get(field) in (None, "", []):
                errors.append(f"method finding {index} missing {field}")
        competitor = str(item.get("competitor", ""))
        if competitor:
            covered.add(competitor)
        dimensions = item.get("mapped_dimensions")
        if not isinstance(item.get("evidence_refs"), list):
            errors.append(f"method finding {index} evidence_refs must be a list")
        if not isinstance(dimensions, list):
            errors.append(f"method finding {index} mapped_dimensions must be a list")
        else:
            invalid = [dim for dim in dimensions if dim not in THREAT_SCORE_DIMENSIONS]
            if invalid:
                errors.append(f"method finding {index} has invalid mapped_dimensions")
    for competitor in sorted(expected - covered):
        errors.append(f"{competitor}: missing method finding")
    return errors


def filter_decision_output(
    output: AgentNodeOutput,
    expected_competitors: list[str],
) -> AgentNodeOutput:
    """Keep model output scoped to the approved competitor set."""
    if not expected_competitors:
        return output

    expected_by_lower = {name.lower(): name for name in expected_competitors}
    updates: dict[str, object] = {}

    if isinstance(output.threat_scores, dict):
        updates["threat_scores"] = {
            expected_by_lower[str(key).lower()]: value
            for key, value in output.threat_scores.items()
            if str(key).lower() in expected_by_lower
        }

    if isinstance(output.threat_assessment, dict):
        updates["threat_assessment"] = {
            expected_by_lower[str(key).lower()]: value
            for key, value in output.threat_assessment.items()
            if str(key).lower() in expected_by_lower
        }

    if isinstance(output.per_competitor_notes, dict):
        updates["per_competitor_notes"] = {
            expected_by_lower[str(key).lower()]: value
            for key, value in output.per_competitor_notes.items()
            if str(key).lower() in expected_by_lower
        }

    if isinstance(output.method_findings, list):
        updates["method_findings"] = [
            item for item in output.method_findings
            if isinstance(item, dict)
            and str(item.get("competitor", "")).lower() in expected_by_lower
        ]

    if isinstance(output.response_actions, list):
        updates["response_actions"] = [
            action for action in output.response_actions
            if isinstance(action, dict)
            and str(action.get("competitor", "")).lower() in expected_by_lower
        ]

    return output.model_copy(update=updates)


def strip_analyst_overall_labels(output: AgentNodeOutput) -> AgentNodeOutput:
    """Analysts produce matrices and per-competitor reasoning, not final labels."""
    return output.model_copy(update={"threat_assessment": ""})


def strip_threat_target(
    output: AgentNodeOutput,
    threat_target: dict[str, object] | None,
) -> AgentNodeOutput:
    """Remove the threat target from scores/assessment if the model mistakenly included it."""
    target_name = (threat_target or {}).get("name", "")
    if not target_name:
        return output

    target_lower = target_name.lower()
    updates: dict[str, object] = {}

    if isinstance(output.threat_scores, dict):
        updates["threat_scores"] = {
            k: v for k, v in output.threat_scores.items()
            if k.lower() != target_lower
        }
    if isinstance(output.threat_assessment, dict):
        updates["threat_assessment"] = {
            k: v for k, v in output.threat_assessment.items()
            if k.lower() != target_lower
        }
    if isinstance(output.per_competitor_notes, dict):
        updates["per_competitor_notes"] = {
            k: v for k, v in output.per_competitor_notes.items()
            if k.lower() != target_lower
        }
    if isinstance(output.method_findings, list):
        updates["method_findings"] = [
            item for item in output.method_findings
            if not isinstance(item, dict)
            or str(item.get("competitor", "")).lower() != target_lower
        ]

    return output.model_copy(update=updates)


def matrix_disagreements(
    analyst_a: AgentNodeOutput,
    analyst_b: AgentNodeOutput,
    expected_competitors: list[str],
) -> list[dict]:
    disagreements: list[dict] = []
    if not isinstance(analyst_a.threat_scores, dict) or not isinstance(analyst_b.threat_scores, dict):
        return disagreements

    for name in expected_competitors:
        a_scores = analyst_a.threat_scores.get(name)
        b_scores = analyst_b.threat_scores.get(name)
        if not isinstance(a_scores, dict) or not isinstance(b_scores, dict):
            continue
        for dim in (*THREAT_SCORE_DIMENSIONS, "overall"):
            a_value = a_scores.get(dim)
            b_value = b_scores.get(dim)
            if not isinstance(a_value, (int, float)) or not isinstance(b_value, (int, float)):
                continue
            delta = abs(a_value - b_value)
            if delta >= 25:
                conflict_level = "high" if delta >= 40 else "medium"
                disagreements.append({
                    "target_node_id": "qa",
                    "competitor": name,
                    "dimension": dim,
                    "delta": round(delta / 100, 2),
                    "a_value": a_value,
                    "b_value": b_value,
                    "recommended_score": round((a_value + b_value) / 2),
                    "method_a": "VRIO",
                    "method_b": "SWOT",
                    "conflict_level": conflict_level,
                    "qa_reason": (
                        "A/B 在该维度分歧达到复核阈值；QA 需要按证据可信度、"
                        "前瞻信号和已发生结果进行辩证调和。"
                    ),
                })
    return disagreements
