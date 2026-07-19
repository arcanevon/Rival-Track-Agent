"""Source candidate building and discovery.

Constructs initial candidate source lists from known-direct sources, search
entry templates, and — when a search API is available — merges in ranked
search results driven by the evidence-acquisition plan.
"""

import asyncio
import logging
from urllib.parse import quote_plus

from aiohttp import ClientSession

from .constants import (
    _is_developer_tool_context,
    canonicalize_url,
    is_search_entry_url,
    known_direct_sources,
    search_query_for_competitor,
    source_has_concrete_url,
    source_status_label,
    source_ui_group,
)
from .quality import assess_source_quality
from .search import (
    community_platforms_for_context,
    rank_search_results,
    search_api_provider,
    search_social_media_api,
    search_web_api,
)
from .plan import build_evidence_acquisition_plan
from src.config.router import resolve_industry_keyword


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 离线候选来源构建
# ---------------------------------------------------------------------------

def build_source_candidates(name: str, track: str = "") -> list[dict]:
    """Build stable candidate URLs for authoritative/manual verification sources."""
    query = f"{name} {track}".strip()
    encoded_name = quote_plus(name)
    encoded_query = quote_plus(query)
    sources = [
        *known_direct_sources(name, track),
        {
            "type": "official-search",
            "label": f"{name} 官网检索入口",
            "url": f"https://www.bing.com/search?q={encoded_name}+%E5%AE%98%E7%BD%91",
            "evidence_url": "",
            "authority": "high",
            "direct_evidence": False,
            "note": "优先从官网、品牌页面、投资者关系或公告入口确认。",
        },
        {
            "type": "business-registry",
            "label": f"{name} 天眼查检索入口",
            "url": f"https://www.tianyancha.com/search?key={encoded_name}",
            "evidence_url": "",
            "authority": "high",
            "direct_evidence": False,
            "note": "用于确认主体公司、工商登记、分支机构与经营范围。",
        },
        {
            "type": "knowledge-base",
            "label": f"{name} 百科条目",
            "url": f"https://baike.baidu.com/item/{encoded_name}",
            "evidence_url": f"https://baike.baidu.com/item/{encoded_name}",
            "authority": "medium",
            "direct_evidence": True,
            "note": "适合快速补全品牌沿革，关键事实仍需回到原始来源复核。",
        },
        {
            "type": "industry-search",
            "label": f"{query or name} 行业报告检索入口",
            "url": f"https://www.bing.com/search?q={encoded_query}+%E8%A1%8C%E4%B8%9A%E6%8A%A5%E5%91%8A+site%3Agov.cn+OR+site%3Aorg.cn+OR+site%3Aedu.cn",
            "evidence_url": "",
            "authority": "medium",
            "direct_evidence": False,
            "note": "用于寻找政府、协会、研究机构发布的行业材料。",
        },
    ]
    for channel, label, url in (
        ("community", f"{name} 小红书检索入口", f"https://www.xiaohongshu.com/search_result?keyword={encoded_query}"),
        ("community", f"{name} 知乎检索入口", f"https://www.zhihu.com/search?q={encoded_query}"),
        ("community", f"{name} B站检索入口", f"https://search.bilibili.com/all?keyword={encoded_query}"),
        ("community", f"{name} 抖音检索入口", f"https://www.douyin.com/search/{encoded_query}"),
        ("community", f"{name} 虎扑检索入口", f"https://www.bing.com/search?q=site%3Ahupu.com+{encoded_query}"),
        ("community", f"{name} 微博检索入口", f"https://s.weibo.com/weibo?q={encoded_query}"),
    ):
        sources.append({
            "type": "social-search",
            "label": label,
            "url": url,
            "evidence_url": "",
            "authority": "medium",
            "direct_evidence": False,
            "channel": channel,
            "note": "用于观察用户反馈与传播信号，需进入具体内容后再作为证据源。",
        })
    if _is_developer_tool_context(name, track):
        sources.extend([
            {
                "type": "github-search",
                "label": f"{name} GitHub 技术足迹检索",
                "url": f"https://github.com/search?q={encoded_query}&type=repositories",
                "evidence_url": "",
                "authority": "medium",
                "direct_evidence": False,
                "channel": "leading",
                "note": "用于发现开源仓库、Star 趋势、Release 与 Issue 活跃度。",
            },
            {
                "type": "github-release-search",
                "label": f"{name} GitHub Release/Issue 活跃度检索",
                "url": f"https://github.com/search?q={encoded_query}+release+OR+issues&type=issues",
                "evidence_url": "",
                "authority": "medium",
                "direct_evidence": False,
                "channel": "leading",
                "note": "用于观察版本节奏、缺陷反馈和路线图讨论。",
            },
        ])

    seen: set[str] = set()
    deduped: list[dict] = []
    for source in sources:
        raw_url = str(source.get("evidence_url") or source.get("url") or "")
        if source.get("evidence_slot") or is_search_entry_url(raw_url):
            key = f"{source.get('type', '')}:{source.get('evidence_slot', '')}:{raw_url}"
        else:
            key = canonicalize_url(raw_url) or str(source.get("label", ""))
        if key in seen:
            continue
        seen.add(key)
        source.setdefault("source_group", source_ui_group(source))
        source.setdefault("source_status", source_status_label(source))
        source["source_quality"] = assess_source_quality(source)
        deduped.append(source)
    return deduped


