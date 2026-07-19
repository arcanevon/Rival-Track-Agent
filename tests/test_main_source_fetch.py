"""Regression coverage for source discovery and readable text extraction."""

import asyncio

from src.intake.constants import (
    is_search_entry_url as _is_search_entry_url,
    search_query_for_competitor as _search_query_for_competitor,
    source_ui_group as _source_ui_group,
)
from src.intake.discovery import build_source_candidates as _build_source_candidates
from src.intake.enrich import enrich_competitor_inputs_with_search
from src.intake.hydrate import (
    ReadableHTMLParser as _ReadableHTMLParser,
    discover_sitemap_urls,
    fetch_readable_source,
    jina_reader_url as _jina_reader_url,
    looks_like_dynamic_shell,
    official_content_url_score,
    readable_text_quality as _readable_text_quality,
    title_from_markdown as _title_from_markdown,
)
from src.intake.quality import source_has_content
from src.intake.search import (
    community_platforms_for_context,
    rank_search_results as _rank_search_results,
    search_social_media_api,
    search_api_key as _search_api_key,
    search_api_provider as _search_api_provider,
    search_web_api as _search_web_api,
)


def test_readable_html_parser_extracts_social_page_text_from_divs_and_jsonld():
    parser = _ReadableHTMLParser()
    parser.feed(
        """
        <html>
          <head>
            <title>茶饮探店笔记</title>
            <meta property="og:description" content="用户评价：排队短，价格带集中在 8-12 元。">
            <script type="application/ld+json">
              {"@type":"SocialMediaPosting","headline":"蜜雪冰城门店体验",
               "articleBody":"消费者反馈提到新品出杯速度快，但高峰期服务波动明显。"}
            </script>
          </head>
          <body>
            <div class="post-content">
              <span>社区讨论：县城门店密度很高，低价心智稳定。</span>
            </div>
          </body>
        </html>
        """
    )

    text = parser.readable_text()

    assert "用户评价" in text
    assert "消费者反馈" in text
    assert "社区讨论" in text


def test_source_candidates_include_selectable_community_entries():
    sources = _build_source_candidates("蜜雪冰城", "奶茶市场")

    community = [src for src in sources if src.get("channel") == "community"]

    assert len(community) >= 2
    assert all(src["url"].startswith("https://") for src in community)
    assert all(src.get("direct_evidence") is False for src in community)


def test_jina_reader_url_and_markdown_quality_helpers():
    url = "https://example.com/product"
    assert _jina_reader_url(url) == "https://r.jina.ai/https://example.com/product"
    assert _title_from_markdown("Title: Product Page\n\nBody", "example.com") == "Product Page"

    strong_text = "This is a readable paragraph about a competitor roadmap. " * 8
    weak_text = "login required"
    assert _readable_text_quality(strong_text)[0] is True
    assert _readable_text_quality(weak_text)[0] is False
    assert _readable_text_quality("请求已被拦截，请稍后重试。" * 30)[0] is False


def test_dynamic_shell_uses_browser_rendering_fallback(monkeypatch):
    async def unavailable_crawl4ai(url):
        raise RuntimeError("not installed")

    async def rendered_page(url):
        return {
            "url": url,
            "title": "动态产品页",
            "text": "动态渲染后的产品能力、价格、渠道、版本更新和用户定位正文。" * 15,
            "text_length": 450,
            "fetch_method": "playwright",
            "candidate_only": False,
            "quality_note": "readable evidence text extracted",
        }

    class FakeResponse:
        status = 200
        headers = {"content-type": "text/html; charset=utf-8"}

        def __init__(self, url):
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self, errors="ignore"):
            if str(self.url).startswith("https://r.jina.ai/"):
                return "Enable JavaScript"
            return '<html><body><div id="root"></div><script src="app.js"></script></body></html>'

    class FakeSession:
        def get(self, url, allow_redirects=True):
            return FakeResponse(url)

    monkeypatch.setattr("src.intake.hydrate.fetch_with_crawl4ai", unavailable_crawl4ai)
    monkeypatch.setattr("src.intake.hydrate.fetch_with_playwright", rendered_page)
    monkeypatch.setenv("DYNAMIC_BROWSER_ENABLED", "1")

    result = asyncio.run(fetch_readable_source(FakeSession(), "https://example.com/product"))

    assert looks_like_dynamic_shell('<div id="root"></div><script src="app.js"></script>')
    assert result["fetch_method"] == "playwright"
    assert result["candidate_only"] is False


