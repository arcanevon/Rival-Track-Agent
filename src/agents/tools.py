"""Collector 可通过 LangGraph ToolNode 调用的证据工具。"""

from __future__ import annotations

import json

from aiohttp import ClientSession, ClientTimeout
from langchain_core.tools import tool

from src.intake.hydrate import READABLE_FETCH_HEADERS, fetch_readable_source
from src.intake.search import (
    community_platforms_for_context,
    rank_search_results,
    search_api_provider,
    search_social_media_api,
    search_web_api,
)


async def _hydrate_ranked_results(
    session: ClientSession,
    results: list[dict],
    competitor: str,
    *,
    max_pages: int = 2,
) -> list[dict]:
    """读取排名靠前的搜索结果，使一次工具调用同时返回候选线索和可核验正文。"""
    hydrated: list[dict] = []
    attempted = 0
    for result in results:
        row = dict(result)
        url = str(row.get("url", "")).strip()
        if not url or attempted >= max_pages:
            hydrated.append(row)
            continue
        attempted += 1
        try:
            fetched = await fetch_readable_source(session, url)
            row["title"] = row.get("title") or fetched.get("title") or competitor
            row["scraped_text"] = str(fetched.get("text", ""))[:4000]
            row["fetch_method"] = fetched.get("fetch_method", "")
            row["candidate_only"] = bool(fetched.get("candidate_only"))
            row["quality_note"] = fetched.get("quality_note", "")
            row["source_quality"] = fetched.get("source_quality", {})
        except Exception as exc:
            row["candidate_only"] = True
            row["fetch_error"] = str(exc)[:300]
        hydrated.append(row)
    return hydrated


@tool
async def search_competitor_evidence(
    query: str,
    competitor: str,
    track: str = "",
    freshness: str = "noLimit",
    source_type: str = "benchmark",
) -> str:
    """搜索竞品公开证据页面；结果是待抓取验证的候选线索。"""
    provider = search_api_provider()
    if not provider:
        return json.dumps(
            {"status": "unavailable", "reason": "未配置搜索工具密钥", "query": query},
            ensure_ascii=False,
        )
    async with ClientSession(timeout=ClientTimeout(total=35)) as session:
        results = await search_web_api(session, query, count=4, freshness=freshness)
        ranked = rank_search_results(results, competitor, track, requested_source_types=[source_type])[:4]
        for row in ranked:
            row["requested_source_types"] = [source_type]
        ranked = await _hydrate_ranked_results(session, ranked, competitor)
    return json.dumps(
        {
            "status": "ok",
            "tool": "search",
            "competitor": competitor,
            "query": query,
            "results": ranked,
            "usage_rule": "仅 scraped_text 非空且通过质量检查的结果可作为评分证据，其余仍是候选线索。",
        },
        ensure_ascii=False,
    )


@tool
async def read_evidence_page(url: str, competitor: str) -> str:
    """读取公开网页正文，返回可供 Collector 观察的结构化内容。"""
    async with ClientSession(
        timeout=ClientTimeout(total=30),
        headers=READABLE_FETCH_HEADERS,
    ) as session:
        result = await fetch_readable_source(session, url)
    return json.dumps(
        {
            "status": "ok" if not result.get("candidate_only") else "weak",
            "tool": "reader",
            "competitor": competitor,
            "url": url,
            "title": result.get("title", ""),
            "text": str(result.get("text", ""))[:4000],
            "fetch_method": result.get("fetch_method", ""),
            "source_quality": result.get("source_quality", {}),
        },
        ensure_ascii=False,
    )


@tool
async def search_community_evidence(
    query: str,
    competitor: str,
    track: str = "",
    freshness: str = "oneMonth",
    source_type: str = "community",
) -> str:
    """在行业相关热门社区中搜索竞品评价、使用体验和用户痛点。"""
    provider = search_api_provider()
    if not provider:
        return json.dumps(
            {"status": "unavailable", "reason": "未配置搜索工具密钥", "query": query},
            ensure_ascii=False,
        )
    async with ClientSession(timeout=ClientTimeout(total=35)) as session:
        platform_keys = community_platforms_for_context(track, competitor)
        results = await search_social_media_api(
            session,
            query,
            count=10,
            freshness=freshness,
            platforms=platform_keys,
            competitor=competitor,
            track=track,
        )
        ranked = rank_search_results(
            results,
            competitor,
            track,
            requested_source_types=["community"],
        )[:6]
        ranked = await _hydrate_ranked_results(session, ranked, competitor)
    return json.dumps(
        {
            "status": "ok",
            "tool": "community_search",
            "competitor": competitor,
            "query": query,
            "platforms": sorted({item.get("social_platform", "") for item in ranked if item.get("social_platform")}),
            "results": ranked,
            "usage_rule": "社交搜索结果仍是候选线索，必须读取具体内容并通过相关性门禁。",
        },
        ensure_ascii=False,
    )


COLLECTOR_TOOLS = [search_competitor_evidence, search_community_evidence, read_evidence_page]

__all__ = [
    "COLLECTOR_TOOLS",
    "read_evidence_page",
    "search_community_evidence",
    "search_competitor_evidence",
]
