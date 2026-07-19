"""来源正文抓取与补全。

Fetches readable text from concrete URLs via Crawl4AI, Jina Reader, or a
fallback HTML parser.  Also handles pre-fetch screening, sitemap discovery,
and the full hydration pipeline that iterates over all source buckets.
"""

import json
import logging
import os
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from aiohttp import ClientSession, ClientTimeout

from src.config import load_scoring_config

from .constants import (
    READABLE_FETCH_HEADERS,
    SOURCE_BUCKETS,
    canonicalize_url,
    is_search_entry_url,
)
from .quality import assess_source_quality, source_coverage_for_competitor
from .evidence_relevance import (
    classify_actual_source_type,
    evaluate_evidence,
    evidence_relevance_metrics,
    rerank_evidence_sources,
)
from .platform_adapters import adapt_platform_page
from .url_security import is_safe_public_url


logger = logging.getLogger(__name__)

_DEFAULT_SLOT_BY_BUCKET = {
    "official_sources": "official_capability",
    "benchmark_sources": "third_party_benchmark",
    "community_sources": "community_pain",
    "leading_sources": "strategic_expansion_signal",
}

_BUCKET_BY_ACTUAL_TYPE = {
    "official": "official_sources",
    "benchmark": "benchmark_sources",
    "community": "community_sources",
    "leading": "leading_sources",
}


def _apply_evidence_verdict(src: dict, company: str, bucket: str) -> dict:
    """把抓取后的相关性判定写回来源，并同步强弱状态。"""
    slot = str(src.get("evidence_slot") or _DEFAULT_SLOT_BY_BUCKET[bucket])
    src["evidence_slot"] = slot
    verdict = evaluate_evidence(src, company, slot)
    src["evidence_verdict"] = verdict.as_dict()
    src["actual_source_type"] = verdict.actual_source_type
    src["candidate_only"] = not verdict.accepted
    src["evidence_status"] = "strong_text" if verdict.accepted else "rejected_irrelevant"
    if verdict.reject_reason:
        src["quality_note"] = verdict.reject_reason
    if verdict.supporting_quote:
        src["supporting_quote"] = verdict.supporting_quote
    src["source_quality"] = assess_source_quality(src)
    return src


def _attach_degraded_summary(src: dict) -> dict:
    """保留搜索摘要供人工补采，但明确禁止把它当作强证据。"""
    snippet = str(src.get("search_snippet") or "").strip()
    if snippet:
        src["degraded_summary"] = snippet[:800]
        src["degraded"] = True
    return src


def _organize_hydrated_sources(next_item: dict, company: str) -> tuple[dict, list[dict]]:
    """按实际来源类型重建 O/B/C/L 桶，并把拒绝项移入候选区。"""
    accepted_by_bucket = {bucket: [] for bucket in SOURCE_BUCKETS}
    rejected: list[dict] = []
    evaluated: list[dict] = []
    for original_bucket in SOURCE_BUCKETS:
        for src in next_item.get(original_bucket, []) or []:
            if not isinstance(src, dict):
                continue
            if isinstance(src.get("evidence_verdict"), dict):
                evaluated.append(src)
            verdict = src.get("evidence_verdict") if isinstance(src.get("evidence_verdict"), dict) else {}
            actual_type = str(verdict.get("actual_source_type") or src.get("actual_source_type") or "unknown")
            target_bucket = _BUCKET_BY_ACTUAL_TYPE.get(actual_type)
            if verdict.get("accepted") is True and target_bucket:
                accepted_by_bucket[target_bucket].append(src)
            else:
                rejected.append(src)

    for bucket in SOURCE_BUCKETS:
        next_item[bucket] = rerank_evidence_sources(
            accepted_by_bucket[bucket], limit=6, per_domain_limit=1,
        )
    return evidence_relevance_metrics(evaluated), rejected


# ===================================================================
# Crawl4AI 辅助函数
# ===================================================================

