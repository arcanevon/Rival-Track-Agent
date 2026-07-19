"""Search API integration and result scoring.

Provides provider detection, web-search API calls, and relevance scoring
for raw search-engine results.
"""

import asyncio
import logging
import os
import re
from urllib.parse import urlparse

from aiohttp import ClientSession

from src.config import load_scoring_config
from src.config.router import resolve_industry_keyword

from .constants import (
    LOW_QUALITY_RESULT_MARKERS,
    OFFICIAL_DOMAIN_HINTS,
    canonicalize_url,
    is_search_entry_url,
)
from .evidence_relevance import is_low_value_page


logger = logging.getLogger(__name__)

SOCIAL_MEDIA_DOMAINS: dict[str, str] = {
    "zhihu": "zhihu.com",
    "xiaohongshu": "xiaohongshu.com",
    "bilibili": "bilibili.com",
    "douyin": "douyin.com",
    "weibo": "weibo.com",
    "hupu": "hupu.com",
    "taptap": "taptap.cn",
    "v2ex": "v2ex.com",
    "reddit": "reddit.com",
    "hackernews": "news.ycombinator.com",
}

SOCIAL_PLATFORM_LABELS = {
    "zhihu": "知乎",
    "xiaohongshu": "小红书",
    "bilibili": "哔哩哔哩",
    "douyin": "抖音",
    "weibo": "微博",
    "hupu": "虎扑",
    "taptap": "TapTap",
    "v2ex": "V2EX",
    "reddit": "Reddit",
    "hackernews": "Hacker News",
}


# ---------------------------------------------------------------------------
# 搜索提供方辅助函数
# ---------------------------------------------------------------------------

def search_api_provider() -> str:
    """Return the name of the configured search API provider (``"bocha"``) or ``""``."""
    if (
        os.environ.get("BOCHA_SEARCH_API_KEY")
        or os.environ.get("WEB_SEARCH_API_KEY")
        or os.environ.get("BING_SEARCH_API_KEY")
    ):
        return "bocha"
    return ""


def search_api_key() -> str:
    """Return the configured search API key or ``""``."""
    return (
        os.environ.get("BOCHA_SEARCH_API_KEY")
        or os.environ.get("WEB_SEARCH_API_KEY")
        or os.environ.get("BING_SEARCH_API_KEY")
        or ""
    ).strip()


# ---------------------------------------------------------------------------
# 搜索结果评分
# ---------------------------------------------------------------------------

def _check_path_match(url: str, patterns: list[str]) -> bool:
    path = urlparse(url).path.lower()
    return any(p.lower() in path for p in patterns)


# 用于识别软件下载页或移动应用下载页的标题模式，避免将其当成有效竞品证据
_DOWNLOAD_TITLE_PATTERNS = [
    # 不带版本号的真实中文下载站标题
    re.compile(r"(?:完整版下载|官网游戏下载|app下载|软件下载|手机版下载|电脑版下载|免费安装|安装包)", re.I),
    # 低信息量攻略页，不能回答产品能力、价格或市场 Claim
    re.compile(r"(?:练什么|怎么玩|领取指南|刷取攻略|下载教程)", re.I),
    # 版本号与下载标记组合
    re.compile(r"v\d+\.\d+.*(?:下载|安卓|app|apk|ios|苹果|手机|安装|最新版|破解|绿色|汉化)", re.I),
    # 例如“XXX下载-XXXv1.2.3 安卓版-站点名”
    re.compile(r"下载[-\s].*v\d+\.\d+", re.I),
    # 纯下载站标题模式
    re.compile(r".*(?:安卓版|app下载|手机版下载|电脑版下载|破解版|绿色版|免费版|中文版|去广告版).*[-\s].*下载", re.I),
    # 标题末尾带版本号，常见于“AppName v1.2.3”一类下载页
    re.compile(r"v\d+\.\d+(?:\.\d+)?\s*(?:安卓|手机|破解|绿色|免费|汉化|中文|去广告|精简)", re.I),
]


