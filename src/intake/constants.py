"""Constants and utility functions for source intake.

This module holds all immutable data structures and pure-helper functions.
It must not import from other ``intake`` submodules at the top level to
avoid circular imports.
"""

from datetime import datetime
from urllib.parse import urlparse, urlunparse


logger = __import__("logging").getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

KNOWN_STRONG_SOURCE_URLS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "呷哺呷哺": {"official": [("呷哺呷哺官方网站", "https://www.xiabu.com/")]},
    "巴奴毛肚火锅": {"official": [("巴奴毛肚火锅官方网站", "https://www.banu.cn/")]},
    "小龙坎火锅": {"official": [("小龙坎火锅官方网站", "https://xiaolongkan.com/zh-cn")]},
    "大龙燚火锅": {"official": [("大龙燚火锅官方网站", "https://www.dalongyi.com/")]},
    "蜀大侠火锅": {"official": [("蜀大侠品牌官方网站", "https://www.cebencao.cn/ppgs/")]},
    "原神": {
        "official": [("原神官方网站", "https://ys.mihoyo.com/")],
        "community": [("原神 TapTap 社区", "https://www.taptap.cn/app/168332")],
    },
    "王者荣耀": {
        "official": [("王者荣耀官方网站", "https://pvp.qq.com/")],
        "community": [("王者荣耀 TapTap 社区", "https://www.taptap.cn/app/1915")],
    },
    "和平精英": {
        "official": [("和平精英官方网站", "https://gp.qq.com/")],
        "community": [("和平精英 TapTap 社区", "https://www.taptap.cn/search/%E5%92%8C%E5%B9%B3%E7%B2%BE%E8%8B%B1")],
    },
    "明日方舟": {
        "official": [("明日方舟官方网站", "https://ak.hypergryph.com/")],
        "community": [("明日方舟 TapTap 社区", "https://www.taptap.cn/app/70253")],
    },
    "重返未来：1999": {
        "official": [("重返未来：1999 官方网站", "https://re.bluepoch.com/")],
        "community": [("重返未来：1999 TapTap 游戏介绍", "https://www.taptap.cn/app/221062/all-info")],
    },
    "崩坏：星穹铁道": {
        "official": [("崩坏：星穹铁道官方网站", "https://sr.mihoyo.com/")],
        "community": [("崩坏：星穹铁道 TapTap 社区", "https://www.taptap.cn/app/224267")],
    },
}

WEAK_EVIDENCE_STATUSES = {
    "candidate_search",
    "candidate_text",
    "background_text",
    "fetch_failed",
    "fetch_budget_deferred",
    "missing_url",
    "rejected_irrelevant",
}

SOURCE_BUCKETS = ("official_sources", "benchmark_sources", "community_sources", "leading_sources")

THREAT_EVIDENCE_SLOTS = {
    "official_capability": {
        "dimension": "capability_catch_up",
        "source_types": ["official"],
        "query_terms": ["official features", "docs", "capabilities"],
        "freshness": "oneMonth",
    },
    "pricing_or_packaging": {
        "dimension": "user_substitution",
        "source_types": ["official"],
        "query_terms": ["pricing", "plans", "packaging"],
        "freshness": "oneMonth",
    },
    "community_pain": {
        "dimension": "user_substitution",
        "source_types": ["community"],
        "query_terms": ["review", "complaint", "feedback", "reddit", "github issues"],
        "freshness": "oneMonth",
    },
    "third_party_benchmark": {
        "dimension": "capability_catch_up",
        "source_types": ["benchmark"],
        "query_terms": ["benchmark", "comparison", "review"],
        "freshness": "oneYear",
    },
    "distribution_signal": {
        "dimension": "distribution",
        "source_types": ["official", "benchmark"],
        "query_terms": ["partners", "marketplace", "enterprise", "customers"],
        "freshness": "oneYear",
    },
    "github_release_velocity": {
        "dimension": "strategic_expansion",
        "source_types": ["leading"],
        "query_terms": ["github releases", "changelog", "issues"],
        "freshness": "oneYear",
    },
    "strategic_expansion_signal": {
        "dimension": "strategic_expansion",
        "source_types": ["leading"],
        "query_terms": ["hiring", "roadmap", "funding", "patent", "partnership"],
        "freshness": "oneYear",
    },
}

