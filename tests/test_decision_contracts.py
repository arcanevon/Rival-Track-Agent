"""Decision-table contract tests for the competitive threat workflow."""

from src.models.output import AgentNodeOutput
from src.agents.prompts import (
    ANALYST_A_SYSTEM,
    ANALYST_A_USER,
    ANALYST_B_SYSTEM,
    ANALYST_B_USER,
    COLLECTOR_SYSTEM,
    COLLECTOR_USER,
    QA_SYSTEM,
    QA_USER,
    WRITER_SYSTEM,
    WRITER_USER,
)
from src.pipeline.coverage import (
    _collector_confidence_cap,
    _ensure_collector_evidence_coverage,
)
from src.pipeline.format import _extract_shared_source_digest, _format_cache_for_collector
from src.intake.enrich import build_cache_from_user_data
from src.models.contracts import (
    filter_decision_output,
    matrix_disagreements,
    strip_analyst_overall_labels,
    validate_competitor_threat_assessment,
    validate_method_findings,
    validate_response_actions,
    validate_threat_matrix,
)
from src.main import _auto_discover_competitor_inputs, _auto_discover_competitor_names


def test_threat_matrix_requires_competitor_keyed_four_dimension_scores():
    node = AgentNodeOutput(
        node_id="qa",
        role="qa",
        threat_scores={
            "Competitor A": {
                "user_substitution": 71,
                "capability_catch_up": 58,
                "distribution": 64,
                "strategic_expansion": 49,
                "overall": 61,
            }
        },
    )

    assert validate_threat_matrix(node, ["Competitor A"]) == []


def test_threat_matrix_rejects_flat_or_incomplete_scores():
    flat = AgentNodeOutput(
        node_id="qa",
        role="qa",
        threat_scores={"user_substitution": 71, "overall": 71},
    )
    missing_dim = AgentNodeOutput(
        node_id="qa",
        role="qa",
        threat_scores={"Competitor A": {"user_substitution": 71, "overall": 71}},
    )

    assert validate_threat_matrix(flat)
    assert validate_threat_matrix(missing_dim, ["Competitor A"])


def test_response_actions_require_issue_ready_fields():
    node = AgentNodeOutput(
        node_id="writer",
        role="writer",
        response_actions=[
            {
                "priority": 88,
                "response_type": "product",
                "related_threat_dimension": "user_substitution",
                "competitor": "Competitor A",
                "concrete_action": "Open an issue to benchmark the top substitution workflow.",
            }
        ],
    )

    assert validate_response_actions(node) == []


def test_collector_cache_format_keeps_later_competitors_visible():
    cache = {
        f"Competitor {i}": {
            "track": "test",
            "official_sources": [{
                "url": f"https://example.com/{i}",
                "label": f"Official {i}",
                "scraped_text": "x" * 3000,
            }],
        }
        for i in range(5)
    }

    formatted = _format_cache_for_collector(cache, max_chars=9000)

    assert "## Competitor 0" in formatted
    assert "## Competitor 4" in formatted
    assert "Official 4" in formatted


def test_collector_cache_format_includes_quality_and_acquisition_plan():
    cache = build_cache_from_user_data([{
        "company": "Cursor",
        "official_sources": [{
            "url": "https://cursor.com/",
            "label": "Cursor official",
            "direct_evidence": True,
            "authority": "high",
            "scraped_text": "Readable official product text. " * 12,
        }],
        "community_sources": [],
    }], "AI coding assistant")

    formatted = _format_cache_for_collector(cache, max_chars=9000)
    digest = _extract_shared_source_digest(cache)

    assert "source_coverage" in formatted
    assert "evidence_acquisition_plan" in formatted
    assert "quality_score" in formatted
    assert "usable_for_scoring" in digest
    assert "needed_slots" in digest


def test_collector_confidence_cap_reflects_weak_source_coverage():
    cache = build_cache_from_user_data([{
        "company": "小米手表/手环",
        "official_sources": [{
            "url": "https://www.usbmi.com/manual/xiaomi-watch.html",
            "label": "小米智能手表说明书-USB迷专注于互联网分享",
            "direct_evidence": True,
            "authority": "medium",
            "fetch_method": "html_parser",
            "evidence_status": "strong_text",
            "scraped_text": "电影迷 专注于电影,影评，解说，视频,免费分享。网页整改中。" * 8,
        }],
    }], "智能手表")

    assert _collector_confidence_cap(cache) <= 0.55


