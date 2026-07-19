from src.intake.platform_adapters import adapt_bilibili_page, adapt_zhihu_page


def test_zhihu_adapter_requires_body_before_citable_grade() -> None:
    page = '''<meta property="og:title" content="产品体验讨论">
    <meta name="author" content="测试用户"><div class="RichContent-inner">''' + ("这是可复核的公开回答正文。" * 20) + "</div>"
    result = adapt_zhihu_page("https://www.zhihu.com/question/123/answer/456", page)
    assert result["content_id"] == "456"
    assert result["evidence_grade"] == "citable_content"
    assert result["login_required"] is False


def test_bilibili_adapter_reads_public_video_metadata() -> None:
    page = '''<meta property="og:title" content="产品测评视频">
    <meta property="og:description" content="这是一个包含实际体验、优缺点和对比结论的公开视频简介。">
    <script type="application/ld+json">{"author":{"name":"测评者"},"uploadDate":"2026-01-02"}</script>'''
    result = adapt_bilibili_page("https://www.bilibili.com/video/BV1abc123", page)
    assert result["content_id"] == "BV1abc123"
    assert result["author"] == "测评者"
    assert result["evidence_grade"] == "citable_content"