def test_official_discovery_reads_robots_nested_sitemaps_and_home_links():
    responses = {
        "https://example.com/robots.txt": "Sitemap: https://example.com/sitemap-index.xml",
        "https://example.com/sitemap-index.xml": "<sitemapindex><sitemap><loc>https://example.com/news-sitemap.xml</loc></sitemap></sitemapindex>",
        "https://example.com/news-sitemap.xml": (
            "<urlset><url><loc>https://example.com/news/2026/product-launch</loc></url>"
            "<url><loc>https://example.com/zh-cn/docs/pricing</loc></url>"
            "<url><loc>https://example.com/tr/docs/pricing</loc></url>"
            "<url><loc>https://example.com/download/app</loc></url></urlset>"
        ),
        "https://example.com/": (
            '<a href="/products/new-camera">产品</a>'
            '<a href="https://other.example/news">站外</a>'
        ),
    }

    class FakeResponse:
        headers = {"content-type": "text/html"}

        def __init__(self, url):
            self.url = url
            self.status = 200 if url in responses else 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self, errors="ignore"):
            return responses.get(self.url, "")

    class FakeSession:
        def get(self, url, allow_redirects=True):
            return FakeResponse(url)

    urls = asyncio.run(discover_sitemap_urls(FakeSession(), "example.com"))

    assert "https://example.com/news/2026/product-launch" in urls
    assert "https://example.com/products/new-camera" in urls
    assert "https://example.com/zh-cn/docs/pricing" in urls
    assert "https://example.com/tr/docs/pricing" not in urls
    assert not any("download" in url for url in urls)
    assert official_content_url_score("https://example.com/worldcup26/team/33203") == 0


def test_source_candidates_include_github_leading_indicator_entries():
    sources = _build_source_candidates("Cursor", "AI coding assistant")

    leading = [src for src in sources if src.get("channel") == "leading"]

    assert any("GitHub" in src["label"] for src in leading)
    assert all(src.get("direct_evidence") is False for src in leading)



def test_search_entries_and_candidate_text_do_not_count_as_strong_content():
    assert _is_search_entry_url("https://www.bing.com/search?q=Apple+iPhone")
    assert not source_has_content({
        "url": "https://www.bing.com/search?q=Apple+iPhone",
        "scraped_text": "自动发现候选来源。relationship_type=direct_substitute",
        "candidate_only": True,
        "evidence_status": "candidate_search",
    }, "Apple iPhone")

    assert not source_has_content({
        "url": "https://baike.baidu.com/item/%E7%8E%8B%E8%80%85%E8%8D%A3%E8%80%80",
        "scraped_text": "《王者荣耀》是由腾讯游戏天美工作室群开发并运营在 Android、iOS 平台上的 MOBA 类国产手游。",
        "candidate_only": True,
        "evidence_status": "background_text",
    }, "王者荣耀")

    assert source_has_content({
        "url": "https://www.apple.com/iphone/",
        "scraped_text": "iPhone product page with enough readable product positioning, chip, camera, pricing and channel details.",
        "fetch_method": "jina_reader",
        "evidence_status": "strong_text",
    }, "Apple iPhone")


def test_phone_source_candidates_prioritize_concrete_official_pages_without_github_noise():
    sources = _build_source_candidates("苹果 iPhone", "智能手机")

    assert sources[0]["type"] == "official-product-page"
    assert sources[0]["direct_evidence"] is True
    assert not any(src.get("channel") == "leading" and "GitHub" in src["label"] for src in sources)