def test_collector_evidence_coverage_adds_refs_for_omitted_competitors():
    node = AgentNodeOutput(
        node_id="collector",
        role="collector",
        evidence=[],
        output_summary="collector summary",
    )
    cache = {
        "A": {"official_sources": [{"url": "https://a.example", "label": "A official", "scraped_text": "A text"}]},
        "B": {"official_sources": [{"url": "https://b.example", "label": "B official", "scraped_text": "B text"}]},
        "C": {"official_sources": [{"url": "https://c.example", "label": "C official", "scraped_text": "C text"}]},
    }

    result = _ensure_collector_evidence_coverage(node, ["A", "B", "C"], cache)

    labels = [e.source_label for e in result.evidence]
    assert any(label.startswith("A ·") for label in labels)
    assert any(label.startswith("B ·") for label in labels)
    assert any(label.startswith("C ·") for label in labels)


def test_auto_discovery_builds_three_evidence_insufficient_candidates():
    competitors = _auto_discover_competitor_inputs("Our Product", "AI代码助手", limit=3)

    assert 1 <= len(competitors) <= 3
    assert all(item["metadata"]["discovered_by_agent"] for item in competitors)
    assert all(item["metadata"]["evidence_insufficient"] for item in competitors)


def test_auto_discovery_keeps_default_candidates_in_same_track():
    names = _auto_discover_competitor_names("OpenAI Codex", "AI代码助手", limit=3)

    assert 1 <= len(names) <= 3
    assert all("direct substitute" not in name for name in names)


def test_auto_discovery_uses_product_subdomain_before_broad_track():
    product = "\u5c0f\u9a6c\u667a\u884c"
    track = "\u65b0\u80fd\u6e90\u6c7d\u8f66"
    names = _auto_discover_competitor_names(product, track, limit=3)

    assert names == ["\u6587\u8fdc\u77e5\u884c WeRide", "\u767e\u5ea6 Apollo", "Waymo"]
    assert track not in names


def test_auto_discovery_when_only_product_name_is_given():
    names = _auto_discover_competitor_names("GitHub Copilot", "", limit=3)

    assert names
    assert "GitHub Copilot" not in names
    assert "Cursor" in names


def test_auto_discovery_for_huawei_phone_uses_real_phone_competitors():
    product = "\u534e\u4e3a\u624b\u673a"
    names = _auto_discover_competitor_names(product, product, limit=3)

    assert names == ["\u82f9\u679c iPhone", "\u4e09\u661f Galaxy", "\u5c0f\u7c73\u624b\u673a"]
    assert all(product not in name for name in names)
    assert all("direct substitute" not in name for name in names)


def test_decision_output_filters_model_invented_target_rows():
    product = "\u534e\u4e3a\u624b\u673a"
    expected = ["\u82f9\u679c iPhone", "\u4e09\u661f Galaxy", "\u5c0f\u7c73\u624b\u673a"]
    node = AgentNodeOutput(
        node_id="qa",
        role="qa",
        threat_scores={
            "\u82f9\u679c iPhone": {
                "user_substitution": 60,
                "capability_catch_up": 20,
                "distribution": 70,
                "strategic_expansion": 30,
                "overall": 45,
            },
            f"{product} (direct substitute)": {
                "user_substitution": 85,
                "capability_catch_up": 90,
                "distribution": 75,
                "strategic_expansion": 80,
                "overall": 83,
            },
        },
        threat_assessment={
            "\u82f9\u679c iPhone": {"level": "\u4e2d", "score": 45, "evidence_strength": "\u8f83\u5145\u5206"},
            f"{product} (direct substitute)": {"level": "\u9ad8", "score": 83, "evidence_strength": "\u4f2a\u9020"},
        },
        per_competitor_notes={
            "\u82f9\u679c iPhone": "valid",
            f"{product} (direct substitute)": "invalid self-target",
        },
        response_actions=[
            {
                "priority": 90,
                "response_type": "product",
                "related_threat_dimension": "user_substitution",
                "competitor": f"{product} (direct substitute)",
                "concrete_action": "Invalid self-target action.",
            },
            {
                "priority": 80,
                "response_type": "product",
                "related_threat_dimension": "distribution",
                "competitor": "\u82f9\u679c iPhone",
                "concrete_action": "Benchmark iPhone channel switching pressure.",
            },
        ],
    )

    filtered = filter_decision_output(node, expected)

    assert list(filtered.threat_scores.keys()) == ["\u82f9\u679c iPhone"]
    assert list(filtered.threat_assessment.keys()) == ["\u82f9\u679c iPhone"]
    assert list(filtered.per_competitor_notes.keys()) == ["\u82f9\u679c iPhone"]
    assert len(filtered.response_actions) == 1
    assert filtered.response_actions[0]["competitor"] == "\u82f9\u679c iPhone"


