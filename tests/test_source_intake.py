"""Contract tests for source intake rules before evidence reaches Collector."""

import asyncio
import pytest

from src.intake.constants import THREAT_EVIDENCE_SLOTS, canonicalize_url
from src.intake.enrich import (
    build_cache_from_user_data,
    build_evidence_leads,
    enrich_competitor_inputs_with_search,
)
from src.intake.discovery import build_source_candidates
from src.intake.hydrate import (
    _attach_degraded_summary,
    _prefilter_source_for_hydration,
    fetch_readable_source,
    hydrate_sources_for_analysis,
    is_likely_download_or_landing,
)
from src.intake.plan import (
    _get_evidence_slot_configs,
    build_evidence_acquisition_plan,
    build_evidence_acquisition_plans,
    build_evidence_gaps,
)
from src.intake.quality import (
    assess_source_quality,
    build_source_quality_context,
    source_coverage_for_competitor,
    source_has_content,
)
from src.intake.search import _check_path_match, search_result_score
from src.config import load_query_templates_config, load_scoring_config
from src.config.router import resolve_industry_keyword


def test_search_enrichment_runs_when_only_one_source_bucket_has_a_url(monkeypatch):
    calls = []

    async def fake_discovery(session, name, track, **kwargs):
        calls.append((name, track))
        return ([{
            "url": "https://benchmark.example/game-review",
            "evidence_url": "https://benchmark.example/game-review",
            "label": "第三方游戏评测",
            "direct_evidence": True,
            "requested_source_types": ["benchmark"],
        }], "测试搜索")

    monkeypatch.setattr("src.intake.enrich.build_source_candidates_with_search", fake_discovery)
    rows = asyncio.run(enrich_competitor_inputs_with_search([{
        "company": "重返未来：1999",
        "official_sources": [{"url": "https://re.bluepoch.com/", "label": "官网"}],
        "benchmark_sources": [],
        "community_sources": [],
        "leading_sources": [],
    }], "游戏与互动娱乐"))

    assert calls == [("重返未来：1999", "游戏与互动娱乐")]
    assert rows[0]["benchmark_sources"][0]["url"] == "https://benchmark.example/game-review"


def test_source_has_content_rejects_candidates_and_search_entries():
    assert not source_has_content({
        "url": "https://www.bing.com/search?q=Apple+iPhone",
        "scraped_text": "自动发现候选来源。relationship_type=direct_substitute",
        "candidate_only": True,
        "evidence_status": "candidate_search",
    }, "Apple iPhone")

    assert source_has_content({
        "url": "https://www.apple.com/iphone/",
        "scraped_text": "iPhone product page with positioning, pricing, channels, and capability details.",
        "fetch_method": "jina_reader",
        "evidence_status": "strong_text",
    }, "Apple iPhone")


def test_build_cache_from_user_data_adds_missing_obcl_evidence_leads():
    cache = build_cache_from_user_data([
        {
            "company": "\u539f\u795e",
            "official_sources": [{"url": "", "label": "\u539f\u795e", "scraped_text": "\u539f\u795e"}],
            "community_sources": [],
        }
    ], "\u4e8c\u6b21\u5143\u6e38\u620f")

    official = cache["\u539f\u795e"]["official_sources"]
    community = cache["\u539f\u795e"]["community_sources"]
    leading = cache["\u539f\u795e"]["leading_sources"]
    candidate_sources = cache["\u539f\u795e"]["metadata"]["candidate_sources"]

    assert any(src["url"] == "https://ys.mihoyo.com/" for src in official)
    assert any("TapTap" in src["label"] for src in community)
    assert any("\u524d\u77bb\u4fe1\u53f7" in src["label"] for src in candidate_sources)
    assert all(src.get("candidate_only") for src in official)