def _is_download_title(title: str, snippet: str) -> bool:
    """Return True when the title/snippet looks like a software/mobile-app download page."""
    combined = f"{title} {snippet}"
    for pat in _DOWNLOAD_TITLE_PATTERNS:
        if pat.search(combined):
            return True
    return False


def social_platform_for_url(url: str) -> str:
    """根据具体内容 URL 识别支持的社交媒体平台。"""
    host = urlparse(str(url or "")).netloc.lower()
    key = next((platform for platform, domain in SOCIAL_MEDIA_DOMAINS.items() if domain in host), "")
    return SOCIAL_PLATFORM_LABELS.get(key, "")


def community_platforms_for_context(track: str = "", name: str = "") -> tuple[str, ...]:
    """按行业选择高信号社区，避免宽泛站点列表稀释搜索结果。"""
    context = f"{track} {name}".lower()
    if any(word in context for word in ("游戏", "手游", "game", "gaming")):
        return ("taptap", "bilibili", "douyin")
    if any(word in context for word in ("代码", "编程", "开发", "软件", "saas", "ai", "agent")):
        return ("zhihu", "v2ex", "bilibili")
    if any(word in context for word in ("茶饮", "零售", "消费", "餐饮", "火锅", "咖啡", "fmcg")):
        # B 站和知乎有稳定正文适配器；其余平台用于补充公开可读内容和搜索线索。
        return ("bilibili", "zhihu", "xiaohongshu", "douyin", "weibo")
    return ("zhihu", "xiaohongshu", "hupu")


def search_result_score(result: dict, name: str, track: str = "",
                        industry_type: str = "",
                        requested_source_types: list[str] | None = None) -> int:
    """Assign a relevance score to a single search result for the given competitor."""
    url = str(result.get("url", ""))
    title = str(result.get("title", ""))
    snippet = str(result.get("snippet", ""))
    host = urlparse(url).netloc.lower()
    haystack = f"{title} {snippet} {url}".lower()

    # 下载、安装和低信息量攻略页直接淘汰，避免品牌名命中把它们重新加回正分。
    if is_low_value_page(url, title, snippet) or _is_download_title(title, snippet):
        return -1000

    # 未显式提供行业类型时，仅通过快速关键词匹配推断
    if not industry_type and track:
        industry_type = resolve_industry_keyword(track, name) or ""

    cfg = load_scoring_config(industry_type) if industry_type else load_scoring_config()
    s = cfg.get("scoring", {}) if cfg else {}
    p = cfg.get("path_rules", {}) if cfg else {}
    d = cfg.get("domain_rules", {}) if cfg else {}
    score = 0
    requested_types = set(requested_source_types or [])
    social_platform = social_platform_for_url(url)

    name_tokens = [token for token in re.split(r"[\s/·,，、]+", name.lower()) if token]
    if any(token and token in haystack for token in name_tokens):
        score += s.get("name_token_match", 30)

    keywords = ("官网", "official", "pricing", "定价", "功能", "features", "docs", "文档", "公告", "release")
    if any(word in haystack for word in keywords):
        score += s.get("keyword_match", 20)

    review_keywords = ("评测", "review", "对比", "benchmark")
    if any(word in haystack for word in review_keywords):
        score += s.get("review_keyword_match", 8)

    community_keywords = ("评价", "体验", "吐槽", "避雷", "优点", "缺点", "踩坑", "推荐", "值不值")
    if "community" in requested_types and any(word in haystack for word in community_keywords):
        score += s.get("community_keyword_match", 12)
    if "community" in requested_types and social_platform:
        score += s.get("social_domain_bonus", 25)

    for keyword, domains in OFFICIAL_DOMAIN_HINTS.items():
        if keyword.lower() in f"{name} {track}".lower() and any(domain in host for domain in domains):
            score += s.get("official_domain_bonus", 40)
            break

    content_paths = p.get("content_paths", [])
    review_paths = p.get("review_paths", [])
    download_paths = p.get("download_paths", [])

    if content_paths and _check_path_match(url, content_paths):
        score += s.get("content_path_bonus", 30)
    if review_paths and _check_path_match(url, review_paths):
        score += s.get("review_path_bonus", 15)
    if download_paths and _check_path_match(url, download_paths):
        score += s.get("download_path_penalty", -50)

    if any(marker in haystack for marker in LOW_QUALITY_RESULT_MARKERS):
        score += s.get("low_quality_marker_penalty", -120)

    if _is_download_title(title, snippet):
        score += s.get("download_title_penalty", -100)

    low_quality_domains = d.get("low_quality_domains", [])
    if low_quality_domains and any(domain in host for domain in low_quality_domains):
        score += s.get("low_quality_domain_penalty", -120)

    if "oracle" in haystack and "obsidian" not in haystack:
        score += s.get("oracle_false_positive_penalty", -120)

    if "baike.baidu.com" in host:
        score += s.get("baike_penalty", -25)

    aggregator_domains = d.get("aggregator_domains", [])
    if aggregator_domains:
        for ad in aggregator_domains:
            if ad in host:
                is_official_hint = any(domain in host for domains in OFFICIAL_DOMAIN_HINTS.values() for domain in domains)
                if not is_official_hint:
                    score += s.get("aggregator_domain_penalty", -30)
                break

    content_farm = d.get("content_farm_domains", [])
    if content_farm and any(domain in host for domain in content_farm) and not (
        "community" in requested_types and social_platform
    ):
        score += s.get("content_farm_marker_penalty", -20)
        if "csdn.net" in host and "official" not in haystack and "官网" not in haystack:
            score += s.get("csdn_non_official_penalty", -35)

    return score


