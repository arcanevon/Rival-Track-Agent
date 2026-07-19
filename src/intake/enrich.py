"""Competitor enrichment.

Takes raw user-supplied competitor data, optionally enriches it with
search-engine results, builds the internal cache shape, and attaches
source-coverage metadata so downstream agents can operate on it.
"""

import logging
from urllib.parse import quote_plus

from aiohttp import ClientSession, ClientTimeout

from .constants import (
    KNOWN_STRONG_SOURCE_URLS,
    SOURCE_BUCKETS,
    candidate_source_to_pipeline_entry,
    is_candidate_source_only,
    source_bucket_for_candidate,
    source_status_label,
    source_ui_group,
    source_has_concrete_url,
)
from .discovery import build_source_candidates_with_search
from .plan import build_evidence_acquisition_plan
from .quality import (
    assess_source_quality,
    source_coverage_for_competitor,
    source_has_content,
    split_candidate_sources,
)
from .search import search_api_provider


logger = logging.getLogger(__name__)


def _concrete_source_bucket_count(competitor: dict) -> int:
    """统计已有具体内容页覆盖的来源类型，避免单条官网链接阻断其余补采。"""
    return sum(
        any(
            isinstance(source, dict) and source_has_concrete_url(source)
            for source in competitor.get(bucket, []) or []
        )
        for bucket in SOURCE_BUCKETS
    )


# ===================================================================
# 证据线索辅助函数
# ===================================================================

def evidence_lead_text(company: str, track: str, source_type: str) -> str:
    """Build a Chinese prompt snippet that tells downstream agents what evidence slots to fill."""
    slots = (
        "定位 positioning; 核心能力 capability; 用户替代 user_substitution; "
        "分发渠道 distribution; 战略扩张 strategic_expansion; "
        "用户反馈 user_feedback; 前瞻信号 leading_indicators"
    )
    return (
        f"候选强证据线索：{company}。"
        f"赛道：{track or '未填写'}。"
        f"来源类型：{source_type}。"
        f"请优先从该来源提取这些证据槽位：{slots}。"
        "如果页面未抓取到正文，不要把竞品名当作证据，"
        "而是明确标记该槽位待补证据。"
    )


def build_evidence_leads(company: str, track: str, source_type: str) -> list[dict]:
    """Build candidate evidence-lead source dicts for the given source type."""
    encoded_company = quote_plus(company)
    encoded_query = quote_plus(f"{company} {track}".strip())
    known = KNOWN_STRONG_SOURCE_URLS.get(company, {}).get(source_type, [])
    sources = [
        {
            "url": url,
            "label": label,
            "scraped_text": evidence_lead_text(company, track, source_type),
            "candidate_only": True,
        }
        for label, url in known
    ]
    if source_type == "official":
        sources.extend([
            {
                "url": f"https://www.bing.com/search?q={encoded_company}+%E5%AE%98%E7%BD%91+%E7%89%88%E6%9C%AC+%E5%85%AC%E5%91%8A",
                "label": f"{company} 官方资料检索",
                "scraped_text": evidence_lead_text(company, track, "official"),
                "candidate_only": True,
            },
            {
                "url": f"https://baike.baidu.com/item/{encoded_company}",
                "label": f"{company} 百科浏览",
                "scraped_text": evidence_lead_text(company, track, "benchmark"),
                "candidate_only": True,
            },
        ])
    elif source_type == "leading":
        sources.extend([
            {
                "url": f"https://www.bing.com/search?q={encoded_company}+%E6%8B%9B%E8%81%98+%E8%B7%AF%E7%BA%BF%E5%9B%BE+%E7%BB%84%E7%BB%87%E8%B0%83%E6%95%B4",
                "label": f"{company} 前瞻信号检索：招聘/路线图/组织调整",
                "scraped_text": evidence_lead_text(company, track, "leading"),
                "candidate_only": True,
            },
            {
                "url": f"https://www.bing.com/search?q={encoded_company}+%E6%8B%9B%E6%8A%95%E6%A0%87+%E9%87%87%E8%B4%AD+%E4%B8%93%E5%88%A9+%E8%9E%8D%E8%B5%84%E7%94%A8%E9%80%94",
                "label": f"{company} 前瞻信号检索：招投标/专利/融资用途",
                "scraped_text": evidence_lead_text(company, track, "leading"),
                "candidate_only": True,
            },
        ])
    else:
        sources.extend([
            {
                "url": f"https://www.taptap.cn/search/{encoded_query}",
                "label": f"{company} TapTap 口碑检索",
                "scraped_text": evidence_lead_text(company, track, "community"),
                "candidate_only": True,
            },
            {
                "url": f"https://s.weibo.com/weibo?q={encoded_query}",
                "label": f"{company} 微博用户反馈检索",
                "scraped_text": evidence_lead_text(company, track, "community"),
                "candidate_only": True,
            },
        ])
    for source in sources:
        source.setdefault("source_group", source_ui_group(source))
        source.setdefault("source_status", source_status_label(source))
        source["source_quality"] = assess_source_quality(source)
    return sources[:3]


# ===================================================================
# 缓存构建
# ===================================================================