@pytest.mark.parametrize(
    ("company", "official_host", "community_path"),
    [
        ("重返未来：1999", "bluepoch.com", "/app/221062/all-info"),
        ("崩坏：星穹铁道", "sr.mihoyo.com", "/app/224267"),
    ],
)
def test_game_competitors_have_official_and_community_seed_sources(
    company: str,
    official_host: str,
    community_path: str,
) -> None:
    cache = build_cache_from_user_data([{"company": company}], "游戏与互动娱乐")
    item = cache[company]
    assert any(official_host in source.get("url", "") for source in item["official_sources"])
    assert any(community_path in source.get("url", "") for source in item["community_sources"])


@pytest.mark.parametrize(
    ("company", "track", "official_host"),
    [
        ("Cursor", "AI 编程工具", "cursor.com"),
        ("蔚来汽车", "新能源汽车", "nio.cn"),
        ("大疆创新", "消费电子与智能硬件", "dji.com"),
        ("小红书", "内容社区与社交平台", "xiaohongshu.com"),
        ("支付宝", "金融科技与移动支付", "alipay.com"),
        ("微医", "医疗健康与数字医疗", "guahao.com"),
    ],
)
def test_cross_domain_products_have_official_fallback_sources(
    company: str,
    track: str,
    official_host: str,
) -> None:
    sources = build_source_candidates(company, track)
    assert any(official_host in source.get("url", "") for source in sources)


def test_failed_page_keeps_search_summary_as_non_scoring_clue():
    source = _attach_degraded_summary({
        "label": "示例搜索结果",
        "search_snippet": "这是搜索 API 返回的摘要，仅用于定位待补采内容。",
        "candidate_only": True,
        "evidence_status": "fetch_failed",
    })
    assert source["degraded"] is True
    assert source["degraded_summary"].startswith("这是搜索 API")

    context = build_source_quality_context({
        "示例竞品": {
            "metadata": {"candidate_sources": [source]},
            "official_sources": [],
            "benchmark_sources": [],
            "community_sources": [],
            "leading_sources": [],
        }
    })
    assert "usable_for_scoring=False" in context
    assert "仅用于定位待补采内容" in context


def test_evidence_leads_expose_dimension_slots_for_collector():
    leads = build_evidence_leads("\u539f\u795e", "\u4e8c\u6b21\u5143\u6e38\u620f", "leading")

    assert leads
    assert any("strategic_expansion" in lead["scraped_text"] for lead in leads)
    assert any("leading_indicators" in lead["scraped_text"] for lead in leads)


def test_source_quality_scores_strong_and_weak_sources():
    strong = assess_source_quality({
        "url": "https://obsidian.md/",
        "direct_evidence": True,
        "authority": "high",
        "scraped_text": "Readable official product text. " * 12,
    })
    weak = assess_source_quality({
        "url": "https://www.bing.com/search?q=obsidian",
        "candidate_only": True,
        "scraped_text": "search result",
    })

    assert strong["usable_for_scoring"] is True
    assert strong["score"] >= 70
    assert weak["usable_for_scoring"] is False
    assert "search entry only" in weak["reasons"]


def test_fetch_readable_source_prefers_crawl4ai(monkeypatch):
    async def fake_crawl(url):
        return {
            "url": url,
            "title": "Apple iPhone",
            "text": "iPhone official product page with pricing, features, channels, and capability details. " * 4,
            "text_length": 320,
            "fetch_method": "crawl4ai",
            "candidate_only": False,
            "quality_note": "readable evidence text extracted",
            "source_quality": {"usable_for_scoring": True, "score": 85, "status": "strong_text"},
        }

    class NoNetworkSession:
        def get(self, *args, **kwargs):
            raise AssertionError("fallback network should not be called")

    monkeypatch.setattr("src.intake.hydrate.fetch_with_crawl4ai", fake_crawl)

    result = asyncio.run(fetch_readable_source(NoNetworkSession(), "https://www.apple.com/iphone/"))

    assert result["fetch_method"] == "crawl4ai"
    assert result["candidate_only"] is False