READABLE_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36 RivalTrack/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

SEARCH_DISAMBIGUATION_TERMS = {
    "obsidian": "Obsidian.md note app knowledge base pricing official",
    "flowus": "FlowUs 笔记 协作文档 多维表格 官网 定价",
    "wolai": "wolai 我来 笔记 协作文档 官网 定价",
    "王者荣耀": "王者荣耀 腾讯游戏 官网 公告 赛事 版本",
    "和平精英": "和平精英 腾讯游戏 官网 公告 版本",
    "明日方舟": "明日方舟 鹰角 官网 公告 版本",
    # 零售与快消品牌
    "蜜雪冰城": "蜜雪冰城 新茶饮 冰淇淋 奶茶 官网 门店 加盟 菜单 价格",
    "喜茶": "喜茶 HEYTEA 新茶饮 芝士茶 官网 门店 菜单 新品",
    "奈雪の茶": "奈雪の茶 奈雪的茶 新茶饮 烘焙 官网 门店 菜单",
    "奈雪的茶": "奈雪の茶 奈雪的茶 新茶饮 烘焙 官网 门店 菜单",
    "霸王茶姬": "霸王茶姬 新茶饮 国风 奶茶 官网 门店 加盟",
    "古茗": "古茗 新茶饮 奶茶 官网 门店 加盟 菜单",
    "茶百道": "茶百道 新茶饮 奶茶 官网 门店 加盟 菜单",
    "沪上阿姨": "沪上阿姨 新茶饮 奶茶 官网 门店 加盟 菜单",
    "瑞幸咖啡": "瑞幸咖啡 Luckin Coffee 官网 门店 菜单 价格 营销",
    "luckin coffee": "瑞幸咖啡 Luckin Coffee 咖啡 官网 门店 菜单",
    "星巴克": "星巴克 Starbucks 咖啡 官网 门店 菜单 价格",
    "Starbucks": "星巴克 Starbucks coffee 官网 门店 菜单",
    "蜜雪": "蜜雪冰城 MIXUE 新茶饮 冰淇淋 官网 门店",
}

LOW_QUALITY_RESULT_MARKERS = (
    "破解", "下载站", "绿色版", "吾爱破解", "52pojie", "crack", "torrent",
    "词典", "dictionary", "slovar", "vocab", "britannica", "gemology",
    "crystal", "spiritofisis", "meaning in english",
    "安卓最新版", "app下载", "download", "xlhs.com", "receipt.php",
)

OFFICIAL_DOMAIN_HINTS = {
    "呷哺呷哺": ("xiabu.com",),
    "巴奴毛肚火锅": ("banu.cn",),
    "小龙坎火锅": ("xiaolongkan.com",),
    "大龙燚火锅": ("dalongyi.com",),
    "蜀大侠火锅": ("cebencao.cn",),
    "obsidian": ("obsidian.md",),
    "flowus": ("flowus.cn",),
    "wolai": ("wolai.com",),
    "王者荣耀": ("pvp.qq.com", "honorofkings.com"),
    "和平精英": ("gp.qq.com",),
    "明日方舟": ("hypergryph.com", "arknights.global"),
    "原神": ("mihoyo.com", "hoyoverse.com"),
    "重返未来：1999": ("bluepoch.com",),
    "崩坏：星穹铁道": ("mihoyo.com", "hoyoverse.com"),
    # AI 编程产品
    "claude code": ("anthropic.com", "claude.ai", "claude.com"),
    "cursor": ("cursor.com",),
    "蔚来": ("nio.cn", "nio.com"),
    "大疆": ("dji.com",),
    "小红书": ("xiaohongshu.com",),
    "支付宝": ("alipay.com",),
    "微医": ("guahao.com", "wedoctor.com"),
    "github copilot": ("github.com", "github.blog"),
    "openai codex": ("openai.com", "help.openai.com"),
    "qodo": ("qodo.ai",),
    "codiumai": ("qodo.ai",),
    "roo code": ("github.com", "marketplace.visualstudio.com", "roocode.com", "roocodeinc.github.io"),
    "通义灵码": ("tongyi.aliyun.com", "developer.aliyun.com", "help.aliyun.com"),
    "windsurf": ("codeium.com", "windsurf.com"),
    # 零售与快消品牌
    "蜜雪冰城": ("mxbc.com",),
    "喜茶": ("heytea.com",),
    "奈雪の茶": ("naixue.com",),
    "奈雪的茶": ("naixue.com",),
    "霸王茶姬": ("bawangchaji.com",),
    "古茗": ("guming.com.cn", "guming.com"),
    "茶百道": ("cha100dao.com", "chabaidao.com"),
    "沪上阿姨": ("hushangayi.com",),
    "阿嬷手作": ("amahandmade.com",),
    "茉莉奶白": ("molijasmine.com",),
    "甜啦啦": ("tianlala.com",),
    "瑞幸咖啡": ("luckincoffee.com",),
    "星巴克": ("starbucks.com.cn", "starbucks.com"),
}


