from pathlib import Path


ROOT = Path(__file__).parents[1]
INDEX_HTML = (ROOT / "src" / "frontend" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "src" / "frontend" / "app.js").read_text(encoding="utf-8")
MAIN_PY = (ROOT / "src" / "main.py").read_text(encoding="utf-8")


def test_analysis_intake_uses_choice_questions_instead_of_long_profile_fields() -> None:
    for name in (
        "target-positioning",
        "target-users",
        "target-capabilities",
        "target-concern",
    ):
        assert f'name="{name}"' in INDEX_HTML

    for removed_id in (
        "ana-track",
        "ana-target-positioning",
        "ana-target-users",
        "ana-target-capabilities",
        "ana-target-concern",
    ):
        assert f'id="{removed_id}"' not in INDEX_HTML


def test_analysis_intake_keeps_only_short_free_text_fallbacks() -> None:
    assert 'id="ana-target-name"' in INDEX_HTML
    assert 'id="ana-context" rows="2"' in INDEX_HTML
    assert 'id="ana-competitors"' not in INDEX_HTML
    assert 'id="ana-track-other"' not in INDEX_HTML
    assert 'name="analysis-track"' not in INDEX_HTML
    assert 'id="scope-track-input"' in APP_JS


def test_choice_answers_map_to_existing_backend_contract() -> None:
    assert "const track = confirmedScope.broad_track || '';" in APP_JS
    assert "positioning: selectedChoiceText('target-positioning'" in APP_JS
    assert "target_users: selectedChoiceText('target-users'" in APP_JS
    assert "core_capabilities: selectedChoiceText('target-capabilities'" in APP_JS
    assert "competitive_concern: selectedChoiceText('target-concern'" in APP_JS
    assert "JSON.stringify({ product_name: productName, track, threat_target: threatTarget, competitors })" in APP_JS


def test_default_and_specific_user_choices_are_mutually_exclusive() -> None:
    assert 'name="target-users" value="用户范围不限" data-default data-exclusive' in INDEX_HTML
    assert "input.hasAttribute('data-exclusive')" in APP_JS


def test_analysis_accepts_eight_competitors_in_frontend_and_backend() -> None:
    assert "const MAX_ANALYSIS_COMPETITORS = 8;" in APP_JS
    assert "names.length > MAX_ANALYSIS_COMPETITORS" in APP_JS
    assert "MAX_CUSTOM_COMPETITORS = 8" in MAIN_PY
    assert "len(user_data) > MAX_CUSTOM_COMPETITORS" in MAIN_PY
    assert "at most 5" not in MAIN_PY


def test_analysis_requires_confirmed_scope_and_exposes_three_modes() -> None:
    assert 'id="btn-discover-track"' in INDEX_HTML
    assert 'id="btn-confirm-track"' in INDEX_HTML
    assert 'id="analysis-scope-panel"' in INDEX_HTML
    for mode in ("fast", "standard", "deep"):
        assert f'name="analysis-mode" value="{mode}"' in INDEX_HTML
    assert "if (!confirmedScope)" in APP_JS
    assert "scope_snapshot: confirmedScope" in APP_JS
    assert 'id="btn-freeze-scope"' in INDEX_HTML
    assert "invalidateConfirmedScope" in APP_JS


def test_new_analysis_is_a_standalone_view_with_discovery_after_product() -> None:
    assert 'class="view" id="view-analysis"' in INDEX_HTML
    assert 'class="analysis-sidebar"' in INDEX_HTML
    assert 'class="modal-overlay" id="analysis-modal-overlay"' not in INDEX_HTML
    assert "switchView('analysis')" in APP_JS
    assert INDEX_HTML.index('id="ana-target-name"') < INDEX_HTML.index('id="btn-discover-track"')
    assert INDEX_HTML.index('id="btn-discover-track"') < INDEX_HTML.index('id="btn-confirm-track"')
    assert "discoverAnalysisTrack" in APP_JS
    assert "confirmTrackAndDiscoverCompetitors" in APP_JS
    assert 'id="btn-add-scope-competitor"' in INDEX_HTML
    assert 'id="scope-manual-name"' in INDEX_HTML
    assert "scopeDraft = {" in APP_JS
    assert "broad_track: document.getElementById('scope-track-input')" in APP_JS
    assert "stage: 'track'" in APP_JS
    assert "stage: 'competitors'" in APP_JS
    assert 'stage == "track"' in MAIN_PY
    assert 'stage == "competitors"' in MAIN_PY


def test_candidate_search_entries_are_not_submitted_as_sources() -> None:
    assert "后台检索线索" in APP_JS
    assert "source?.source_group === 'candidate'" in APP_JS
    assert "source?.direct_evidence === false" in APP_JS