def test_source_has_content_rejects_readable_but_irrelevant_pages():
    irrelevant = {
        "url": "https://www.usbmi.com/manual/xiaomi-watch.html",
        "label": "小米智能手表说明书-USB迷专注于互联网分享",
        "direct_evidence": True,
        "authority": "medium",
        "fetch_method": "html_parser",
        "evidence_status": "strong_text",
        "scraped_text": (
            "电影迷 专注于电影,影评，解说，视频,免费分享。网页整改中，不用太久，"
            "我们会回来，We love you, We will be back soon。"
        ) * 8,
    }
    irrelevant["source_quality"] = assess_source_quality(irrelevant)

    assert irrelevant["source_quality"]["usable_for_scoring"] is True
    assert not source_has_content(irrelevant, "小米手表/手环")


def test_build_cache_moves_irrelevant_concrete_pages_to_candidates():
    cache = build_cache_from_user_data([{
        "company": "小米手表/手环",
        "official_sources": [{
            "url": "https://www.usbmi.com/manual/xiaomi-watch.html",
            "label": "小米智能手表说明书-USB迷专注于互联网分享",
            "direct_evidence": True,
            "authority": "medium",
            "fetch_method": "html_parser",
            "evidence_status": "strong_text",
            "scraped_text": (
                "电影迷 专注于电影,影评，解说，视频,免费分享。网页整改中，不用太久，"
                "我们会回来，We love you, We will be back soon。"
            ) * 8,
        }],
        "community_sources": [],
        "leading_sources": [],
    }], "智能手表")

    urls = [src["url"] for src in cache["小米手表/手环"]["official_sources"]]
    candidate_urls = [src["url"] for src in cache["小米手表/手环"]["metadata"]["candidate_sources"]]

    assert "https://www.usbmi.com/manual/xiaomi-watch.html" not in urls
    assert "https://www.usbmi.com/manual/xiaomi-watch.html" in candidate_urls


def test_source_coverage_reports_obcl_statuses():
    competitor = {
        "official_sources": [{
            "url": "https://obsidian.md/",
            "direct_evidence": True,
            "authority": "high",
            "scraped_text": "Readable official product text. " * 12,
        }],
        "community_sources": [{
            "url": "https://www.bing.com/search?q=obsidian+review",
            "candidate_only": True,
        }],
        "leading_sources": [],
    }

    coverage = source_coverage_for_competitor(competitor)

    assert coverage["official"]["status"] == "covered"
    assert coverage["community"]["status"] == "candidate_only"
    assert coverage["leading"]["status"] == "missing"


def test_canonicalize_url_removes_tracking_and_duplicate_slashes():
    assert canonicalize_url("HTTPS://Example.com/path/?utm_source=x#section") == "https://example.com/path"


def test_source_quality_context_summarizes_coverage_and_scores():
    cache = {
        "Obsidian": {
            "official_sources": [{
                "url": "https://obsidian.md/",
                "label": "Obsidian official",
                "direct_evidence": True,
                "authority": "high",
                "scraped_text": "Readable official product text. " * 12,
            }],
            "community_sources": [{
                "url": "https://www.bing.com/search?q=obsidian+review",
                "label": "Obsidian community search",
                "candidate_only": True,
            }],
        }
    }

    context = build_source_quality_context(cache)

    assert "Obsidian" in context
    assert "coverage" in context
    assert "quality_score" in context
    assert "usable_for_scoring" in context


def test_evidence_acquisition_plan_maps_coverage_gaps_to_threat_slots():
    plan = build_evidence_acquisition_plan({
        "company": "Cursor",
        "official_sources": [{
            "url": "https://cursor.com/",
            "direct_evidence": True,
            "authority": "high",
            "scraped_text": "Readable official product text. " * 12,
        }],
        "community_sources": [{
            "url": "https://www.bing.com/search?q=cursor+complaints",
            "candidate_only": True,
        }],
        "leading_sources": [],
    }, "AI coding assistant")

    assert plan["competitor"] == "Cursor"
    assert "community_pain" in plan["needed_slots"]
    assert "github_release_velocity" in plan["needed_slots"]
    assert "community" in plan["required_source_types"]
    assert "leading" in plan["required_source_types"]
    assert plan["minimum_strong_sources"] == 2
    assert any(item["slot"] == "community_pain" for item in plan["queries"])