# ---------------------------------------------------------------------------
# URL 辅助函数
# ---------------------------------------------------------------------------

def canonicalize_url(url: str) -> str:
    """将 URL 规范化，供去重逻辑使用。"""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return (url or "").strip()
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def is_search_entry_url(url: str) -> bool:
    """判断 URL 是否为搜索结果入口，而不是可引用的具体页面。"""
    lowered = (url or "").lower()
    search_markers = (
        "bing.com/search",
        "google.com/search",
        "github.com/search",
        "tianyancha.com/search",
        "qcc.com/web/search",
        "xiaohongshu.com/search",
        "zhihu.com/search",
        "search.bilibili.com",
        "douyin.com/search",
        "s.weibo.com/weibo",
        "s.taobao.com/search",
        "douyin.com/search",
        "taptap.cn/search",
    )
    return any(marker in lowered for marker in search_markers)


# ---------------------------------------------------------------------------
# 来源分类辅助函数
# ---------------------------------------------------------------------------

def source_has_concrete_url(source: dict) -> bool:
    """判断来源是否具有非搜索页、非百科页的具体 URL。"""
    url = str(source.get("evidence_url") or source.get("url") or "").strip()
    if not url or is_search_entry_url(url):
        return False
    host = urlparse(url).netloc.lower()
    if "baike.baidu.com" in host:
        return False
    return True


def competitor_has_concrete_sources(competitor: dict) -> bool:
    """判断竞品是否至少有一个来源桶包含具体 URL。"""
    return any(
        source_has_concrete_url(source)
        for bucket in SOURCE_BUCKETS
        for source in competitor.get(bucket, []) or []
        if isinstance(source, dict)
    )


def source_bucket_for_candidate(source: dict) -> str:
    """按查询期望选择临时桶；抓取后还会根据实际来源重新分类。"""
    requested = source.get("requested_source_types")
    if isinstance(requested, str):
        requested = [requested]
    requested = requested if isinstance(requested, list) else []
    if source.get("channel") == "community":
        return "community_sources"
    if source.get("channel") == "leading":
        return "leading_sources"
    if "community" in requested:
        return "community_sources"
    if "leading" in requested:
        return "leading_sources"
    if "benchmark" in requested:
        return "benchmark_sources"
    if source.get("type") in {"knowledge-base", "industry-search"}:
        return "benchmark_sources"
    return "official_sources"


def is_candidate_source_only(source: dict) -> bool:
    """Return True when a source is not ready to be used as evidence."""
    return (
        not source_has_concrete_url(source)
        or source.get("direct_evidence") is False
    )


def source_ui_group(source: dict) -> str:
    """Classify a source into one of three UI groups: ``"leading"``, ``"candidate"``, ``"direct"``."""
    if source.get("channel") == "leading":
        return "leading"
    if source.get("type") == "knowledge-base":
        return "candidate"
    if source.get("direct_evidence") is False or is_search_entry_url(str(source.get("url", ""))):
        return "candidate"
    return "direct"


def source_status_label(source: dict) -> str:
    """Return a human-readable Chinese label for the source's current status."""
    group = source.get("source_group") or source_ui_group(source)
    if group == "leading":
        return "前瞻风向标"
    if group == "candidate":
        return "候选入口"
    if source.get("type") == "knowledge-base":
        return "背景信息"
    if source.get("type") == "web-search-result":
        return "需抓取验证"
    if source.get("authority") == "high":
        return "强证据"
    return "需人工确认"


