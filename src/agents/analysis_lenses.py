"""根据任务场景选择补充分析视角。

VRIO 与市场动态分析始终作为两个基础视角；这里仅生成方法提示，不增加新的
固定 Agent，也不改变现有 LangGraph 的并行结构。
"""

from __future__ import annotations


_LENS_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("医疗", "医药", "健康", "金融", "银行", "保险", "证券", "fintech"),
     "监管与合规：检查准入、数据合规、责任边界和政策变化是否改变竞争门槛。"),
    (("平台", "社区", "社交", "内容", "短视频", "游戏", "直播"),
     "网络效应与生态锁定：检查供需两侧规模、创作者/开发者生态、社交关系和迁移成本。"),
    (("电商", "零售", "汽车", "消费", "餐饮", "茶饮", "硬件"),
     "价格、渠道与单位经济性：检查定价、补贴、毛利、渠道控制和履约效率是否可持续。"),
    (("企业", "软件", "saas", "云", "开发", "代码", "ai", "人工智能"),
     "用户任务与替代路径：检查竞品完成的核心任务、切换触发点、迁移成本和工作流嵌入程度。"),
)


def select_analysis_lenses(
    track: str,
    threat_target: dict[str, object] | None = None,
) -> list[str]:
    """返回适合当前赛道的补充视角，最多三个并保持确定性顺序。"""
    target = threat_target or {}
    context = " ".join(
        str(value)
        for value in (
            track,
            target.get("positioning", ""),
            target.get("target_users", ""),
            target.get("core_capabilities", ""),
            target.get("competitive_concern", ""),
        )
    ).lower()
    selected = [description for keywords, description in _LENS_RULES if any(key in context for key in keywords)]
    if not selected:
        selected.append("行业结构：检查进入壁垒、替代品、上下游议价权和竞争强度。")
    selected.append("前瞻情景：区分已发生结果与领先信号，说明信号成立所需的后续条件。")
    return selected[:3]


def format_analysis_lenses(
    track: str,
    threat_target: dict[str, object] | None = None,
) -> str:
    """将场景视角格式化为可直接注入提示词的中文说明。"""
    items = select_analysis_lenses(track, threat_target)
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))
