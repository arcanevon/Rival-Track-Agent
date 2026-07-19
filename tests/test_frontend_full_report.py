from pathlib import Path


ROOT = Path(__file__).parents[1]
INDEX_HTML = (ROOT / "src" / "frontend" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "src" / "frontend" / "app.js").read_text(encoding="utf-8")


def test_full_report_is_a_first_class_navigation_view() -> None:
    assert 'data-view="report">完整报告' in INDEX_HTML
    assert 'id="view-report"' in INDEX_HTML
    assert 'id="full-report-content"' in INDEX_HTML
    assert 'id="report-toc"' in INDEX_HTML


def test_full_report_uses_writer_qa_evidence_and_actions() -> None:
    assert "function buildFullReportModel(outputs)" in APP_JS
    assert "writer.report_sections" in APP_JS
    assert "writer.threat_scores || qa.threat_scores" in APP_JS
    assert "uniqueReportEvidence(outputs)" in APP_JS
    assert "writer.response_actions" in APP_JS
    assert "writer.evidence_gaps" in APP_JS


def test_full_report_has_verda_style_business_reading_layers() -> None:
    for label in (
        "关键结论速览", "逐竞品档案", "风险、机会与观察信号",
        "证据覆盖看板", "方法与质量审计",
    ):
        assert label in APP_JS
    for function_name in (
        "reportMetricStripHTML", "reportKeyFindingsHTML", "reportCompetitorProfilesHTML",
        "reportRiskOpportunityHTML", "reportEvidenceCoverageHTML", "reportMethodologyHTML",
    ):
        assert f"function {function_name}" in APP_JS
    assert "reportMetricStripHTML(model)" in APP_JS
    assert "requires_human_confirmation" in APP_JS
    assert "function setupReportReadingProgress()" in APP_JS
    assert 'id="report-read-percent"' in APP_JS


def test_full_report_supports_markdown_and_print_exports() -> None:
    assert 'id="btn-export-report"' in INDEX_HTML
    assert 'id="btn-print-report"' in INDEX_HTML
    assert "function buildFullReportMarkdown(outputs)" in APP_JS
    assert "text/markdown;charset=utf-8" in APP_JS
    assert "window.print()" in APP_JS


def test_report_supports_csv_annotation_and_evidence_workspace() -> None:
    for element_id in (
        "btn-export-threat-csv", "btn-export-evidence-csv", "btn-export-actions-csv",
        "revision-modal-overlay", "btn-open-evidence-workspace", "evidence-workspace-overlay",
    ):
        assert f'id="{element_id}"' in INDEX_HTML
    for function_name in (
        "buildThreatMatrixCSV", "buildEvidenceCSV", "buildActionsCSV", "openRevisionModal",
    ):
        assert f"function {function_name}" in APP_JS
    assert "/api/report/revise" in APP_JS
    assert "/api/quality/human-metrics" in APP_JS


def test_report_annotations_support_non_modifying_actions() -> None:
    assert 'value="highlight_only"' in INDEX_HTML
    assert 'value="comment_only"' in INDEX_HTML
    assert 'id="btn-request-revision"' in INDEX_HTML
    assert 'class="btn-new-analysis" id="btn-request-revision"' in INDEX_HTML
    assert "function applyReportAnnotations(outputs)" in APP_JS
    assert "revision.kind === 'annotation'" in APP_JS


def test_methodology_explains_vrio_and_swot_for_business_users() -> None:
    assert "VRIO：判断优势能否持续" in APP_JS
    assert "V（价值性）" in APP_JS and "O（组织承接）" in APP_JS
    assert "SWOT：判断优势如何转化为市场压力" in APP_JS
    assert "S（优势）" in APP_JS and "T（威胁）" in APP_JS
    assert "而不是机械平均分数" in APP_JS


def test_dashboard_suppresses_scores_without_evidence() -> None:
    assert "function hasNoScoreableEvidence" in APP_JS
    assert "THREAT_DIMS.map(() => null).concat([null])" in APP_JS
    assert "scoreObj && !noEvidence" in APP_JS


def test_no_evidence_scores_are_presented_as_pending_evaluation() -> None:
    assert "if (noEvidence) return '<span class=\"report-score-low\">待评估</span>';" in APP_JS
    assert "/无证据/.test(strength) ? '待评估'" in APP_JS
