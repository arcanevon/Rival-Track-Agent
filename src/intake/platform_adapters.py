"""公开社区内容页适配器。

适配器只读取公开页面，不保存 Cookie，也不绕过登录墙或验证码。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, asdict
from urllib.parse import urlparse, urlunparse


@dataclass(frozen=True)
class PlatformContent:
    platform: str
    content_id: str
    canonical_url: str
    title: str = ""
    author: str = ""
    published_at: str = ""
    body: str = ""
    metrics: dict[str, object] | None = None
    fetch_status: str = "candidate"
    login_required: bool = False
    evidence_grade: str = "candidate_lead"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _meta(page: str, key: str) -> str:
    patterns = [
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(key)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            return _clean_text(match.group(1))
    return ""


def _canonical(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), parsed.path, "", "", ""))


def _json_ld(page: str) -> list[dict]:
    rows = []
    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.I | re.S):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        values = value if isinstance(value, list) else [value]
        rows.extend(item for item in values if isinstance(item, dict))
    return rows


def adapt_zhihu_page(url: str, page: str) -> dict[str, object]:
    """提取知乎公开问题或回答页的正文与可验证元数据。"""
    match = re.search(r"/question/(\d+)(?:/answer/(\d+))?", url)
    content_id = (match.group(2) or match.group(1)) if match else ""
    blocked = any(marker in page for marker in ("登录后", "安全验证", "验证码"))
    bodies = re.findall(r'class=["\'][^"\']*(?:RichContent-inner|RichText)[^"\']*["\'][^>]*>(.*?)</(?:div|span)>', page, re.I | re.S)
    body = max((_clean_text(value) for value in bodies), key=len, default="")
    title = _meta(page, "og:title") or _meta(page, "twitter:title")
    author = _meta(page, "author")
    status = "ok" if len(body) >= 80 else ("login_required" if blocked else "metadata_only")
    grade = "citable_content" if status == "ok" else ("verifiable_metadata" if title else "candidate_lead")
    return PlatformContent("zhihu", content_id, _canonical(url), title, author, body=body,
                           fetch_status=status, login_required=blocked,
                           evidence_grade=grade).to_dict()


def adapt_bilibili_page(url: str, page: str) -> dict[str, object]:
    """提取 B 站公开视频页标题、简介、作者与发布时间。"""
    match = re.search(r"/(?:video/)?((?:BV|av)[A-Za-z0-9]+)", url, re.I)
    content_id = match.group(1) if match else ""
    title = _meta(page, "og:title")
    body = _meta(page, "og:description") or _meta(page, "description")
    author = ""
    published = ""
    for item in _json_ld(page):
        title = title or _clean_text(item.get("name") or item.get("headline"))
        body = body or _clean_text(item.get("description"))
        creator = item.get("author") or item.get("creator") or {}
        author = _clean_text(creator.get("name") if isinstance(creator, dict) else creator)
        published = _clean_text(item.get("uploadDate") or item.get("datePublished"))
    status = "ok" if title and len(body) >= 20 else "metadata_only"
    grade = "citable_content" if status == "ok" else ("verifiable_metadata" if title else "candidate_lead")
    return PlatformContent("bilibili", content_id, _canonical(url), title, author, published,
                           body, {}, status, False, grade).to_dict()


def adapt_platform_page(url: str, page: str) -> dict[str, object] | None:
    """按域名路由到平台适配器，非支持域名返回空。"""
    host = urlparse(url).netloc.lower()
    if host.endswith("zhihu.com"):
        return adapt_zhihu_page(url, page)
    if host.endswith("bilibili.com"):
        return adapt_bilibili_page(url, page)
    return None


__all__ = ["PlatformContent", "adapt_bilibili_page", "adapt_platform_page", "adapt_zhihu_page"]