def test_evidence_acquisition_plans_are_in_source_quality_context():
    cache = build_cache_from_user_data([{
        "company": "Cursor",
        "official_sources": [],
        "community_sources": [],
        "leading_sources": [],
    }], "AI coding assistant")
    plans = build_evidence_acquisition_plans(cache, "AI coding assistant")
    context = build_source_quality_context(cache)

    assert "Cursor" in plans
    assert "needed_slots" in context
    assert "required_source_types" in context


def test_evidence_gaps_are_structured_for_downstream_actions():
    cache = build_cache_from_user_data([{
        "company": "Cursor",
        "official_sources": [],
        "community_sources": [],
        "leading_sources": [],
    }], "AI coding assistant")

    gaps = build_evidence_gaps(cache, "AI coding assistant")

    assert gaps
    assert all("competitor" in gap and "slot" in gap and "query" in gap for gap in gaps)
    assert any(gap["slot"] == "community_pain" for gap in gaps)
    official = next(gap for gap in gaps if gap["slot"] == "official_capability")
    community = next(gap for gap in gaps if gap["slot"] == "community_pain")
    assert official["source_types"] == ["official"]
    assert community["source_types"] == ["community"]
    assert "freshness" in official


# =============================================================================
# YAML 配置加载与配置驱动路径测试
# =============================================================================


def test_load_scoring_config_loads_yaml():
    """load_scoring_config should parse scoring.yaml into a dict."""
    cfg = load_scoring_config()
    assert isinstance(cfg, dict)
    assert "scoring" in cfg
    assert cfg["scoring"].get("name_token_match") == 30


def test_load_scoring_config_fallback_on_missing(monkeypatch):
    """load_scoring_config should return empty dict when scoring.yaml is missing."""
    import src.config as _cfg

    monkeypatch.setattr(_cfg, "_scoring_config", None)
    monkeypatch.setattr("src.config._load_yaml", lambda name: {})
    cfg = _cfg.load_scoring_config()
    assert cfg == {}


def test_load_query_templates_config_loads_yaml():
    """load_query_templates_config should parse query_templates.yaml into a dict."""
    cfg = load_query_templates_config()
    assert isinstance(cfg, dict)
    assert "slots" in cfg
    assert "official_capability" in cfg["slots"]


def test_load_query_templates_config_fallback_on_missing(monkeypatch):
    """load_query_templates_config should return empty dict when YAML missing."""
    import src.config as _cfg

    monkeypatch.setattr(_cfg, "_query_templates_config", None)
    monkeypatch.setattr("src.config._load_yaml", lambda name: {})
    cfg = _cfg.load_query_templates_config()
    assert cfg == {}


def test_check_path_match_matches_substring():
    """_check_path_match should return True when pattern is in URL path."""
    assert _check_path_match("https://example.com/features/pricing", ["features"])
    assert _check_path_match("https://example.com/docs/api/v2", ["docs"])
    assert _check_path_match("https://example.com/blog/announcement", ["blog"])


def test_check_path_match_no_match():
    """_check_path_match should return False when no pattern is in URL path."""
    assert not _check_path_match("https://example.com/about", ["features", "pricing"])
    assert not _check_path_match("https://example.com/", ["docs"])


def test_is_likely_download_or_landing_search_entry():
    """Search entry URLs should be flagged as download/landing."""
    assert is_likely_download_or_landing("https://www.bing.com/search?q=test")


def test_is_likely_download_or_landing_download_fragment():
    """URLs with download/install/setup fragment should be flagged."""
    assert is_likely_download_or_landing("https://example.com/page#download")
    assert is_likely_download_or_landing("https://example.com/page#install")
    assert is_likely_download_or_landing("https://example.com/page#setup")


def test_is_likely_download_or_landing_bare_homepage():
    """Bare homepage or /index.html URLs should be flagged."""
    assert is_likely_download_or_landing("https://example.com/")
    assert is_likely_download_or_landing("https://example.com/index.html")
    assert is_likely_download_or_landing("https://example.com/index.php")


def test_is_likely_download_or_landing_normal_page():
    """Normal content page URLs should NOT be flagged."""
    assert not is_likely_download_or_landing("https://example.com/features")
    assert not is_likely_download_or_landing("https://example.com/pricing")
    assert not is_likely_download_or_landing("https://example.com/docs/changelog")