def crawl4ai_enabled() -> bool:
    """未显式关闭 ``CRAWL4AI_ENABLED`` 时返回真。"""
    return os.environ.get("CRAWL4AI_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def dynamic_browser_enabled() -> bool:
    """判断是否允许使用可选浏览器渲染动态网页。"""
    return os.environ.get("DYNAMIC_BROWSER_ENABLED", "1").strip().lower() not in {
        "0", "false", "no",
    }


def looks_like_dynamic_shell(raw_html: str, readable_text: str = "") -> bool:
    """识别 HTTP 成功但正文仍是前端 JavaScript 外壳的页面。"""
    raw = (raw_html or "").lower()
    text = re.sub(r"\s+", " ", readable_text or "").strip().lower()
    markers = (
        "enable javascript", "please enable javascript", "javascript is required",
        "__next_data__", "__nuxt__", "data-reactroot", "id=\"root\"",
        "id='root'", "id=\"app\"", "id='app'", "ng-version=",
    )
    marker_hit = any(marker in raw or marker in text for marker in markers)
    script_heavy = raw.count("<script") >= 5 and len(text) < 400
    empty_mount = bool(re.search(r"<(?:div|main)[^>]+id=[\"'](?:root|app|__next)[\"'][^>]*>\s*</", raw))
    return marker_hit or script_heavy or empty_mount


async def fetch_with_playwright(url: str) -> dict:
    """使用可选 Playwright/Chromium 渲染动态页面并提取可见正文。"""
    if not dynamic_browser_enabled():
        raise RuntimeError("dynamic browser disabled")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed; install requirements-crawl.txt and run "
            "`playwright install chromium`"
        ) from exc

    timeout_ms = max(5_000, int(os.environ.get("DYNAMIC_BROWSER_TIMEOUT_MS", "25000")))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                user_agent=READABLE_FETCH_HEADERS["User-Agent"],
                locale="zh-CN",
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8_000))
            except Exception:
                pass
            title = (await page.title()).strip() or urlparse(url).netloc
            text = re.sub(r"\n{3,}", "\n\n", (await page.locator("body").inner_text()).strip())
        finally:
            await browser.close()

    # 拦截提示有时只出现在 document.title，必须与正文一起验收。
    strong, quality_note = readable_text_quality(f"{title}\n{text}")
    result = {
        "url": url,
        "title": title,
        "text": text[:12000],
        "text_length": len(text),
        "fetch_method": "playwright",
        "candidate_only": not strong,
        "quality_note": quality_note,
    }
    result["source_quality"] = assess_source_quality({
        **result,
        "scraped_text": result["text"],
        "direct_evidence": True,
    })
    return result


def _extract_crawl4ai_markdown(result: object) -> str:
    """从 Crawl4AI 结果中提取质量最好的 Markdown 正文。"""
    markdown = getattr(result, "markdown", "")
    if isinstance(markdown, str):
        return markdown
    for attr in ("fit_markdown", "raw_markdown", "markdown"):
        value = getattr(markdown, attr, "")
        if isinstance(value, str) and value.strip():
            return value
    for attr in ("fit_markdown", "raw_markdown", "cleaned_html"):
        value = getattr(result, attr, "")
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _extract_crawl4ai_title(result: object, fallback: str) -> str:
    """从 Crawl4AI 结果元数据中提取网页标题。"""
    metadata = getattr(result, "metadata", {})
    if isinstance(metadata, dict):
        for key in ("title", "og:title", "name"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


async def fetch_with_crawl4ai(url: str) -> dict:
    """可选依赖可用时，使用 Crawl4AI 抓取具体网页。"""
    if not crawl4ai_enabled():
        raise RuntimeError("Crawl4AI disabled")
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError as exc:
        raise RuntimeError("Crawl4AI is not installed") from exc

    parsed = urlparse(url)
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url)

    markdown = _extract_crawl4ai_markdown(result)
    if not markdown.strip():
        raise RuntimeError("Crawl4AI extracted no readable markdown")

    strong, quality_note = readable_text_quality(markdown)
    title = _extract_crawl4ai_title(result, title_from_markdown(markdown, parsed.netloc))
    crawl_result = {
        "url": url,
        "title": title,
        "text": markdown[:12000].strip(),
        "text_length": len(markdown.strip()),
        "fetch_method": "crawl4ai",
        "candidate_only": not strong,
        "quality_note": quality_note,
    }
    crawl_result["source_quality"] = assess_source_quality({
        **crawl_result,
        "scraped_text": crawl_result["text"],
        "direct_evidence": True,
    })
    return crawl_result


