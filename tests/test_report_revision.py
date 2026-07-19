from src.reporting.revision import compile_annotation_gap, section_diff


def test_annotation_research_intents_compile_to_structured_gap() -> None:
    gap = compile_annotation_gap({
        "section_id": "threat_assessment", "quote": "证据不足",
        "comment": "补充知乎用户反馈", "intent": "supplement_evidence",
        "competitors": ["甲"], "dimensions": ["user_substitution"],
    })
    assert gap["requires_research"] is True
    assert gap["source_types"] == ["official", "benchmark", "community", "leading"]


def test_section_diff_is_reproducible() -> None:
    assert section_diff("第一行\n旧结论", "第一行\n新结论") == [
        {"kind": "equal", "text": "第一行"},
        {"kind": "remove", "text": "旧结论"},
        {"kind": "add", "text": "新结论"},
    ]


def test_highlight_and_comment_do_not_trigger_research() -> None:
    for intent in ("highlight_only", "comment_only"):
        gap = compile_annotation_gap({"section_id": "summary", "quote": "选中文字", "intent": intent})
        assert gap["requires_research"] is False
        assert gap["source_types"] == []