def build_cache_from_user_data(user_data: list[dict], track: str = "") -> dict[str, dict]:
    """Convert user-submitted competitor data into the cache shape consumed by Collector."""
    cache: dict[str, dict] = {}
    for item in user_data:
        company = item.get("company", "Unknown")
        official_sources = item.get("official_sources", [])
        benchmark_sources = item.get("benchmark_sources", [])
        community_sources = item.get("community_sources", [])
        leading_sources = item.get("leading_sources", [])

        if not any(source_has_content(src, company) for src in official_sources if isinstance(src, dict)):
            official_sources = build_evidence_leads(company, track, "official") + official_sources

        if not community_sources or not any(
            source_has_content(src, company) for src in community_sources if isinstance(src, dict)
        ):
            summary = " ".join(
                str(src.get("scraped_text", ""))
                for src in official_sources
                if isinstance(src, dict)
            ).strip()
            community_sources = build_evidence_leads(company, track, "community") + [{
                "url": "",
                "label": f"{company} 用户反馈待补证据",
                "scraped_text": summary or f"{company} 暂无独立社区信号，需标记为证据缺口，但不能把竞品名称当作证据。",
            }]
        if not leading_sources or not any(
            source_has_content(src, company) for src in leading_sources if isinstance(src, dict)
        ):
            leading_sources = build_evidence_leads(company, track, "leading") + leading_sources
        cache[company] = {
            "company": company,
            "official_sources": official_sources,
            "benchmark_sources": benchmark_sources,
            "community_sources": community_sources,
            "leading_sources": leading_sources,
            "screenshots": item.get("screenshots", []),
            "metadata": item.get("metadata", {}),
        }
        metadata = cache[company]["metadata"] if isinstance(cache[company]["metadata"], dict) else {}
        candidate_sources = list(metadata.get("candidate_sources") or [])
        for bucket in SOURCE_BUCKETS:
            evidence_sources, bucket_candidates = split_candidate_sources(cache[company].get(bucket, []), company)
            cache[company][bucket] = evidence_sources
            candidate_sources.extend(bucket_candidates)
        cache[company]["metadata"] = {
            **metadata,
            "candidate_sources": candidate_sources[:16],
            "source_coverage": source_coverage_for_competitor(cache[company]),
        }
        cache[company]["metadata"]["evidence_acquisition_plan"] = build_evidence_acquisition_plan(
            cache[company],
            track,
        )
    return cache


# ===================================================================
# 搜索增强
# ===================================================================

async def enrich_competitor_inputs_with_search(
    user_data: list[dict],
    track: str,
    *,
    max_search_queries_per_competitor: int = 4,
) -> list[dict]:
    """Ensure analysis requests also use configured search API when sources are weak."""
    if not user_data:
        return user_data
    async with ClientSession(timeout=ClientTimeout(total=18)) as session:
        enriched: list[dict] = []
        for competitor in user_data:
            if not isinstance(competitor, dict):
                continue
            name = str(competitor.get("company") or competitor.get("name") or "").strip()
            # 至少覆盖三类来源后才跳过搜索；只有官网或社区链接时仍补采缺失类型。
            if not name or _concrete_source_bucket_count(competitor) >= 3:
                metadata = competitor.get("metadata") if isinstance(competitor.get("metadata"), dict) else {}
                enriched.append({
                    **competitor,
                    "metadata": {
                        **metadata,
                        "source_coverage": source_coverage_for_competitor(competitor),
                    },
                })
                continue

            sources, message = await build_source_candidates_with_search(
                session, name, track, max_queries=max_search_queries_per_competitor,
            )
            metadata = competitor.get("metadata") if isinstance(competitor.get("metadata"), dict) else {}
            relationship = str(metadata.get("relationship_type", "manual_or_auto"))
            official_sources = list(competitor.get("official_sources") or [])
            community_sources = list(competitor.get("community_sources") or [])
            leading_sources = list(competitor.get("leading_sources") or [])
            benchmark_sources = list(competitor.get("benchmark_sources") or [])
            candidate_sources = list(metadata.get("candidate_sources") or [])

            for source in sources:
                source.setdefault("source_group", source_ui_group(source))
                source.setdefault("source_status", source_status_label(source))
                if is_candidate_source_only(source):
                    candidate_sources.append(source)
                    continue
                entry = candidate_source_to_pipeline_entry(source, name, relationship)
                bucket = source_bucket_for_candidate(source)
                if bucket == "community_sources":
                    community_sources.append(entry)
                elif bucket == "leading_sources":
                    leading_sources.append(entry)
                elif bucket == "benchmark_sources":
                    benchmark_sources.append(entry)
                else:
                    official_sources.append(entry)

            enriched_competitor = {
                **competitor,
                # 先扩大搜索召回池，正文抓取后再按 Claim 相关性和域名多样性重排。
                "official_sources": official_sources[:12],
                "benchmark_sources": benchmark_sources[:8],
                "community_sources": community_sources[:8],
                "leading_sources": leading_sources[:8],
            }
            enriched_competitor["metadata"] = {
                **metadata,
                "source_enrichment": message,
                "search_provider": search_api_provider() or "",
                "source_coverage": source_coverage_for_competitor(enriched_competitor),
                "candidate_sources": candidate_sources[:12],
            }
            enriched_competitor["metadata"]["evidence_acquisition_plan"] = build_evidence_acquisition_plan(
                enriched_competitor,
                track,
            )
            enriched.append(enriched_competitor)
    return enriched