def test_ai_app_source_candidates_prioritize_concrete_official_pages():
    sources = _build_source_candidates("豆包", "AI 应用")

    assert sources[0]["type"] == "official-product-page"
    assert sources[0]["url"] == "https://www.doubao.com/"
    assert sources[0]["direct_evidence"] is True


def test_productivity_source_candidates_prioritize_concrete_official_pages():
    sources = _build_source_candidates("Obsidian", "效率工具/笔记软件")

    assert sources[0]["type"] == "official-product-page"
    assert sources[0]["url"] == "https://obsidian.md/"
    assert sources[0]["direct_evidence"] is True


def test_game_source_candidates_prioritize_concrete_official_pages():
    sources = _build_source_candidates("王者荣耀", "手游")

    assert sources[0]["type"] == "official-product-page"
    assert sources[0]["url"] == "https://pvp.qq.com/"
    assert sources[0]["direct_evidence"] is True


def test_search_provider_and_source_group_classification(monkeypatch):
    for key in ("BOCHA_SEARCH_API_KEY", "WEB_SEARCH_API_KEY", "BING_SEARCH_API_KEY", "BRAVE_SEARCH_API_KEY", "SERPAPI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert _search_api_provider() == ""

    monkeypatch.setenv("BOCHA_SEARCH_API_KEY", "test-key")
    assert _search_api_provider() == "bocha"
    assert _search_api_key() == "test-key"

    assert _source_ui_group({"direct_evidence": True, "url": "https://example.com/page"}) == "direct"
    assert _source_ui_group({"type": "knowledge-base", "direct_evidence": True, "url": "https://baike.baidu.com/item/x"}) == "candidate"
    assert _source_ui_group({"direct_evidence": False, "url": "https://www.bing.com/search?q=x"}) == "candidate"
    assert _source_ui_group({"channel": "leading", "url": "https://github.com/search?q=x"}) == "leading"


def test_search_query_disambiguates_and_ranking_filters_bad_results():
    assert "Obsidian.md" in _search_query_for_competitor("Obsidian", "效率工具/笔记软件")

    ranked = _rank_search_results([
        {
            "title": "Obsidian - The Gemology Project",
            "url": "http://gemologyproject.com/wiki/index.php?title=Obsidian",
            "snippet": "Obsidian is a volcanic glassy rock.",
        },
        {
            "title": "Obsidian - Sharpen your thinking",
            "url": "https://obsidian.md/",
            "snippet": "Obsidian is a private and flexible writing app.",
        },
        {
            "title": "Obsidian 最新版分享 - 吾爱破解",
            "url": "https://www.52pojie.cn/thread-1978976-1-1.html",
            "snippet": "下载软件。",
        },
        {
            "title": "obsidian笔记官方APP(obsidian中文替代软件)v1.1.0安卓最新版",
            "url": "http://www.xlhs.com/app/36752.html",
            "snippet": "app下载。",
        },
        {
            "title": "Oracle 产品报价 _oracle价格-CSDN博客",
            "url": "https://blog.csdn.net/inthirties/article/details/4804725",
            "snippet": "Oracle 产品报价。",
        },
    ], "Obsidian", "效率工具/笔记软件")

    assert ranked[0]["url"] == "https://obsidian.md/"
    assert not any("52pojie" in item["url"] for item in ranked)
    assert not any("xlhs.com" in item["url"] for item in ranked)
    assert not any("oracle" in item["title"].lower() for item in ranked)


def test_ranking_rejects_real_chinese_download_and攻略_pages():
    """真实运行中出现过的下载/攻略标题不能只因包含品牌名而进入候选集。"""
    ranked = _rank_search_results([
        {
            "title": "云原神完整版下载-云原神最新官网游戏下载",
            "url": "https://mip.tianqing123.cn/wenda/10558111.html",
            "snippet": "云原神完整版下载。",
        },
        {
            "title": "明日方舟练什么 - 苏珊文章",
            "url": "https://susan.qqpk.cn/news_15325935.htm",
            "snippet": "明日方舟干员培养攻略。",
        },
        {
            "title": "原神官方网站 - 米哈游",
            "url": "https://ys.mihoyo.com/main/",
            "snippet": "原神官方产品、版本与活动公告。",
        },
    ], "原神", "手游")

    assert [item["url"] for item in ranked] == ["https://ys.mihoyo.com/main/"]


def test_bocha_search_response_is_parsed(monkeypatch):
    monkeypatch.setenv("BOCHA_SEARCH_API_KEY", "test-key")

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "code": 200,
                "data": {
                    "webPages": {
                        "value": [{
                            "name": "Obsidian - Sharpen your thinking",
                            "url": "https://obsidian.md/",
                            "summary": "Obsidian is a private and flexible writing app.",
                            "siteName": "obsidian.md",
                            "datePublished": "2026-01-01T00:00:00+08:00",
                        }]
                    }
                },
            }

    class FakeSession:
        def post(self, endpoint, json, headers):
            assert endpoint == "https://api.bocha.cn/v1/web-search"
            assert headers["Authorization"] == "Bearer test-key"
            assert json["summary"] is True
            return FakeResponse()

    results = asyncio.run(_search_web_api(FakeSession(), "Obsidian 官网", count=1))

    assert results == [{
        "title": "Obsidian - Sharpen your thinking",
        "url": "https://obsidian.md/",
        "snippet": "Obsidian is a private and flexible writing app.",
        "site_name": "obsidian.md",
        "date_published": "2026-01-01T00:00:00+08:00",
    }]