# ---------------------------------------------------------------------------
# 查询辅助函数
# ---------------------------------------------------------------------------

def search_query_for_competitor(name: str, track: str = "") -> str:
    """Build a search query string for the given competitor, applying disambiguation when possible."""
    text = f"{name} {track}".lower()
    for keyword, query in SEARCH_DISAMBIGUATION_TERMS.items():
        if keyword.lower() in text:
            return f"{query} 最新 {datetime.now().year}"
    return f"{name} {track} 官网 产品 功能 定价 公告 评测 最新 {datetime.now().year}".strip()


def _is_developer_tool_context(name: str, track: str) -> bool:
    """Heuristic: return True when the competitor name/track suggests a developer tool product."""
    text = f"{name} {track}".lower()
    keywords = (
        "ai coding", "coding assistant", "code assistant", "github", "copilot",
        "cursor", "codex", "claude code", "developer", "devtool", "代码", "编程",
        "开发者", "代码助手", "智能体", "agent",
    )
    return any(keyword in text for keyword in keywords)


# ---------------------------------------------------------------------------
# 已知直接来源
# ---------------------------------------------------------------------------

def known_direct_sources(name: str, track: str = "") -> list[dict]:
    """Return concrete, readable source templates for common products / brands."""
    text = f"{name} {track}".lower()
    direct_product_sources = [
        ("cursor", "https://www.cursor.com/"),
        ("蔚来汽车", "https://www.nio.cn/"),
        ("蔚来", "https://www.nio.cn/"),
        ("nio", "https://www.nio.com/"),
        ("大疆创新", "https://www.dji.com/cn"),
        ("大疆", "https://www.dji.com/cn"),
        ("dji", "https://www.dji.com/"),
        ("小红书", "https://www.xiaohongshu.com/"),
        ("支付宝", "https://www.alipay.com/"),
        ("alipay", "https://global.alipay.com/"),
        ("微医", "https://www.guahao.com/"),
        ("豆包", "https://www.doubao.com/"),
        ("doubao", "https://www.doubao.com/"),
        ("文心一言", "https://yiyan.baidu.com/"),
        ("文心", "https://yiyan.baidu.com/"),
        ("ernie bot", "https://yiyan.baidu.com/"),
        ("智谱清言", "https://chatglm.cn/"),
        ("chatglm", "https://chatglm.cn/"),
        ("kimi", "https://www.kimi.com/"),
        ("通义", "https://tongyi.aliyun.com/"),
        ("tongyi", "https://tongyi.aliyun.com/"),
        ("腾讯元宝", "https://yuanbao.tencent.com/"),
        ("yuanbao", "https://yuanbao.tencent.com/"),
        ("讯飞星火", "https://xinghuo.xfyun.cn/"),
        ("星火", "https://xinghuo.xfyun.cn/"),
        ("王者荣耀", "https://pvp.qq.com/"),
        ("honor of kings", "https://www.honorofkings.com/"),
        ("和平精英", "https://gp.qq.com/"),
        ("明日方舟", "https://ak.hypergryph.com/"),
        ("arknights", "https://arknights.global/"),
        ("原神", "https://ys.mihoyo.com/"),
        ("genshin", "https://genshin.hoyoverse.com/"),
        ("obsidian", "https://obsidian.md/"),
        ("flowus", "https://flowus.cn/"),
        ("wolai", "https://www.wolai.com/"),
        ("我来", "https://www.wolai.com/"),
    ]
    phone_sources = [
        ("苹果 iphone", "https://www.apple.com.cn/iphone/"),
        ("apple iphone", "https://www.apple.com/iphone/"),
        ("小米", "https://www.mi.com/phone"),
        ("xiaomi", "https://www.mi.com/global/product-list/phone/"),
        ("oppo", "https://www.oppo.com/cn/smartphones/"),
        ("荣耀", "https://www.honor.com/cn/phones/"),
        ("honor", "https://www.honor.com/global/phones/"),
        ("华为", "https://consumer.huawei.com/cn/phones/"),
        ("huawei", "https://consumer.huawei.com/en/phones/"),
        ("三星", "https://www.samsung.com/cn/smartphones/"),
        ("samsung", "https://www.samsung.com/smartphones/"),
        ("vivo", "https://www.vivo.com.cn/vivo/param"),
    ]
    sources: list[dict] = []
    for source_type, known_rows in KNOWN_STRONG_SOURCE_URLS.get(name, {}).items():
        for label, url in known_rows:
            sources.append({
                "type": "official-product-page" if source_type == "official" else f"known-{source_type}-page",
                "label": label,
                "url": url,
                "evidence_url": url,
                "authority": "high" if source_type == "official" else "medium",
                "direct_evidence": True,
                "channel": source_type,
                "requested_source_types": [source_type],
            })
    for keyword, url in direct_product_sources:
        if keyword.lower() in text:
            sources.append({
                "type": "official-product-page",
                "label": f"{name} 官方产品页",
                "url": url,
                "evidence_url": url,
                "authority": "high",
                "direct_evidence": True,
                "channel": "official",
                "note": "具体官网产品页，优先用于提取产品定位、核心能力、入口与商业化信息。",
            })
            break
    retail_sources = [
        ("蜜雪冰城", "https://www.mxbc.com/"),
        ("喜茶", "https://www.heytea.com/"),
        ("奈雪の茶", "https://www.naixue.com/"),
        ("奈雪的茶", "https://www.naixue.com/"),
        ("霸王茶姬", "https://www.bawangchaji.com/"),
        ("古茗", "https://www.guming.com.cn/"),
        ("茶百道", "https://www.chabaidao.com/"),
        ("沪上阿姨", "https://www.hushangayi.com/"),
        ("瑞幸咖啡", "https://www.luckincoffee.com/"),
        ("luckin coffee", "https://www.luckincoffee.com/"),
        ("星巴克", "https://www.starbucks.com.cn/"),
    ]
    retail_keywords = ("新茶饮", "奶茶", "咖啡", "茶饮", "餐饮", "火锅", "烘焙", "冰淇淋")
    if any(kw in text for kw in retail_keywords):
        for keyword, url in retail_sources:
            if keyword.lower() in text:
                sources.append({
                    "type": "official-product-page",
                    "label": f"{name} 官方品牌页",
                    "url": url,
                    "evidence_url": url,
                    "authority": "high",
                    "direct_evidence": True,
                    "channel": "official",
                    "note": "具体品牌官网，优先用于提取品牌定位、菜单、门店、加盟与营销信息。",
                })
                break
    if any(keyword in text for keyword in ("手机", "smartphone", "phone", "iphone", "galaxy")):
        for keyword, url in phone_sources:
            if keyword.lower() in text:
                sources.append({
                    "type": "official-product-page",
                    "label": f"{name} 官方产品页",
                    "url": url,
                    "evidence_url": url,
                    "authority": "high",
                    "direct_evidence": True,
                    "channel": "official",
                    "note": "具体官网产品页，优先用于提取产品定位、核心能力、规格与渠道信息。",
                })
                break
    return sources