def test_prefilter_source_for_hydration_download_url():
    """Download/landing URLs should get candidate_only + quality_note."""
    result = _prefilter_source_for_hydration({
        "url": "https://example.com/download/setup.exe",
        "label": "setup",
    })
    assert result.get("candidate_only") is True
    assert result.get("evidence_status") == "candidate_search"
    assert "下载页/落地页预过滤" in result.get("quality_note", "")


def test_prefilter_source_for_hydration_normal_url():
    """Normal content URLs should NOT be modified by prefilter."""
    result = _prefilter_source_for_hydration({
        "url": "https://example.com/features",
        "label": "features",
        "direct_evidence": True,
    })
    assert not result.get("candidate_only")
    assert not result.get("evidence_status")


def test_prefilter_source_for_hydration_empty_url():
    """Sources without a URL should be returned unchanged."""
    src = {"label": "no url"}
    result = _prefilter_source_for_hydration(src)
    assert result == src


def test_get_evidence_slot_configs_from_yaml(monkeypatch):
    """_get_evidence_slot_configs should load template/exclude_terms from YAML."""
    monkeypatch.setattr("src.config._query_templates_config", None)
    slots = _get_evidence_slot_configs()
    assert "official_capability" in slots
    assert slots["official_capability"]["template"].startswith("{name}")
    assert "exclude_terms" in slots["official_capability"]
    assert "download" in slots["official_capability"]["exclude_terms"]


def test_get_evidence_slot_configs_fallback(monkeypatch):
    """_get_evidence_slot_configs should fall back to THREAT_EVIDENCE_SLOTS."""
    monkeypatch.setattr("src.config._query_templates_config", None)
    monkeypatch.setattr("src.intake.plan.load_query_templates_config", lambda *args: {})
    slots = _get_evidence_slot_configs()
    assert slots == THREAT_EVIDENCE_SLOTS


def test_search_result_score_content_path_bonus():
    """URLs with content paths (features/pricing/docs) should score higher."""
    score_with = search_result_score({
        "url": "https://example.com/features/unique503",
        "title": "unique503 Features",
        "snippet": "product info",
    }, "unique503", "test")
    score_without = search_result_score({
        "url": "https://example.com/about/unique503",
        "title": "unique503 About",
        "snippet": "product info",
    }, "unique503", "test")
    assert score_with > score_without


def test_search_result_score_aggregator_domain_penalty():
    """Aggregator domains (sohu, 163, sina) should get scoring penalty."""
    score_agg = search_result_score({
        "url": "https://www.sohu.com/a/unique888",
        "title": "unique888 news",
        "snippet": "some article",
    }, "unique888", "test")
    score_normal = search_result_score({
        "url": "https://example.com/a/unique888",
        "title": "unique888 news",
        "snippet": "some article",
    }, "unique888", "test")
    assert score_agg < score_normal


def test_search_result_score_content_farm_penalty():
    """Content farm domains (CSDN, zhihu, jianshu) should get scoring penalty."""
    score_csdn = search_result_score({
        "url": "https://blog.csdn.net/unique999/article/123",
        "title": "unique999 Tutorial",
        "snippet": "technical guide",
    }, "unique999", "test")
    score_normal = search_result_score({
        "url": "https://example.com/article/unique999",
        "title": "unique999 Tutorial",
        "snippet": "technical guide",
    }, "unique999", "test")
    assert score_csdn < score_normal


def test_build_evidence_acquisition_plan_uses_template_with_exclude(monkeypatch):
    """Query templates from query_templates.yaml should render with exclude_terms."""
    monkeypatch.setattr("src.config._query_templates_config", None)
    plan = build_evidence_acquisition_plan({
        "company": "TemplateTestCo",
        "official_sources": [],
        "community_sources": [],
        "leading_sources": [],
    }, "test")
    official_query = None
    for q in plan.get("queries", []):
        if isinstance(q, dict) and q.get("slot") == "official_capability":
            official_query = q
            break
    assert official_query is not None, "official_capability query should be in plan"
    assert "{name}" not in official_query["query"], "template should be rendered"
    assert "TemplateTestCo" in official_query["query"]
    assert any(
        term in official_query["query"]
        for term in ("-download", "-安装", "-app下载")
    ), "exclude_terms should appear as negative keywords"