def test_social_search_uses_domain_include_and_normalizes_platform(monkeypatch):
    monkeypatch.setenv("BOCHA_SEARCH_API_KEY", "test-key")

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "code": 200,
                "data": {"webPages": {"value": [{
                    "name": "Cursor 使用体验与缺点",
                    "url": "https://www.zhihu.com/question/123/answer/456",
                    "summary": "用户评价 Cursor 的 Agent 功能，也提到索引速度和价格问题。",
                }]}}
            }

    class FakeSession:
        def post(self, endpoint, json, headers):
            assert json["include"] == "zhihu.com"
            return FakeResponse()

    results = asyncio.run(search_social_media_api(
        FakeSession(),
        "Cursor 用户评价",
        platforms=("zhihu",),
    ))

    assert results[0]["social_platform"] == "知乎"
    ranked = _rank_search_results(
        results,
        "Cursor",
        "AI 编程工具",
        requested_source_types=["community"],
    )
    assert ranked and ranked[0]["search_score"] >= 40


def test_community_platforms_are_selected_by_industry():
    assert "taptap" in community_platforms_for_context("手机游戏", "原神")
    assert "v2ex" in community_platforms_for_context("AI 编程工具", "Cursor")
    assert "douyin" in community_platforms_for_context("新茶饮", "喜茶")
    assert "hupu" in community_platforms_for_context("通用赛道", "竞品")


def test_bocha_social_search_uses_domain_include_and_labels_platform(monkeypatch):
    monkeypatch.setenv("BOCHA_SEARCH_API_KEY", "test-key")
    requests = []

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "code": 200,
                "data": {"webPages": {"value": [
                    {
                        "name": "Cursor 使用体验与缺点",
                        "url": "https://www.zhihu.com/question/123/answer/456",
                        "summary": "Cursor 用户评价，讨论代码 Agent 的优点、缺点和使用体验。",
                    },
                    {
                        "name": "Cursor 一个月使用体验",
                        "url": "https://www.xiaohongshu.com/explore/abc123",
                        "summary": "真实使用体验与踩坑记录。",
                    },
                ]}},
            }

    class FakeSession:
        def post(self, endpoint, json, headers):
            requests.append(json)
            assert json["include"] == "zhihu.com|xiaohongshu.com"
            assert "site:zhihu.com" in json["query"]
            assert "site:xiaohongshu.com" in json["query"]
            return FakeResponse()

    from src.intake.search import search_social_media_api

    results = asyncio.run(search_social_media_api(
        FakeSession(),
        "Cursor 用户评价",
        platforms=("zhihu", "xiaohongshu"),
    ))
    assert [item["social_platform"] for item in results] == ["知乎", "小红书"]
    assert len(requests) == 1
    ranked = _rank_search_results(
        results,
        "Cursor",
        "AI代码助手",
        requested_source_types=["community"],
    )
    assert len(ranked) == 2
    assert all(item["search_score"] >= 40 for item in ranked)