# ---------------------------------------------------------------------------
# 流程输入构建器（延迟导入 quality.assess_source_quality）
# ---------------------------------------------------------------------------

def candidate_source_to_pipeline_entry(source: dict, name: str, relationship: str = "manual_or_auto") -> dict:
    """Convert a candidate source dict into a pipeline-ready entry dict.

    This function performs a deferred import of ``assess_source_quality`` to
    keep the top-level of this module free of circular dependencies.
    """
    from .quality import assess_source_quality  # noqa: E402  -- deferred

    url = source.get("evidence_url") or source.get("url", "")
    source_group = source.get("source_group") or source_ui_group(source)
    quality = assess_source_quality(source)
    return {
        "url": url,
        "label": source.get("label", f"{name} evidence source"),
        "scraped_text": (
            f"自动发现证据来源。relationship_type={relationship}; "
            f"source_group={source_group}; direct_evidence={source.get('direct_evidence')}; "
            f"quality_score={quality['score']}; note={source.get('note', '')}"
        ),
        "fetch_method": "auto_discovered",
        "candidate_only": source_group == "candidate" or not quality["usable_for_scoring"],
        "source_group": source_group,
        "source_status": source.get("source_status") or source_status_label(source),
        "source_quality": quality,
        "evidence_slot": source.get("evidence_slot", ""),
        "threat_dimension": source.get("threat_dimension", ""),
        "requested_source_types": source.get("requested_source_types", []),
        "search_score": source.get("search_score", 0),
    }