def rank_search_results(results: list[dict], name: str, track: str = "",
                        industry_type: str = "",
                        requested_source_types: list[str] | None = None) -> list[dict]:
    """按相关性降序排列，并过滤只靠品牌名获得低分的页面。"""
    ranked = [
        (result, search_result_score(result, name, track, industry_type, requested_source_types))
        for result in results
    ]
    return [
        {**result, "search_score": score}
        for result, score in sorted(ranked, key=lambda item: item[1], reverse=True)
        if score >= 40
    ]


# ---------------------------------------------------------------------------
# 网络搜索调用
# ---------------------------------------------------------------------------

async def search_web_api(
    session: ClientSession,
    query: str,
    count: int = 5,
    freshness: str = "noLimit",
    include_domains: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """Return concrete web result pages from a configured search API.

    Currently only the **Bocha** provider is supported.
    """
    provider = search_api_provider()
    if not provider:
        return []

    results: list[dict] = []
    if provider == "bocha":
        endpoint = "https://api.bocha.cn/v1/web-search"
        payload_body = {
            "query": query,
            "freshness": freshness,
            "summary": True,
            "count": max(1, min(int(count), 50)),
        }
        if include_domains:
            payload_body["include"] = "|".join(dict.fromkeys(include_domains))
        headers = {
            "Authorization": f"Bearer {search_api_key()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        payload = None
        for attempt in range(2):
            async with session.post(endpoint, json=payload_body, headers=headers) as resp:
                if resp.status in {429, 500, 502, 503, 504} and attempt == 0:
                    retry_after = resp.headers.get("Retry-After", "")
                    try:
                        delay = min(2.0, max(0.1, float(retry_after)))
                    except (TypeError, ValueError):
                        delay = 0.35
                    await resp.read()
                    await asyncio.sleep(delay)
                    continue
                if resp.status >= 400:
                    text = await resp.text(errors="ignore")
                    raise RuntimeError(f"Bocha Web Search API HTTP {resp.status}: {text[:200]}")
                payload = await resp.json()
                break
        if payload is None:
            raise RuntimeError("Bocha Web Search API retry budget exhausted")
        if str(payload.get("code")) not in {"200", "0"} and payload.get("code") not in {200, 0, None}:
            raise RuntimeError(f"Bocha Web Search API error {payload.get('code')}: {payload.get('msg') or payload.get('message')}")
        data = payload.get("data", payload)
        for item in data.get("webPages", {}).get("value", [])[:count]:
            normalized = {
                "title": item.get("name", ""),
                "url": item.get("url", ""),
                "snippet": item.get("summary") or item.get("snippet", ""),
                "site_name": item.get("siteName", ""),
                "date_published": item.get("datePublished") or item.get("dateLastCrawled", ""),
            }
            social_platform = social_platform_for_url(str(item.get("url", "")))
            if social_platform:
                normalized["social_platform"] = social_platform
            results.append(normalized)

    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in results:
        url = str(item.get("url", "")).strip()
        canonical = canonicalize_url(url)
        if not url or canonical in seen or is_search_entry_url(url):
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        seen.add(canonical)
        cleaned.append(item)
    return cleaned


async def search_social_media_api(
    session: ClientSession,
    query: str,
    count: int = 10,
    freshness: str = "oneMonth",
    platforms: tuple[str, ...] = ("zhihu", "xiaohongshu", "hupu"),
    competitor: str = "",
    track: str = "",
) -> list[dict]:
    """在受控请求预算内检索多个社区，并对一个缺失平台做定向补偿。"""
    domains = [SOCIAL_MEDIA_DOMAINS[name] for name in platforms if name in SOCIAL_MEDIA_DOMAINS]
    if not domains:
        return []
    scoped_query = query
    if competitor:
        context = f"{competitor} {track}".strip()
        scoped_query = f"{context} (用户评价 OR 使用体验 OR 优缺点 OR 吐槽 OR 避雷)"
    site_clause = " OR ".join(f"site:{domain}" for domain in domains)
    try:
        raw_rows = await search_web_api(
            session,
            f"{scoped_query} ({site_clause})",
            count=min(20, max(count * 2, len(domains) * 2)),
            freshness=freshness,
            include_domains=domains,
        )
    except Exception as exc:
        logger.warning("Batched social search failed for %s: %s", "|".join(domains), exc)
        raw_rows = []

    merged = [
        row for row in raw_rows
        if any(domain in urlparse(str(row.get("url", ""))).netloc.lower() for domain in domains)
        and _social_result_matches(row, competitor)
    ]

    # 批量召回为空时仅补偿最高优先级平台，避免行业批测触发 API 限流。
    if competitor and not merged:
        platform_key = next((key for key in platforms if key in SOCIAL_MEDIA_DOMAINS), "")
        domain = SOCIAL_MEDIA_DOMAINS.get(platform_key, "")
        if domain:
            try:
                fallback = await search_web_api(
                    session,
                    f"{competitor} {track} {SOCIAL_PLATFORM_LABELS[platform_key]} 评价 使用体验",
                    count=min(10, max(4, count)),
                    freshness=freshness,
                    include_domains=[domain],
                )
                merged = [row for row in fallback if _social_result_matches(row, competitor)]
            except Exception as exc:
                logger.warning("Social fallback search failed for %s: %s", domain, exc)

    seen: set[str] = set()
    deduped: list[dict] = []
    for result in merged:
        canonical = canonicalize_url(str(result.get("url", "")))
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        deduped.append(result)
    return deduped[:count]


def _social_result_matches(result: dict, competitor: str) -> bool:
    """要求社区搜索结果命中竞品实体，避免只命中宽泛行业词。"""
    if not competitor:
        return True
    blob = f"{result.get('title', '')} {result.get('snippet', '')}".lower()
    primary = re.split(r"[（(]", competitor.lower(), maxsplit=1)[0].strip()
    if primary and primary in blob:
        return True
    tokens = [
        token for token in re.split(r"[\s/\\,，、|·:：()（）\[\]【】\-]+", primary)
        if len(token) >= 2
    ]
    return bool(tokens) and all(token in blob for token in tokens)