def test_hydrate_sources_prefilter_and_fetch_flow(monkeypatch):
    """hydrate_sources_for_analysis should pre-filter, skip search entries, fetch real URLs."""
    async def mock_fetch(session, url, *args, **kwargs):
        return {
            "url": url,
            "text": "原神官方产品版本更新，介绍开放世界功能、角色能力和技术特性。" * 12,
            "fetch_method": "jina_reader",
            "candidate_only": False,
            "quality_note": "readable evidence text extracted",
        }

    async def mock_discover(session, domain):
        return []

    monkeypatch.setattr("src.intake.hydrate.fetch_readable_source", mock_fetch)
    monkeypatch.setattr("src.intake.hydrate.discover_sitemap_urls", mock_discover)

    user_data = [{
        "company": "原神",
        "official_sources": [
            {"label": "Download Page", "url": "https://example.com/download/setup.exe"},
            {"label": "Features", "url": "https://ys.mihoyo.com/main/features"},
            {"label": "Search Entry", "url": "https://www.bing.com/search?q=hydrate"},
        ],
        "benchmark_sources": [],
        "community_sources": [],
        "leading_sources": [],
    }]

    result = asyncio.run(hydrate_sources_for_analysis(user_data, "test"))
    official = result[0]["official_sources"]
    candidates = result[0]["metadata"]["candidate_sources"]

    # 下载页应在预筛选阶段标记为候选来源
    download = [s for s in candidates if "Download" in s.get("label", "")]
    assert download, "Download source should be in results"
    assert download[0].get("candidate_only") is True
    assert download[0].get("evidence_status") == "candidate_search"

    # 搜索入口应标记为候选搜索来源
    search = [s for s in candidates if "Search" in s.get("label", "")]
    assert search, "Search entry source should be in results"
    assert search[0].get("evidence_status") == "candidate_search"

    # 功能页面应成功抓取
    features = [s for s in official if "Features" in s.get("label", "")]
    assert features, "Features source should be in results"
    assert features[0].get("fetch_method") == "jina_reader"
    assert features[0].get("candidate_only") is False


def test_game_choice_routes_to_gaming_templates() -> None:
    """前端的组合领域标签也必须命中游戏行业，而不是降级到软件模板。"""
    assert resolve_industry_keyword("游戏与互动娱乐", "洛克王国手游") == "gaming"


def test_official_homepage_is_fetched_instead_of_discarded(monkeypatch) -> None:
    """官网首页可能就是产品内容页，不能仅因路径为根目录而跳过正文抓取。"""
    fetched_urls: list[str] = []

    async def mock_fetch(session, url, *args, **kwargs):
        fetched_urls.append(url)
        return {
            "url": url,
            "title": "明日方舟官方网站",
            "text": "明日方舟官方介绍版本活动、塔防玩法、干员养成与近期内容更新。" * 15,
            "fetch_method": "jina_reader",
            "candidate_only": False,
            "quality_note": "readable evidence text extracted",
        }

    async def mock_discover(session, domain):
        return []

    monkeypatch.setattr("src.intake.hydrate.fetch_readable_source", mock_fetch)
    monkeypatch.setattr("src.intake.hydrate.discover_sitemap_urls", mock_discover)

    result = asyncio.run(hydrate_sources_for_analysis([{
        "company": "明日方舟",
        "official_sources": [{
            "label": "明日方舟官方网站",
            "url": "https://ak.hypergryph.com/",
            "direct_evidence": True,
        }],
        "benchmark_sources": [],
        "community_sources": [],
        "leading_sources": [],
    }], "游戏与互动娱乐"))

    assert fetched_urls == ["https://ak.hypergryph.com/"]
    assert result[0]["official_sources"][0]["candidate_only"] is False