def test_empty_competitor_name_only_input_gets_evidence_leads():
    cache = build_cache_from_user_data([
        {
            "company": "\u539f\u795e",
            "official_sources": [{"url": "", "label": "\u539f\u795e", "scraped_text": "\u539f\u795e"}],
            "community_sources": [],
        }
    ], "\u4e8c\u6b21\u5143\u6e38\u620f")

    official = cache["\u539f\u795e"]["official_sources"]
    community = cache["\u539f\u795e"]["community_sources"]
    leading = cache["\u539f\u795e"]["leading_sources"]
    candidate_sources = cache["\u539f\u795e"]["metadata"]["candidate_sources"]

    assert any(src["url"] == "https://ys.mihoyo.com/" for src in official)
    assert any("TapTap" in src["label"] for src in community)
    assert any("\u524d\u77bb\u4fe1\u53f7" in src["label"] for src in candidate_sources)
    assert any("positioning" in src["scraped_text"] for src in official)
    assert any("leading_indicators" in src["scraped_text"] for src in candidate_sources)
    assert all(src.get("candidate_only") for src in official)


def test_analyst_prompts_do_not_emit_final_threat_assessment():
    assert '"threat_assessment"' not in ANALYST_A_SYSTEM
    assert '"threat_assessment"' not in ANALYST_B_SYSTEM
    assert "Do NOT output threat_assessment" in ANALYST_A_SYSTEM
    assert "Do NOT output threat_assessment" in ANALYST_B_SYSTEM
    assert '"threat_assessment"' in QA_SYSTEM
    assert '"threat_assessment"' in WRITER_SYSTEM
    assert "per_competitor_notes" in ANALYST_A_SYSTEM
    assert "per_competitor_notes" in ANALYST_B_SYSTEM
    assert '"q"' in ANALYST_A_SYSTEM and '"t"' in ANALYST_A_SYSTEM
    assert "VRIO" in ANALYST_A_SYSTEM
    assert "Porter" not in ANALYST_A_SYSTEM
    assert "SWOT" in ANALYST_B_SYSTEM
    assert "Operational VRIO procedure" in ANALYST_A_SYSTEM
    assert "Operational market-dynamics/SWOT procedure" in ANALYST_B_SYSTEM
    assert "method_findings" in ANALYST_A_SYSTEM
    assert "method_findings" in ANALYST_B_SYSTEM
    assert "preset optimistic or pessimistic persona" in ANALYST_A_SYSTEM
    assert "preset optimistic or pessimistic persona" in ANALYST_B_SYSTEM
    assert "{analysis_lenses}" in ANALYST_A_USER
    assert "{analysis_lenses}" in ANALYST_B_USER


