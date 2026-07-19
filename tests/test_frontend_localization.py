from pathlib import Path


APP_JS = (Path(__file__).parents[1] / "src" / "frontend" / "app.js").read_text(encoding="utf-8")


def test_internal_fields_have_chinese_labels() -> None:
    expected_fields = {
        "user_substitution": "用户替代威胁",
        "capability_catch_up": "能力追赶威胁",
        "distribution": "分发渠道威胁",
        "strategic_expansion": "战略扩张威胁",
        "evidence_strength": "证据强度",
        "actual_source_type": "实际来源类型",
        "relevance_precision_at_5": "前五条证据相关率",
        "claim_answer_rate": "问题可回答率",
        "bad_domain_leakage": "低质域名泄漏率",
    }
    for field, label in expected_fields.items():
        assert f"{field}: '{label}'" in APP_JS


def test_action_cards_do_not_fall_back_to_raw_internal_fields() -> None:
    assert "|| a.response_type" not in APP_JS
    assert "|| a.related_threat_dimension" not in APP_JS
    assert "fieldLabel(a.related_threat_dimension" in APP_JS


def test_framework_and_threat_dimensions_use_label_mappers() -> None:
    assert "fieldLabel(name)" in APP_JS
    assert "valueLabel(output.framework)" in APP_JS


def test_debate_callout_renders_markdown_blocks_instead_of_plain_text() -> None:
    assert "function renderDebateMarkdown" in APP_JS
    assert "debate-a-text').innerHTML = renderDebateMarkdown" in APP_JS
    assert "debate-b-text').innerHTML = renderDebateMarkdown" in APP_JS
    assert "renderDebateMarkdown(qa.output_summary" in APP_JS
    assert "debate-a-text').textContent" not in APP_JS
