"""Tests for DeepSeek client — parsing and error handling (no API calls)."""

import json

import pytest

from src.client.deepseek import (
    DeepSeekError,
    DeepSeekSchemaError,
    DeepSeekRefusalError,
    call_and_parse,
    parse_agent_output,
    get_client,
)


class TestParseAgentOutput:
    def test_parses_valid_json_directly(self):
        raw = json.dumps({
            "label": "Collector Report",
            "framework": "data-collection",
            "output_summary": "Collected 3 competitor profiles",
            "confidence": 0.9,
            "evidence": [
                {
                    "source_url": "https://codium.ai",
                    "source_label": "Codium Homepage",
                    "quote": "AI-powered code completion",
                    "relevance": "Core product description",
                }
            ],
        })
        result = parse_agent_output(raw, "collector", "collector-1")
        assert result.node_id == "collector-1"
        assert result.role == "collector"
        assert result.status == "completed"
        assert result.label == "Collector Report"
        assert result.confidence == 0.9
        assert len(result.evidence) == 1
        assert result.evidence[0].source_url == "https://codium.ai"

    def test_parses_json_from_markdown_code_block(self):
        raw = '```json\n{"label": "QA Verdict", "confidence": 0.75, "output_summary": "Analysts agree on threat level"}\n```'
        result = parse_agent_output(raw, "qa", "qa-1")
        assert result.label == "QA Verdict"
        assert result.confidence == 0.75
        assert result.output_summary == "Analysts agree on threat level"

    def test_parses_json_from_generic_code_block(self):
        raw = '```\n{"label": "Writer Report", "confidence": 0.8, "output_summary": "Final analysis"}\n```'
        result = parse_agent_output(raw, "writer", "writer-1")
        assert result.label == "Writer Report"
        assert result.confidence == 0.8

    def test_defaults_applied_for_missing_fields(self):
        raw = '{"label": "Minimal"}'
        result = parse_agent_output(raw, "analyst-a", "a-1")
        assert result.label == "Minimal"
        assert result.confidence == 0.5  # default
        assert result.framework == ""
        assert result.evidence == []

    def test_evidence_parsed_with_alternate_keys(self):
        raw = json.dumps({
            "label": "Report",
            "confidence": 0.7,
            "output_summary": "test",
            "evidence": [
                {"url": "https://x.com", "label": "X Source", "quote": "data", "relevance": "key"}
            ],
        })
        result = parse_agent_output(raw, "collector", "c-1")
        assert len(result.evidence) == 1
        assert result.evidence[0].source_url == "https://x.com"
        assert result.evidence[0].source_label == "X Source"

    def test_writer_section_array_is_normalized_to_markdown(self):
        raw = json.dumps({
            "report_sections": {"key_findings": ["发现 A", "发现 B"]},
        }, ensure_ascii=False)

        result = parse_agent_output(raw, "writer", "writer-1")

        assert result.report_sections["key_findings"] == "- 发现 A\n- 发现 B"

    def test_writer_section_object_is_normalized_to_chinese_markdown(self):
        raw = json.dumps({
            "report_sections": {
                "risk_opportunity": {
                    "immediate_risks": ["用户可能转向竞品"],
                    "strategic_opportunities": "强化企业工作流集成",
                    "unexpected_model_key": "需要人工复核",
                },
            },
        }, ensure_ascii=False)

        result = parse_agent_output(raw, "writer", "writer-1")
        section = result.report_sections["risk_opportunity"]

        assert "**即时风险**" in section
        assert "**战略机会**：强化企业工作流集成" in section
        assert "**补充信息 3**：需要人工复核" in section
        assert "unexpected_model_key" not in section

    def test_validation_error_is_exposed_as_schema_error_for_retry(self):
        raw = json.dumps({"confidence": [0.8]})

        with pytest.raises(DeepSeekSchemaError, match="schema validation"):
            parse_agent_output(raw, "writer", "writer-1")

    def test_raises_schema_error_for_invalid_json(self):
        with pytest.raises(DeepSeekSchemaError):
            parse_agent_output("not valid json at all {{{", "collector", "c-1")

    def test_raises_schema_error_for_empty_string(self):
        with pytest.raises(DeepSeekSchemaError):
            parse_agent_output("", "collector", "c-1")


class TestDeepSeekErrorHierarchy:
    def test_timeout_is_deepseek_error(self):
        assert issubclass(DeepSeekSchemaError, DeepSeekError)

    def test_refusal_is_deepseek_error(self):
        assert issubclass(DeepSeekRefusalError, DeepSeekError)


@pytest.mark.anyio
async def test_call_and_parse_retries_after_schema_validation_error(monkeypatch):
    responses = iter([
        json.dumps({"confidence": [0.8]}),
        json.dumps({"label": "撰写 Agent", "output_summary": "已完成"}, ensure_ascii=False),
    ])
    call_count = 0

    async def fake_call_deepseek(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return next(responses)

    monkeypatch.setattr("src.client.deepseek.call_deepseek", fake_call_deepseek)

    result = await call_and_parse(
        "system",
        "user",
        "writer",
        "writer-1",
        parse_attempts=2,
    )

    assert call_count == 2
    assert result.output_summary == "已完成"


class TestGetClient:
    def test_raises_without_api_key(self):
        import os
        old_key = os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            with pytest.raises(DeepSeekError, match="DEEPSEEK_API_KEY"):
                get_client()
        finally:
            if old_key is not None:
                os.environ["DEEPSEEK_API_KEY"] = old_key
