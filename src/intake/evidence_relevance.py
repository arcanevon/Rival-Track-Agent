"""抓取后证据相关性验收、实际来源识别和二阶段重排。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from .constants import OFFICIAL_DOMAIN_HINTS


LOW_VALUE_DOMAINS = (
    "pc6.com", "downza.cn", "onlinedown.net", "mydown.com", "xlhs.com",
    "tianqing123.cn", "qqpk.cn", "oapkzyh.cn", "itmop.com", "wankr.com.cn",
    "5577.com", "cncrk.com", "downkuai.com", "duote.com",
)

LOW_VALUE_TITLE_MARKERS = (
    "完整版下载", "官网游戏下载", "app下载", "软件下载", "手机版下载",
    "电脑版下载", "免费安装", "安装包", "破解版", "绿色版", "练什么",
    "怎么玩", "领取指南", "刷取攻略", "下载教程",
)

COMMUNITY_DOMAINS = (
    "taptap.cn", "reddit.com", "zhihu.com", "weibo.com", "xiaohongshu.com",
    "douban.com", "v2ex.com", "tieba.baidu.com", "bbs.", "hupu.com",
    "news.ycombinator.com", "producthunt.com", "juejin.cn", "bilibili.com",
    "douyin.com",
)

LEADING_DOMAINS = (
    "github.com", "gitee.com", "patents.google.com", "jobs.", "careers.",
)

BENCHMARK_DOMAINS = (
    "36kr.com", "caixin.com", "yicai.com", "people.com.cn", "xinhuanet.com",
    "thepaper.cn", "tmtpost.com", "huxiu.com", "geekpark.net", "gartner.com",
    "idc.com", "statista.com", "counterpointresearch.com", "cnblogs.com",
    "faros.ai", "qodo.ai",
    "21jingji.com", "iimedia.cn", "chinadaily.com.cn", "news.cn",
    "jiemian.com", "chinanews.com.cn", "stcn.com", "cnstock.com",
    "eeo.com.cn", "foodtalks.cn", "hongcan.com.cn", "canyin88.com", "topnews.cn",
)

SLOT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "official_capability": (
        "功能", "能力", "产品", "版本", "更新", "特性", "技术", "品牌", "服务",
        "门店", "饮品", "feature",
        "capability", "release", "changelog",
    ),
    "pricing_or_packaging": (
        "价格", "定价", "套餐", "付费", "免费版", "会员", "内购", "pricing", "plan",
    ),
    "community_pain": (
        "用户", "玩家", "评价", "反馈", "吐槽", "问题", "体验", "流失", "投诉",
        "review", "feedback", "complaint",
    ),
    "third_party_benchmark": (
        "评测", "测评", "对比", "榜单", "排名", "市场份额", "流水", "benchmark",
        "comparison", "report",
    ),
    "distribution_signal": (
        "渠道", "门店", "合作", "客户", "市场", "覆盖", "分发", "用户规模",
        "partner", "marketplace", "customer",
    ),
    "github_release_velocity": (
        "release", "commit", "issue", "star", "版本", "更新", "开源",
    ),
    "strategic_expansion_signal": (
        "招聘", "路线图", "融资", "专利", "合作", "扩张", "投资", "roadmap",
        "hiring", "funding", "patent",
    ),
    "general_competitor_evidence": ("产品", "用户", "市场", "功能", "竞争"),
}

SLOT_SOURCE_TYPES: dict[str, set[str]] = {
    "official_capability": {"official", "benchmark"},
    "pricing_or_packaging": {"official", "benchmark"},
    "community_pain": {"community", "benchmark"},
    "third_party_benchmark": {"benchmark"},
    "distribution_signal": {"official", "benchmark"},
    "github_release_velocity": {"leading"},
    "strategic_expansion_signal": {"leading", "official", "benchmark"},
    "general_competitor_evidence": {"official", "benchmark", "community", "leading"},
}


@dataclass(frozen=True)
class EvidenceVerdict:
    """单条正文能否进入 Threat Matrix 评分证据池的结构化判定。"""

    accepted: bool
    entity_relevance: float
    claim_alignment: float
    content_quality: float
    source_authority: float
    actual_source_type: str
    relevance_score: float
    supporting_quote: str
    reject_reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _host(url: str) -> str:
    return urlparse(str(url or "")).netloc.lower().removeprefix("www.")


def is_low_value_page(url: str, title: str = "", text: str = "") -> bool:
    """识别下载站、安装包页和只提供操作攻略的低价值页面。"""
    host = _host(url)
    sample = f"{title} {text[:500]}".lower()
    if any(domain in host for domain in LOW_VALUE_DOMAINS):
        return True
    return any(marker.lower() in sample for marker in LOW_VALUE_TITLE_MARKERS)


def _official_domains(company: str) -> set[str]:
    normalized = str(company or "").lower()
    domains: set[str] = set()
    for keyword, hints in OFFICIAL_DOMAIN_HINTS.items():
        if keyword.lower() in normalized or normalized in keyword.lower():
            domains.update(hints)
    return domains


def classify_actual_source_type(source: dict, company: str = "") -> str:
    """根据实际域名和页面性质分类，绝不根据查询期望伪装来源类型。"""
    url = str(source.get("url") or source.get("evidence_url") or "")
    host = _host(url)
    title = str(source.get("label") or source.get("title") or "")
    text = str(source.get("scraped_text") or source.get("text") or "")
    if not host or is_low_value_page(url, title, text):
        return "unknown"
    declared = str(source.get("verified_source_type") or source.get("channel") or "")
    if (
        declared in {"official", "benchmark", "community", "leading"}
        and source.get("authority") == "high"
        and not source.get("search_provider")
    ):
        return declared
    if any(domain in host for domain in _official_domains(company)):
        return "official"
    if any(domain in host for domain in COMMUNITY_DOMAINS):
        return "community"
    if any(domain in host for domain in LEADING_DOMAINS):
        return "leading"
    if any(domain in host for domain in BENCHMARK_DOMAINS):
        return "benchmark"
    return "unknown"


def _entity_relevance(source: dict, company: str) -> float:
    title = str(source.get("label") or source.get("title") or "").lower()
    text = str(source.get("scraped_text") or source.get("text") or "").lower()
    company_text = str(company or "").strip().lower()
    primary_name = re.split(r"[（(]", company_text, maxsplit=1)[0].strip()
    variants = {company_text, company_text.replace("の", "的"), company_text.replace("的", "の")}
    if len(primary_name) >= 2:
        variants.add(primary_name)
    variants.discard("")
    if any(value in title for value in variants):
        return 1.0
    if any(value in text for value in variants):
        return 0.85
    # 括号内通常是母公司，不应要求正文同时出现品牌名和母公司名。
    tokens = [token for token in re.split(r"[\s/\\,，、|·:：()（）\[\]【】\-]+", primary_name) if len(token) >= 2]
    hits = sum(1 for token in tokens if token in f"{title} {text}")
    return 0.8 if tokens and hits == len(tokens) else 0.0


def _claim_alignment(text: str, slot: str) -> float:
    keywords = SLOT_KEYWORDS.get(slot) or SLOT_KEYWORDS["general_competitor_evidence"]
    lowered = text.lower()
    hits = sum(1 for keyword in keywords if keyword.lower() in lowered)
    # 一个明确命中即可证明正文回答了该 Claim；两个及以上命中代表强对齐。
    return round(min(1.0, hits / min(2, len(keywords))), 3)


def _supporting_quote(text: str, company: str, slot: str) -> str:
    keywords = SLOT_KEYWORDS.get(slot) or SLOT_KEYWORDS["general_competitor_evidence"]
    sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])|[\r\n]+", text) if part.strip()]
    company_low = str(company or "").lower()
    ranked = sorted(
        sentences,
        key=lambda sentence: (
            company_low in sentence.lower(),
            sum(1 for keyword in keywords if keyword.lower() in sentence.lower()),
            min(len(sentence), 240),
        ),
        reverse=True,
    )
    return ranked[0][:300] if ranked else ""


def evaluate_evidence(source: dict, company: str, slot: str = "") -> EvidenceVerdict:
    """在正文抓取后进行实体、Claim、正文和来源四重验收。"""
    url = str(source.get("url") or source.get("evidence_url") or "")
    title = str(source.get("label") or source.get("title") or "")
    text = str(source.get("scraped_text") or source.get("text") or "").strip()
    slot = slot or str(source.get("evidence_slot") or "general_competitor_evidence")
    actual_type = classify_actual_source_type(source, company)
    entity = _entity_relevance(source, company)
    claim = _claim_alignment(f"{title} {text}", slot)
    # 120 字已足以抽取一条可核验句子；更短正文仅保留为候选，不进入评分池。
    content = 1.0 if len(text) >= 120 else 0.5 if len(text) >= 60 else 0.0
    authority = {"official": 1.0, "benchmark": 0.8, "leading": 0.7, "community": 0.65}.get(actual_type, 0.2)
    expected_types = SLOT_SOURCE_TYPES.get(slot, SLOT_SOURCE_TYPES["general_competitor_evidence"])
    low_value = is_low_value_page(url, title, text)
    provenance_valid = actual_type in expected_types
    quote = _supporting_quote(text, company, slot)
    accepted = bool(
        not low_value
        and entity >= 0.65
        and claim >= 0.5
        and content >= 1.0
        and provenance_valid
        and quote
    )
    score = round(100 * (0.35 * entity + 0.30 * claim + 0.20 * content + 0.15 * authority), 1)

    if low_value:
        reason = "下载、安装或攻略聚合页不能作为评分证据。"
    elif entity < 0.65:
        reason = "正文未能确认目标竞品实体。"
    elif claim < 0.5:
        reason = f"正文没有回答证据槽位 {slot} 对应的问题。"
    elif content < 1.0:
        reason = "正文过短，无法形成可核验引用。"
    elif not provenance_valid:
        reason = f"实际来源类型 {actual_type} 不满足槽位 {slot} 的来源要求。"
    elif not quote:
        reason = "正文中没有可核验的支持性句子。"
    else:
        reason = ""

    return EvidenceVerdict(
        accepted=accepted,
        entity_relevance=entity,
        claim_alignment=claim,
        content_quality=content,
        source_authority=authority,
        actual_source_type=actual_type,
        relevance_score=score,
        supporting_quote=quote if accepted else "",
        reject_reason=reason,
    )


def rerank_evidence_sources(
    sources: list[dict],
    *,
    limit: int = 6,
    per_domain_limit: int = 1,
) -> list[dict]:
    """按抓取后相关性重排，并优先保证域名多样性。"""
    ranked = sorted(
        sources,
        key=lambda item: float((item.get("evidence_verdict") or {}).get("relevance_score", 0)),
        reverse=True,
    )
    selected: list[dict] = []
    deferred: list[dict] = []
    domain_counts: dict[str, int] = {}
    for item in ranked:
        domain = _host(str(item.get("url") or item.get("evidence_url") or ""))
        if domain_counts.get(domain, 0) >= per_domain_limit:
            deferred.append(item)
            continue
        selected.append(item)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        if len(selected) >= limit:
            return selected
    for item in deferred:
        if len(selected) >= limit:
            break
        selected.append(item)
    return selected


def evidence_relevance_metrics(sources: list[dict]) -> dict[str, object]:
    """计算可用于 benchmark 和 Quality Gate 的确定性证据质量指标。"""
    evaluated = [source for source in sources if isinstance(source.get("evidence_verdict"), dict)]
    accepted = [source for source in evaluated if source["evidence_verdict"].get("accepted") is True]
    top_five = evaluated[:5]
    accepted_top = sum(1 for source in top_five if source["evidence_verdict"].get("accepted") is True)
    accepted_official = [
        source for source in accepted
        if source["evidence_verdict"].get("actual_source_type") == "official"
    ]
    bad_accepted = [
        source for source in accepted
        if is_low_value_page(
            str(source.get("url") or ""),
            str(source.get("label") or ""),
            str(source.get("scraped_text") or ""),
        )
    ]
    quoted = [
        source for source in accepted
        if source["evidence_verdict"].get("supporting_quote")
    ]
    domains = {_host(str(source.get("url") or "")) for source in accepted if source.get("url")}
    return {
        "evaluated_sources": len(evaluated),
        "accepted_sources": len(accepted),
        "precision_at_5": round(accepted_top / len(top_five), 3) if top_five else 0.0,
        "official_precision": round(len(accepted_official) / len(accepted), 3) if accepted else 0.0,
        "claim_answer_rate": round(len(quoted) / len(accepted), 3) if accepted else 0.0,
        "bad_domain_leakage": round(len(bad_accepted) / len(accepted), 3) if accepted else 0.0,
        "unique_domains_at_5": min(5, len(domains)),
    }
