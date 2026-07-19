"""抓取后证据相关性、实际来源类型和二阶段重排测试。"""

from src.intake.evidence_relevance import (
    classify_actual_source_type,
    evaluate_evidence,
    evidence_relevance_metrics,
    rerank_evidence_sources,
)


def _source(url: str, title: str, text: str, slot: str = "official_capability") -> dict:
    return {
        "url": url,
        "label": title,
        "scraped_text": text,
        "evidence_slot": slot,
        "direct_evidence": True,
    }


def test_download_aggregator_is_rejected_even_when_brand_name_matches():
    source = _source(
        "https://mip.tianqing123.cn/wenda/10558111.html",
        "云原神完整版下载",
        ("云原神完整版下载，提供安卓安装包和最新版游戏资源。" * 20),
    )

    verdict = evaluate_evidence(source, "原神", "official_capability")

    assert verdict.accepted is False
    assert verdict.actual_source_type == "unknown"
    assert "下载" in verdict.reject_reason


def test_official_page_must_answer_the_requested_claim():
    source = _source(
        "https://ys.mihoyo.com/main/news/detail/1",
        "原神版本能力介绍",
        ("原神官方发布新版本，新增开放世界区域、角色能力、跨平台云游戏功能与版本更新说明。" * 12),
    )

    verdict = evaluate_evidence(source, "原神", "official_capability")

    assert verdict.accepted is True
    assert verdict.actual_source_type == "official"
    assert verdict.supporting_quote in source["scraped_text"]
    assert verdict.claim_alignment >= 0.5


def test_source_type_comes_from_provenance_not_requested_slot():
    community = _source(
        "https://www.taptap.cn/moment/123",
        "原神玩家评价",
        ("原神玩家反馈版本体验、抽卡压力和内容消耗后的流失原因。" * 12),
        "community_pain",
    )

    assert classify_actual_source_type(community, "原神") == "community"
    assert classify_actual_source_type(
        {**community, "url": "https://ys.mihoyo.com/main/"}, "原神"
    ) == "official"


def test_rerank_limits_one_domain_per_slot_before_filling_duplicates():
    sources = []
    for index, score in enumerate((92, 88, 80)):
        item = _source(
            f"https://same.example.com/article/{index}",
            f"竞品甲能力材料 {index}",
            "竞品甲功能、版本和产品能力说明。" * 20,
        )
        item["evidence_verdict"] = {"accepted": True, "relevance_score": score}
        sources.append(item)
    diverse = _source(
        "https://other.example.org/report/1",
        "竞品甲第三方能力材料",
        "竞品甲功能、版本和产品能力说明。" * 20,
    )
    diverse["evidence_verdict"] = {"accepted": True, "relevance_score": 75}
    sources.append(diverse)

    ranked = rerank_evidence_sources(sources, limit=3, per_domain_limit=1)

    assert [item["url"] for item in ranked[:2]] == [
        "https://same.example.com/article/0",
        "https://other.example.org/report/1",
    ]


def test_relevance_metrics_report_precision_and_bad_domain_leakage():
    accepted = _source("https://ys.mihoyo.com/main/", "原神官网", "原神版本能力。" * 30)
    accepted["evidence_verdict"] = {
        "accepted": True, "actual_source_type": "official", "relevance_score": 90,
    }
    rejected = _source("https://pc6.com/down/1", "原神下载", "原神下载。" * 30)
    rejected["evidence_verdict"] = {
        "accepted": False, "actual_source_type": "unknown", "relevance_score": 20,
    }

    metrics = evidence_relevance_metrics([accepted, rejected])

    assert metrics["evaluated_sources"] == 2
    assert metrics["accepted_sources"] == 1
    assert metrics["precision_at_5"] == 0.5
    assert metrics["bad_domain_leakage"] == 0.0