# ===================================================================
# 可读正文辅助函数
# ===================================================================

def readable_text_quality(text: str) -> tuple[bool, str]:
    """判断提取正文能否作为有效证据。"""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) < 200:
        return False, "readable text shorter than 200 characters"
    weak_markers = (
        "enable javascript", "access denied", "captcha", "403 forbidden", "please log in",
        "request blocked", "too many requests", "请求已被拦截", "访问被拒绝", "拒绝访问",
        "安全验证", "请输入验证码", "登录后查看", "无权访问",
    )
    lowered = cleaned.lower()
    if any(marker in lowered for marker in weak_markers):
        return False, "reader output looks blocked or login-gated"
    return True, "readable evidence text extracted"


def title_from_markdown(text: str, fallback: str) -> str:
    """从 Markdown 正文中提取首个标题行。"""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Title:"):
            return stripped.removeprefix("Title:").strip() or fallback
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def jina_reader_url(url: str) -> str:
    """为公开 HTTP(S) 地址生成 Jina Reader 地址。"""
    return "https://r.jina.ai/" + url.strip()


# ===================================================================
# HTML 可读正文解析器
# ===================================================================

class ReadableHTMLParser(HTMLParser):
    """无额外依赖的网页标题、元数据和正文提取器。"""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.meta_title = ""
        self._capture: str | None = None
        self._capture_json_script = False
        self._json_script_parts: list[str] = []
        self._skip_depth = 0
        self._parts: list[str] = []
        self._structured_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "script":
            script_type = (attrs_dict.get("type") or "").lower()
            if "ld+json" in script_type:
                self._capture_json_script = True
                self._json_script_parts = []
            else:
                self._skip_depth += 1
            return
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            if name in {"description", "og:description"} and not self.meta_description:
                self.meta_description = attrs_dict.get("content", "").strip()
            if name in {"title", "og:title", "twitter:title"} and not self.meta_title:
                self.meta_title = attrs_dict.get("content", "").strip()
        if tag in {"title", "h1", "h2", "h3", "p", "li", "article"}:
            self._capture = tag

    def handle_endtag(self, tag):
        if tag == "script" and self._capture_json_script:
            self._capture_json_script = False
            self._extract_structured_text("".join(self._json_script_parts))
            self._json_script_parts = []
            return
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._capture == tag:
            self._capture = None

    def handle_data(self, data):
        if self._capture_json_script:
            self._json_script_parts.append(data or "")
            return
        if self._skip_depth:
            return
        text = re.sub(r"\s+", " ", data or "").strip()
        if len(text) < 2:
            return
        if self._capture == "title" and not self.title:
            self.title = text
            return
        self._parts.append(text)

    def _extract_structured_text(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        text_keys = {
            "headline", "name", "description", "articleBody", "text",
            "reviewBody", "caption", "commentText", "keywords",
        }

        def walk(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key in text_keys and isinstance(nested, str):
                        cleaned = re.sub(r"\s+", " ", nested).strip()
                        if cleaned:
                            self._structured_parts.append(cleaned)
                    else:
                        walk(nested)
            elif isinstance(value, list):
                for nested in value:
                    walk(nested)

        walk(data)

    def readable_text(self, limit: int = 4000) -> str:
        seen: set[str] = set()
        parts: list[str] = []
        for item in [self.title, self.meta_title, self.meta_description,
                     *self._structured_parts, *self._parts]:
            text = item.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            parts.append(text)
            if len("\n".join(parts)) >= limit:
                break
        return "\n".join(parts)[:limit].strip()


# 为现有测试保留的向后兼容私有名称
_ReadableHTMLParser = ReadableHTMLParser


# ===================================================================
# 抓取前筛选
# ===================================================================

def is_likely_download_or_landing(url: str) -> bool:
    """预筛选下载页或仅指向首页锚点的地址。"""
    if is_search_entry_url(url):
        return True
    parsed = urlparse(url)
    path = parsed.path.lower().rstrip("/")
    fragment = parsed.fragment.lower()
    if fragment in {"download", "install", "setup", "下载", "安装"}:
        return True
    if not path or path in {"/index.html", "/index.php", "/index.htm", "/home"}:
        return True
    cfg = load_scoring_config()
    download_paths = []
    if cfg:
        download_paths = cfg.get("path_rules", {}).get("download_paths", [])
    for dp in download_paths:
        if dp in path.split("/"):
            return True
    return False


def _is_bare_homepage(url: str) -> bool:
    """判断 URL 是否为站点首页；官网首页允许进入正文抓取流程。"""
    path = urlparse(url).path.lower().rstrip("/")
    return not path or path in {"/index.html", "/index.php", "/index.htm", "/home"}


_OFFICIAL_CONTENT_PATH_HINTS = (
    "features", "pricing", "changelog", "blog", "docs", "guide", "updates",
    "release-notes", "news", "article", "articles", "product", "products",
    "announcement", "announcements", "press", "media", "solutions", "cases",
    "events", "公告", "新闻", "产品", "动态", "资讯", "版本", "活动",
)


def _same_site(host: str, domain: str) -> bool:
    """忽略 www 前缀比较站点域名。"""
    return host.lower().removeprefix("www.") == domain.lower().removeprefix("www.")


def official_content_url_score(url: str, content_paths: list[str] | None = None) -> int:
    """按官网内容意图为 URL 排序，过滤下载页和低信息根路径。"""
    parsed = urlparse(url)
    path = unescape(parsed.path).lower().rstrip("/")
    if not path or is_likely_download_or_landing(url):
        return -100
    hints = tuple(dict.fromkeys([*_OFFICIAL_CONTENT_PATH_HINTS, *(content_paths or [])]))
    hint_hits = sum(1 for hint in hints if str(hint).lower() in path)
    if hint_hits == 0:
        return 0
    score = hint_hits * 20
    if re.search(r"/20\d{2}(?:/|-)\d{1,2}", path):
        score += 8
    if path.count("/") >= 2:
        score += 4
    if parsed.query:
        score -= 3
    first_segment = next((segment for segment in path.split("/") if segment), "")
    preferred_locales = {"cn", "zh-cn", "zh-hans", "zh"}
    if first_segment in preferred_locales:
        score += 6
    elif re.fullmatch(r"[a-z]{2}(?:-[a-z]{2,4})?", first_segment):
        score -= 15
    return score


def _official_content_semantic_key(url: str) -> str:
    """移除语言前缀生成语义路径键，避免多语言镜像挤占候选配额。"""
    path = unescape(urlparse(url).path).lower().rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if segments and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2,4})?", segments[0]):
        segments = segments[1:]
    return "/".join(segments)