def test_social_search_drops_results_outside_platform_allowlist(monkeypatch):
    monkeypatch.setenv("BOCHA_SEARCH_API_KEY", "test-key")

    async def fake_search(session, query, count=5, freshness="noLimit", include_domains=None):
        return [{
            "title": "域外社区结果",
            "url": "https://bbs.hupu.com/123",
            "snippet": "不应进入知乎和小红书专用结果。",
        }]

    monkeypatch.setattr("src.intake.search.search_web_api", fake_search)
    from src.intake.search import search_social_media_api

    assert asyncio.run(search_social_media_api(
        object(),
        "Cursor 用户评价",
        platforms=("zhihu", "xiaohongshu"),
    )) == []


def test_discovery_merges_social_api_results_as_community_candidates(monkeypatch):
    monkeypatch.setenv("BOCHA_SEARCH_API_KEY", "test-key")

    async def fake_web_search(session, query, count=5, freshness="noLimit", include_domains=None):
        return []

    async def fake_social_search(
        session,
        query,
        count=10,
        freshness="oneMonth",
        platforms=("zhihu", "xiaohongshu"),
        competitor="",
        track="",
    ):
        return [{
            "title": "Cursor 长期使用体验与缺点",
            "url": "https://www.zhihu.com/question/123/answer/456",
            "snippet": "Cursor 用户评价，记录 Agent 功能的优点、缺点和踩坑体验。",
            "site_name": "知乎",
            "social_platform": "知乎",
        }]

    monkeypatch.setattr("src.intake.discovery.search_web_api", fake_web_search)
    monkeypatch.setattr("src.intake.discovery.search_social_media_api", fake_social_search)

    from src.intake.discovery import build_source_candidates_with_search

    sources, message = asyncio.run(
        build_source_candidates_with_search(object(), "Cursor", "AI代码助手")
    )
    social = [source for source in sources if source.get("social_platform") == "知乎"]
    assert social
    assert social[0]["channel"] == "community"
    assert social[0]["requested_source_types"] == ["community"]
    assert social[0]["evidence_slot"] == "community_pain"
    assert "bocha" in message


def test_analyze_path_enriches_weak_competitor_sources(monkeypatch):
    async def fake_build(session, name, track, count=4, **kwargs):
        return [
            {
                "type": "web-search-result",
                "label": f"{name} 官网",
                "url": f"https://example.com/{name}",
                "evidence_url": f"https://example.com/{name}",
                "direct_evidence": True,
                "source_group": "direct",
                "source_status": "需抓取验证",
            }
        ], "fake search"

    monkeypatch.setattr("src.intake.enrich.build_source_candidates_with_search", fake_build)
    competitors = [{
        "company": "FlowUs",
        "official_sources": [{"url": "https://www.bing.com/search?q=FlowUs", "label": "FlowUs 检索"}],
        "benchmark_sources": [{"url": "https://baike.baidu.com/item/FlowUs", "label": "FlowUs 百科"}],
    }]

    enriched = asyncio.run(enrich_competitor_inputs_with_search(competitors, "效率工具"))

    urls = [src["url"] for src in enriched[0]["official_sources"]]
    assert "https://example.com/FlowUs" in urls
    assert enriched[0]["metadata"]["source_enrichment"] == "fake search"
    assert "candidate_sources" in enriched[0]["metadata"]
