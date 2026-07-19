"""场景附加分析视角的路由测试。"""

from src.agents.analysis_lenses import format_analysis_lenses, select_analysis_lenses


def test_platform_track_selects_network_effect_and_forward_scenario() -> None:
    lenses = select_analysis_lenses("短视频社区", {"competitive_concern": "创作者迁移"})

    assert any("网络效应" in item for item in lenses)
    assert any("前瞻情景" in item for item in lenses)


def test_regulated_track_selects_compliance_lens() -> None:
    rendered = format_analysis_lenses("医疗 AI", {"target_users": "医院"})

    assert "监管与合规" in rendered
    assert rendered.startswith("1. ")


def test_unknown_track_uses_industry_structure_fallback() -> None:
    lenses = select_analysis_lenses("未知赛道", {})

    assert "行业结构" in lenses[0]