def test_collector_and_qa_prompts_use_obcl_sources_and_dialectic_reconciliation():
    assert "O/B/C/L" in COLLECTOR_SYSTEM
    assert "Leading Indicators" in COLLECTOR_SYSTEM
    assert "source_tier\": \"official|benchmark|community|leading\"" in COLLECTOR_SYSTEM
    assert "source_quality" in COLLECTOR_SYSTEM
    assert "evidence_acquisition_plan" in COLLECTOR_SYSTEM
    assert "evidence_gaps" in COLLECTOR_SYSTEM
    assert "{source_quality_context}" in COLLECTOR_USER
    assert "{evidence_acquisition_plans}" in COLLECTOR_USER
    assert "source_quality" in ANALYST_A_SYSTEM
    assert "evidence_acquisition_plan" in ANALYST_B_SYSTEM
    assert "evidence-dialectic reconciliation strategy" in QA_SYSTEM
    assert "证据辩证调和策略" in QA_SYSTEM
    assert "L=Leading Indicator" in QA_SYSTEM
    assert "VRIO" in QA_SYSTEM and "SWOT" in QA_SYSTEM
    assert "source_quality" in QA_SYSTEM
    assert "source_coverage" in QA_SYSTEM
    assert "Audit method_findings" in QA_SYSTEM
    assert "{source_quality_context}" in QA_USER
    assert "source_quality" in WRITER_SYSTEM
    assert "evidence_gaps" in WRITER_SYSTEM
    assert "{source_quality_context}" in WRITER_USER


def test_runtime_strips_analyst_overall_labels():
    node = AgentNodeOutput(
        node_id="analyst-a",
        role="analyst-a",
        threat_assessment="Low",
        threat_scores={
            "Competitor A": {
                "user_substitution": 50,
                "capability_catch_up": 50,
                "distribution": 50,
                "strategic_expansion": 50,
                "overall": 50,
            }
        },
    )

    stripped = strip_analyst_overall_labels(node)

    assert stripped.threat_assessment == ""
    assert "Competitor A" in stripped.threat_scores


def test_method_findings_require_auditable_trace_for_every_competitor():
    output = AgentNodeOutput(
        node_id="analyst-a",
        role="analyst-a",
        method_findings=[{
            "competitor": "Competitor A",
            "criterion": "难模仿性",
            "finding": "部分成立",
            "evidence_refs": ["https://example.com/source"],
            "reasoning": "能力依赖长期数据积累",
            "uncertainty": "缺少数据规模",
            "mapped_dimensions": ["capability_catch_up"],
        }],
    )

    assert validate_method_findings(output, ["Competitor A"]) == []
    assert validate_method_findings(output, ["Competitor A", "Competitor B"])


def test_qa_threat_assessment_must_be_competitor_object():
    valid = AgentNodeOutput(
        node_id="qa",
        role="qa",
        threat_assessment={
            "Competitor A": {
                "level": "\u4e2d",
                "score": 56,
                "evidence_strength": "\u8f83\u5145\u5206",
                "source_distribution": "O\u00d71 / B\u00d71 / C\u00d70",
            }
        },
    )
    invalid = AgentNodeOutput(
        node_id="qa",
        role="qa",
        threat_assessment="Competitor A\uff1a\u4e2d 56",
    )

    assert validate_competitor_threat_assessment(valid, ["Competitor A"]) == []
    assert validate_competitor_threat_assessment(invalid, ["Competitor A"])


def test_matrix_disagreements_use_score_deltas_not_analyst_labels():
    analyst_a = AgentNodeOutput(
        node_id="analyst-a",
        role="analyst-a",
        threat_assessment="Low",
        threat_scores={
            "Competitor A": {
                "user_substitution": 20,
                "capability_catch_up": 50,
                "distribution": 50,
                "strategic_expansion": 50,
                "overall": 43,
            }
        },
    )
    analyst_b = AgentNodeOutput(
        node_id="analyst-b",
        role="analyst-b",
        threat_assessment="High",
        threat_scores={
            "Competitor A": {
                "user_substitution": 72,
                "capability_catch_up": 50,
                "distribution": 50,
                "strategic_expansion": 50,
                "overall": 56,
            }
        },
    )

    disagreements = matrix_disagreements(analyst_a, analyst_b, ["Competitor A"])

    assert len(disagreements) == 1
    assert disagreements[0]["dimension"] == "user_substitution"
    assert disagreements[0]["delta"] == 0.52
    assert disagreements[0]["method_a"] == "VRIO"
    assert disagreements[0]["method_b"] == "SWOT"
    assert disagreements[0]["qa_reason"]