# ---------------------------------------------------------------------------
# 搜索增强候选来源构建
# ---------------------------------------------------------------------------

async def build_source_candidates_with_search(
    session: ClientSession,
    name: str,
    track: str = "",
    count: int = 10,
    max_queries: int = 4,
) -> tuple[list[dict], str]:
    """Merge slot-planned concrete search result pages with fallback candidates."""
    sources = build_source_candidates(name, track)
    provider = search_api_provider()
    if not provider:
        return sources, "未配置搜索 API，已返回离线候选来源。"

    industry_type = resolve_industry_keyword(track, name) or ""
    plan = build_evidence_acquisition_plan({"company": name}, track)
    all_plan_queries = [
        query
        for query in plan.get("queries", [])
        if isinstance(query, dict) and query.get("query")
    ]
    # 先保证 O/C/B/L 来源组合，再使用剩余预算补充价格、渠道等相邻槽位。
    priority = ("official", "community", "benchmark", "leading")
    plan_queries: list[dict] = []
    for source_type in priority:
        match = next((item for item in all_plan_queries
                      if source_type in item.get("source_types", []) and item not in plan_queries), None)
        if match:
            plan_queries.append(match)
    plan_queries.extend(item for item in all_plan_queries if item not in plan_queries)
    plan_queries = plan_queries[:max(1, max_queries)]
    if not plan_queries:
        plan_queries = [{
            "slot": "general_competitor_evidence",
            "dimension": "overall",
            "query": search_query_for_competitor(name, track),
        }]
    result_items: list[tuple[dict, dict]] = []
    per_query_count = max(4, min(10, count))

    async def run_query(query_item: dict) -> list[tuple[dict, dict]]:
        requested_types = list(query_item.get("source_types", []))
        query = str(query_item["query"])
        tasks = [search_web_api(
            session, query, count=per_query_count,
            freshness=str(query_item.get("freshness", "noLimit")),
        )]
        if "community" in requested_types:
            tasks.append(search_social_media_api(
                session, query, count=per_query_count,
                freshness=str(query_item.get("freshness", "oneMonth")),
                platforms=community_platforms_for_context(track, name),
                competitor=name, track=track,
            ))
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        rows: list[dict] = []
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                logger.warning("Search query failed for %s (%s): %s", name, query, outcome)
            else:
                rows.extend(outcome)
        return [
            (result, query_item)
            for result in rank_search_results(rows, name, track, industry_type, requested_types)
        ]

    # 小批量执行，避免多个竞品同时启动时瞬间击穿搜索 API 限流。
    for offset in range(0, len(plan_queries), 2):
        batch_items = plan_queries[offset:offset + 2]
        for batch in await asyncio.gather(*(run_query(item) for item in batch_items)):
            result_items.extend(batch)

    search_sources: list[dict] = []
    for result, query_item in result_items:
        url = str(result.get("url", "")).strip()
        if not source_has_concrete_url({"url": url, "direct_evidence": True}):
            continue
        search_sources.append({
            "type": "web-search-result",
            "label": result.get("title") or f"{name} 搜索结果",
            "url": url,
            "evidence_url": url,
            "authority": "medium",
            "direct_evidence": True,
            "source_group": "direct",
            "source_status": "需抓取验证",
            "search_provider": provider,
            "site_name": result.get("site_name", ""),
            "date_published": result.get("date_published", ""),
            "social_platform": result.get("social_platform", ""),
            "channel": "community" if result.get("social_platform") else "",
            "search_snippet": result.get("snippet", ""),
            "evidence_slot": query_item.get("slot", ""),
            "threat_dimension": query_item.get("dimension", ""),
            "requested_source_types": query_item.get("source_types", []),
            "search_score": result.get("search_score", 0),
            "note": result.get("snippet") or "真实搜索 API 返回的具体页面；开始分析前会尝试抓取正文并校验质量。",
        })

    seen: set[str] = set()
    merged: list[dict] = []
    direct_sources = [
        source for source in sources
        if source.get("direct_evidence") is True
        and source.get("type") != "knowledge-base"
        and not is_search_entry_url(str(source.get("url", "")))
    ]
    other_sources = [source for source in sources if source not in direct_sources]
    for source in [*direct_sources, *search_sources, *other_sources]:
        url = str(source.get("evidence_url") or source.get("url") or "")
        key = canonicalize_url(url) or str(source.get("label", ""))
        if key in seen:
            continue
        seen.add(key)
        source["source_quality"] = assess_source_quality(source)
        merged.append(source)
    return merged, f"已接入 {provider} 搜索 API，并合并真实搜索结果。"