def _extract_locations(xml: str) -> list[str]:
    return [unescape(value.strip()) for value in re.findall(r"<loc\b[^>]*>(.*?)</loc>", xml, re.I | re.S)]


def _extract_homepage_links(html: str, base_url: str, domain: str) -> list[str]:
    """从官网首页抽取同域内容链接，作为 sitemap 缺失时的降级发现。"""
    links: list[str] = []
    for href in re.findall(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"']", html, re.I):
        absolute = urljoin(base_url, unescape(href.strip()))
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not _same_site(parsed.netloc, domain):
            continue
        links.append(absolute.split("#", 1)[0])
    return links


async def discover_sitemap_urls(session: ClientSession, domain: str) -> list[str]:
    """从 robots、多级 sitemap 和首页链接发现高价值官网内容页。"""
    cfg = load_scoring_config()
    content_paths = list(cfg.get("path_rules", {}).get("content_paths", [])) if cfg else []
    base_url = f"https://{domain}/"
    sitemap_queue = [
        f"https://{domain}/sitemap.xml",
        f"https://{domain}/sitemap_index.xml",
        f"https://{domain}/sitemap-index.xml",
    ]
    try:
        async with session.get(f"https://{domain}/robots.txt", allow_redirects=True) as resp:
            if resp.status < 400:
                robots = await resp.text(errors="ignore")
                sitemap_queue = [
                    *re.findall(r"^\s*Sitemap:\s*(\S+)", robots, re.I | re.M),
                    *sitemap_queue,
                ]
    except Exception:
        pass

    seen_sitemaps: set[str] = set()
    discovered: set[str] = set()
    while sitemap_queue and len(seen_sitemaps) < 8:
        sitemap_url = sitemap_queue.pop(0)
        canonical = canonicalize_url(sitemap_url)
        if not canonical or canonical in seen_sitemaps:
            continue
        seen_sitemaps.add(canonical)
        try:
            async with session.get(sitemap_url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    continue
                xml = await resp.text(errors="ignore")
        except Exception:
            continue
        for location in _extract_locations(xml):
            parsed = urlparse(location)
            if not _same_site(parsed.netloc, domain):
                continue
            if parsed.path.lower().endswith((".xml", ".xml.gz")):
                sitemap_queue.append(location)
            elif official_content_url_score(location, content_paths) > 0:
                discovered.add(location)

    # sitemap 缺失或内容稀少时，从首页导航和卡片链接补充候选。
    if len(discovered) < 5:
        try:
            async with session.get(base_url, allow_redirects=True) as resp:
                if resp.status < 400:
                    html = await resp.text(errors="ignore")
                    for location in _extract_homepage_links(html, str(resp.url), domain):
                        if official_content_url_score(location, content_paths) > 0:
                            discovered.add(location)
        except Exception:
            pass

    ranked = sorted(
        discovered,
        key=lambda url: (official_content_url_score(url, content_paths), url),
        reverse=True,
    )
    deduped: list[str] = []
    semantic_paths: set[str] = set()
    for url in ranked:
        key = _official_content_semantic_key(url)
        if key and key in semantic_paths:
            continue
        if key:
            semantic_paths.add(key)
        deduped.append(url)
    return deduped[:20]


def _prefilter_source_for_hydration(source: dict, *, allow_homepage: bool = False) -> dict:
    """抓取前筛选单个来源并返回副本。"""
    src = dict(source)
    url = str(src.get("url", "")).strip()
    if not url:
        return src
    if is_likely_download_or_landing(url) and not (allow_homepage and _is_bare_homepage(url)):
        src["candidate_only"] = True
        src["evidence_status"] = "candidate_search"
        src.setdefault("quality_note", "")
        if not src["quality_note"]:
            src["quality_note"] = "下载页/落地页预过滤，跳过抓取。"
        else:
            src["quality_note"] += "; 下载页/落地页预过滤，跳过抓取。"
        src["source_quality"] = assess_source_quality(src)
    return src


# ===================================================================
# 单网址抓取器
# ===================================================================

async def fetch_readable_source(
    session: ClientSession,
    url: str,
    allow_dynamic_browser: bool = True,
) -> dict:
    """依次尝试 Crawl4AI、Jina Reader、本地解析器和动态浏览器提取正文。"""
    if not is_safe_public_url(url):
        raise RuntimeError(f"URL blocked by SSRF protection: {urlparse(url).netloc}")
    parsed = urlparse(url)
    reader_url = jina_reader_url(url)
    fallback_error = ""
    weak_reader_result: dict | None = None

    try:
        crawl_result = await fetch_with_crawl4ai(url)
        if crawl_result.get("candidate_only") is False:
            return crawl_result
        weak_reader_result = crawl_result
    except Exception as exc:
        fallback_error = f"Crawl4AI failed: {exc}"

    try:
        async with session.get(reader_url, allow_redirects=True) as resp:
            if resp.status < 400:
                markdown = await resp.text(errors="ignore")
                strong, quality_note = readable_text_quality(markdown)
                if markdown.strip():
                    reader_result = {
                        "url": url,
                        "reader_url": reader_url,
                        "title": title_from_markdown(markdown, parsed.netloc),
                        "text": markdown[:12000].strip(),
                        "text_length": len(markdown.strip()),
                        "fetch_method": "jina_reader",
                        "candidate_only": not strong,
                        "quality_note": quality_note,
                    }
                    reader_result["source_quality"] = assess_source_quality({
                        **reader_result,
                        "scraped_text": reader_result["text"],
                        "direct_evidence": True,
                    })
                    if strong:
                        return reader_result
                    weak_reader_result = reader_result
            else:
                fallback_error = "; ".join(part for part in [fallback_error, f"Jina Reader HTTP {resp.status}"] if part)
    except Exception as exc:
        fallback_error = "; ".join(part for part in [fallback_error, f"Jina Reader failed: {exc}"] if part)

    try:
        async with session.get(url, allow_redirects=True) as resp:
            content_type = resp.headers.get("content-type", "")
            if resp.status >= 400:
                raise RuntimeError(f"Fetch failed with HTTP {resp.status}")
            if "text/html" not in content_type and "text/plain" not in content_type:
                raise RuntimeError(f"Unsupported content type: {content_type}")
            raw = await resp.text(errors="ignore")
    except Exception as exc:
        if allow_dynamic_browser and dynamic_browser_enabled():
            try:
                browser_result = await fetch_with_playwright(url)
                if browser_result.get("text"):
                    return browser_result
            except Exception as browser_exc:
                fallback_error = "; ".join(
                    part for part in [fallback_error, f"HTML fetch failed: {exc}", f"Playwright failed: {browser_exc}"]
                    if part
                )
        if weak_reader_result:
            weak_reader_result["quality_note"] = "; ".join(
                part for part in [weak_reader_result.get("quality_note", ""), fallback_error] if part
            )
            return weak_reader_result
        raise RuntimeError(f"Unable to fetch readable page: {fallback_error or exc}") from exc

    # 社区内容页先走平台适配器，避免通用正文算法丢失作者和发布时间。
    platform = adapt_platform_page(url, raw)
    if platform and platform.get("evidence_grade") == "citable_content":
        platform_result = {
            "url": platform["canonical_url"],
            "title": platform.get("title", ""),
            "text": platform.get("body", ""),
            "text_length": len(str(platform.get("body", ""))),
            "fetch_method": f"{platform.get('platform')}_adapter",
            "candidate_only": False,
            "author": platform.get("author", ""),
            "published_at": platform.get("published_at", ""),
            "platform_metadata": platform,
            "evidence_grade": "citable_content",
        }
        platform_result["source_quality"] = assess_source_quality({
            **platform_result, "scraped_text": platform_result["text"], "direct_evidence": True,
        })
        return platform_result

    parser = ReadableHTMLParser()
    parser.feed(raw[:2_000_000])
    text = parser.readable_text()
    if not text:
        if allow_dynamic_browser and dynamic_browser_enabled() and looks_like_dynamic_shell(raw):
            try:
                browser_result = await fetch_with_playwright(url)
                if browser_result.get("text"):
                    return browser_result
            except Exception as exc:
                fallback_error = "; ".join(
                    part for part in [fallback_error, f"Playwright failed: {exc}"] if part
                )
        if weak_reader_result:
            weak_reader_result["quality_note"] = (
                f"{weak_reader_result.get('quality_note', '')}; html parser extracted no readable text; {fallback_error}"
            ).strip("; ")
            return weak_reader_result
        raise RuntimeError(
            "No readable text extracted. This page may require login, JavaScript rendering, "
            f"or manual excerpting. {fallback_error}".strip()
        )
    strong, quality_note = readable_text_quality(text)
    result = {
        "url": url,
        "title": parser.title or parsed.netloc,
        "text": text,
        "text_length": len(text),
        "fetch_method": "html_parser",
        "candidate_only": not strong,
        "quality_note": quality_note if not fallback_error else f"{quality_note}; {fallback_error}",
    }
    result["source_quality"] = assess_source_quality({
        **result,
        "scraped_text": text,
        "direct_evidence": True,
    })
    if not strong and allow_dynamic_browser and dynamic_browser_enabled() and looks_like_dynamic_shell(raw, text):
        try:
            browser_result = await fetch_with_playwright(url)
            if browser_result.get("candidate_only") is False:
                return browser_result
            if int(browser_result.get("text_length", 0) or 0) > result["text_length"]:
                return browser_result
        except Exception as exc:
            result["quality_note"] = "; ".join(
                part for part in [result.get("quality_note", ""), f"Playwright failed: {exc}"] if part
            )
    return result


# ===================================================================
# 完整正文补全流程
# ===================================================================

async def hydrate_sources_for_analysis(
    user_data: list[dict],
    track: str = "",
    *,
    max_fetch_attempts_per_competitor: int = 12,
    max_browser_attempts_per_competitor: int = 2,
    max_accepted_sources_per_competitor: int = 12,
) -> list[dict]:
    """在采集 Agent 使用来源前补全正文并执行相关性复判。

    每个来源桶先过滤下载页和落地页；抓取失败或正文过弱时，自动尝试后续候选。
    已有缓存正文会直接复判，避免重复请求同一页面。
    """
    timeout = ClientTimeout(total=30)
    async with ClientSession(timeout=timeout, headers=READABLE_FETCH_HEADERS) as session:
        hydrated: list[dict] = []
        for item in user_data:
            company = str(item.get("company", "Unknown")).strip() or "Unknown"
            next_item = dict(item)
            concrete_fetches = 0
            fetch_attempts = 0
            browser_attempts = 0
            max_concrete = max(1, max_accepted_sources_per_competitor)
            max_fetch_attempts = max(1, max_fetch_attempts_per_competitor)
            max_browser_attempts = max(0, max_browser_attempts_per_competitor)
            seen: set[str] = set()
            for bucket in SOURCE_BUCKETS:
                bucket_fetch_start = fetch_attempts
                # 为 O/B/C/L 四个来源桶分别预留抓取额度，避免官网候选耗尽全部预算。
                bucket_fetch_quota = max(1, max_fetch_attempts // len(SOURCE_BUCKETS))
                sources = item.get(bucket, [])
                if not isinstance(sources, list):
                    continue
                hydrated_sources: list[dict] = []
                # 预筛选：标记下载页和落地页，并收集可抓取来源
                prefiltered: list[dict] = []
                for source in sources:
                    if not isinstance(source, dict):
                        continue
                    src = dict(source)
                    url = str(src.get("url", "")).strip()
                    canonical = canonicalize_url(url)
                    if canonical and canonical in seen:
                        continue
                    if canonical:
                        seen.add(canonical)
                    if not url:
                        src.setdefault("candidate_only", True)
                        src.setdefault("evidence_status", "missing_url")
                        src["source_quality"] = assess_source_quality(src)
                        hydrated_sources.append(src)
                        continue
                    if is_search_entry_url(url):
                        src["candidate_only"] = True
                        src["evidence_status"] = "candidate_search"
                        src.setdefault("fetch_method", "search_entry")
                        src["source_quality"] = assess_source_quality(src)
                        hydrated_sources.append(src)
                        continue
                    if len(str(src.get("scraped_text", "")).strip()) >= 120 and not src.get("candidate_only"):
                        src.setdefault("fetch_method", "cached_text")
                        src = _apply_evidence_verdict(src, company, bucket)
                        hydrated_sources.append(src)
                        continue
                    if src.get("fetch_method") in {"crawl4ai", "playwright", "jina_reader", "html_parser"} and not src.get("candidate_only"):
                        src = _apply_evidence_verdict(src, company, bucket)
                        hydrated_sources.append(src)
                        continue
                    src = _prefilter_source_for_hydration(
                        src,
                        allow_homepage=bucket == "official_sources",
                    )
                    prefiltered.append(src)

                # 尝试从官方网站的 sitemap 补充当前来源桶
                official_domains_checked: set[str] = set()
                for src in prefiltered:
                    url = str(src.get("url", "")).strip()
                    if bucket != "official_sources" or not url or src.get("candidate_only"):
                        continue
                    if classify_actual_source_type(src, company) != "official":
                        continue
                    host = urlparse(url).netloc.lower()
                    if not host or host in official_domains_checked:
                        continue
                    official_domains_checked.add(host)
                    try:
                        sitemap_urls = await discover_sitemap_urls(session, host)
                        for sm_url in sitemap_urls[:5]:
                            sm_canonical = canonicalize_url(sm_url)
                            if sm_canonical and sm_canonical in seen:
                                continue
                            if sm_canonical:
                                seen.add(sm_canonical)
                            prefiltered.append({
                                "label": f"{host} (sitemap)",
                                "url": sm_url,
                                "evidence_url": sm_url,
                                "authority": "high",
                                "direct_evidence": True,
                                "source_group": "direct",
                                "source_status": "需抓取验证",
                                "discovered_via": "sitemap",
                                "evidence_slot": src.get("evidence_slot") or _DEFAULT_SLOT_BY_BUCKET[bucket],
                            })
                    except Exception as exc:
                        logger.debug("Sitemap discovery failed for %s: %s", host, exc)

                # 带降级的正文补全：结果过弱或失败时尝试下一个候选
                pending: list[dict] = []
                for src in prefiltered:
                    url = str(src.get("url", "")).strip()
                    if not url or src.get("candidate_only"):
                        hydrated_sources.append(src)
                        continue
                    if (concrete_fetches >= max_concrete or fetch_attempts >= max_fetch_attempts
                            or fetch_attempts - bucket_fetch_start >= bucket_fetch_quota):
                        src.setdefault("candidate_only", True)
                        src.setdefault("evidence_status", "fetch_budget_deferred")
                        src = _attach_degraded_summary(src)
                        src["source_quality"] = assess_source_quality(src)
                        hydrated_sources.append(src)
                        continue
                    concrete_fetches += 1
                    fetch_attempts += 1
                    try:
                        fetched = await fetch_readable_source(
                            session,
                            url,
                            allow_dynamic_browser=browser_attempts < max_browser_attempts,
                        )
                        if fetched.get("fetch_method") == "playwright":
                            browser_attempts += 1
                        src["label"] = src.get("label") or fetched.get("title") or company
                        src["scraped_text"] = fetched.get("text", "")[:12000]
                        src["fetch_method"] = fetched.get("fetch_method", "")
                        src["reader_url"] = fetched.get("reader_url", "")
                        src["candidate_only"] = bool(fetched.get("candidate_only"))
                        src["quality_note"] = fetched.get("quality_note", "")
                        src["evidence_status"] = "candidate_text" if src["candidate_only"] else "strong_text"
                        src["source_quality"] = fetched.get("source_quality") or assess_source_quality(src)
                        if "baike.baidu.com" in urlparse(url).netloc.lower():
                            src["candidate_only"] = True
                            src["evidence_status"] = "background_text"
                            src["quality_note"] = "百科仅作背景信息，不计入强证据。"
                            src["source_quality"] = assess_source_quality(src)
                        if not src.get("candidate_only"):
                            src = _apply_evidence_verdict(src, company, bucket)
                        if src.get("candidate_only"):
                            src = _attach_degraded_summary(src)
                            pending.append(src)
                            concrete_fetches -= 1  # 弱结果不占用有效抓取配额
                        else:
                            hydrated_sources.append(src)
                    except Exception as exc:
                        logger.info("Hydration failed for %s (%s): %s", company, url, exc)
                        src["candidate_only"] = True
                        src["evidence_status"] = "fetch_failed"
                        src["quality_note"] = "页面抓取失败，请稍后重试或手动填入摘录。"
                        src = _attach_degraded_summary(src)
                        src["source_quality"] = assess_source_quality(src)
                        pending.append(src)
                        concrete_fetches -= 1  # 抓取失败不占用有效抓取配额

                # 最后追加仍待处理的弱来源或失败来源
                hydrated_sources.extend(pending)
                next_item[bucket] = hydrated_sources
            relevance_metrics, rejected_sources = _organize_hydrated_sources(next_item, company)
            metadata = next_item.get("metadata") if isinstance(next_item.get("metadata"), dict) else {}
            previous_candidates = metadata.get("candidate_sources") if isinstance(metadata.get("candidate_sources"), list) else []
            next_item["metadata"] = {
                **metadata,
                "candidate_sources": [*previous_candidates, *rejected_sources][:30],
                "evidence_relevance": relevance_metrics,
                "acquisition_attempts": {
                    "fetch_attempts": fetch_attempts,
                    "accepted_sources": concrete_fetches,
                    "browser_attempts": browser_attempts,
                    "fetch_attempt_limit": max_fetch_attempts,
                    "accepted_source_limit": max_concrete,
                    "browser_attempt_limit": max_browser_attempts,
                },
                "source_coverage": source_coverage_for_competitor(next_item),
            }
            hydrated.append(next_item)
        return hydrated
