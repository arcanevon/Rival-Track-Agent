"""Industry routing: keyword matching + LLM fallback for track → industry_type."""

import re
import logging
from . import load_routing_config

logger = logging.getLogger(__name__)

ROUTER_SYSTEM = """You are an industry classifier. Given a product name and track description,
output ONLY a single JSON object with one key "industry" whose value is exactly one of:
software_saas, retail_fmcg, automotive, consumer_hardware, gaming, platform_social, fintech, healthcare, generic_business.

Rules:
- software_saas: AI tools, SaaS platforms, developer tools, cloud services, enterprise software
- retail_fmcg: food & beverage, restaurants, cosmetics, fashion, retail chains, consumer packaged goods
- automotive: electric vehicles, autonomous driving, ride-hailing, charging infrastructure
- consumer_hardware: smartphones, wearables, drones, smart home devices, chips
- gaming: mobile games, PC/console games, esports, VR gaming
- platform_social: short video, e-commerce platforms, social media, live streaming, content platforms
- fintech: payments, cryptocurrency, insurtech, wealth management, banking
- healthcare: medical devices, digital therapeutics, pharma, biotech, hospitals
- generic_business: traditional consulting, logistics, manufacturing, agriculture, and anything not clearly in the above categories

Respond with ONLY the JSON object, no other text."""

ROUTER_USER = """Classify this product into one industry:
Product: {product_name}
Track: {track}

Output ONLY: {{"industry": "..."}}"""


def _tokenize(text: str) -> set[str]:
    return set(re.split(r"[\s·\-|,，、。（）()\[\]【】/\\]+", str(text).lower())) - {""}


def resolve_industry_keyword(track: str = "", product_name: str = "") -> str | None:
    """Fast keyword-based industry routing. Returns None if no match found."""
    routing = load_routing_config()
    routes = routing.get("routes", []) if routing else []
    if not routes:
        return None

    context = f"{track} {product_name}".lower()
    tokens = _tokenize(context)
    best_industry = None
    best_score = 0
    for route in routes:
        industry = route.get("industry", "")
        keywords = route.get("keywords", [])
        if not industry or not keywords:
            continue
        score = 0
        for keyword in keywords:
            normalized = str(keyword).strip().lower()
            if not normalized:
                continue
            if normalized in tokens:
                score += 1
                continue
            # 中文领域标签经常是“游戏与互动娱乐”这样的组合短语，不能只做整词匹配。
            if re.search(r"[\u4e00-\u9fff]", normalized) and normalized in context:
                score += 1
        if score > best_score:
            best_score = score
            best_industry = industry

    return best_industry if best_score > 0 else None


async def resolve_industry_llm(product_name: str = "", track: str = "") -> str:
    """LLM-based industry classification fallback."""
    import json as _json
    from src.client.deepseek import call_deepseek

    try:
        raw = await call_deepseek(
            ROUTER_SYSTEM,
            ROUTER_USER.format(product_name=product_name or track or "unknown", track=track or ""),
            max_tokens=64,
            timeout=10,
            temperature=0.1,
        )
        data = _json.loads(raw.strip())
        industry = str(data.get("industry", "")).strip()
        valid = {
            "software_saas", "retail_fmcg", "automotive", "consumer_hardware",
            "gaming", "platform_social", "fintech", "healthcare", "generic_business",
        }
        if industry in valid:
            return industry
    except Exception as exc:
        logger.debug("LLM industry router failed: %s", exc)
    return "generic_business"


async def resolve_industry(track: str = "", product_name: str = "") -> str:
    """Resolve industry type: keyword match first, LLM fallback if no match."""
    keyword_result = resolve_industry_keyword(track, product_name)
    if keyword_result:
        return keyword_result
    return await resolve_industry_llm(product_name, track)
