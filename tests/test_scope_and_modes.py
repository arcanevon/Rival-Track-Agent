import asyncio

from src.intake.scope import (
    analysis_mode_policy, build_scope_snapshot, discover_analysis_scope, validate_scope_snapshot,
    extract_competitor_names_from_search,
)


def test_analysis_modes_control_budget_and_rework() -> None:
    fast = analysis_mode_policy("fast")
    standard = analysis_mode_policy("standard")
    deep = analysis_mode_policy("deep")
    assert [fast.competitor_limit, standard.competitor_limit, deep.competitor_limit] == [3, 5, 8]
    assert [fast.rework_rounds, standard.rework_rounds, deep.rework_rounds] == [0, 1, 2]
    assert fast.budget.max_search_calls < standard.budget.max_search_calls < deep.budget.max_search_calls
    assert analysis_mode_policy("任意输入") == standard


def test_track_only_discovery_returns_track_before_competitors() -> None:
    async def fake_model(system_prompt: str, user_prompt: str) -> str:
        assert "只识别" in user_prompt
        return '{"broad_track":"餐饮与消费","sub_track":"中式火锅","competitors":[]}'

    scope = asyncio.run(discover_analysis_scope(
        "测试火锅品牌", "", [], 5, model_call=fake_model, track_only=True,
    ))
    assert scope["broad_track"] == "餐饮与消费"
    assert scope["sub_track"] == "中式火锅"
    assert scope["competitors"] == []


def test_scope_snapshot_rejects_drift_after_confirmation() -> None:
    scope = build_scope_snapshot("目标产品", "游戏", ["甲", "乙"])
    scope["confirmed"] = True
    assert validate_scope_snapshot(scope, "目标产品", "游戏", ["甲", "乙"]) == ""
    assert "竞品集合" in validate_scope_snapshot(scope, "目标产品", "游戏", ["乙", "甲"])
    assert "赛道" in validate_scope_snapshot(scope, "目标产品", "软件", ["甲", "乙"])


def test_unknown_product_uses_model_discovery_instead_of_empty_scope() -> None:
    async def fake_model(system_prompt: str, user_prompt: str) -> str:
        assert "任意新产品" in user_prompt
        return '''{"broad_track":"企业软件","sub_track":"协同知识库",
        "competitors":[{"name":"竞品甲","relationship_type":"direct_substitute","reason":"用户需求重合","confidence":0.84},
        {"name":"竞品乙","relationship_type":"capability_chaser","reason":"能力重合","confidence":0.72}]}'''

    scope = asyncio.run(discover_analysis_scope(
        "任意新产品", "", [], 5, seed_names=[], model_call=fake_model,
    ))
    assert scope["broad_track"] == "企业软件"
    assert scope["sub_track"] == "协同知识库"
    assert [item["name"] for item in scope["competitors"]] == ["竞品甲", "竞品乙"]


def test_scope_uses_search_candidates_when_model_is_unavailable() -> None:
    async def failed_model(system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("model unavailable")

    async def fake_search(product: str, track: str, limit: int) -> list[str]:
        return ["搜索竞品甲", "搜索竞品乙"]

    scope = asyncio.run(discover_analysis_scope(
        "新产品", "协同软件", [], 5, seed_names=[],
        model_call=failed_model, search_discover=fake_search,
    ))
    assert [item["name"] for item in scope["competitors"]] == ["搜索竞品甲", "搜索竞品乙"]
    assert scope["discovery_method"] == "search_fallback"


def test_search_scope_extractor_keeps_compared_product_entities() -> None:
    rows = [
        {"title": "ClickUp vs Notion: which workspace is better?", "snippet": ""},
        {"title": "Notion alternatives", "snippet": "Popular alternatives include Coda, Slite and Confluence."},
    ]
    assert extract_competitor_names_from_search("Notion", rows, 5) == [
        "ClickUp", "Coda", "Slite", "Confluence",
    ]
