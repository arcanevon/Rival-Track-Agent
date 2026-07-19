"""研究范围确认与分析模式预算。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable

from .acquisition import AcquisitionBudget


@dataclass(frozen=True)
class AnalysisModePolicy:
    """把产品档位转换为可审计的采集和返工预算。"""

    name: str
    competitor_limit: int
    rework_rounds: int
    budget: AcquisitionBudget


MODE_POLICIES = {
    "fast": AnalysisModePolicy(
        "fast", 3, 0,
        AcquisitionBudget(2, 9, 5, 0, 5, 50.0),
    ),
    "standard": AnalysisModePolicy(
        "standard", 5, 1,
        AcquisitionBudget(2, 20, 12, 2, 12, 120.0),
    ),
    "deep": AnalysisModePolicy(
        "deep", 8, 2,
        AcquisitionBudget(3, 48, 20, 4, 20, 240.0),
    ),
}


_ENTITY_STOP_WORDS = {
    "best", "top", "review", "reviews", "pricing", "features", "alternative", "alternatives",
    "competitor", "competitors", "software", "platform", "tools", "tool", "guide", "official",
    "最佳", "推荐", "替代品", "竞品", "对比", "评测", "价格", "功能", "工具", "平台", "软件", "官网",
}


def extract_competitor_names_from_search(product: str, rows: list[dict], limit: int) -> list[str]:
    """从“对比/替代品”搜索结果中保守提取产品实体。"""
    candidates: list[str] = []
    product_key = re.sub(r"\s+", "", product).casefold()
    product_tokens = [
        token.casefold() for token in re.findall(r"[A-Za-z0-9+.-]{3,}|[\u4e00-\u9fff]{2,}", product)
        if token.casefold() not in {"the", "and", "ai"}
    ]

    def add(value: str) -> None:
        name = re.sub(r"^[\s\-–—:：,，、]+|[\s\-–—:：,，、]+$", "", value)
        name = re.sub(r"\s+", " ", name).strip("'\"“”‘’()（）[]【】")
        name = re.sub(r"等(?:产品|应用|工具)?$", "", name).strip()
        key = re.sub(r"\s+", "", name).casefold()
        words = {word.casefold() for word in re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", name)}
        if not name or key == product_key or product_key in key or key in product_key:
            return
        if len(name) < 2 or len(name) > 36 or words.intersection(_ENTITY_STOP_WORDS):
            return
        if name not in candidates:
            candidates.append(name)

    for row in rows:
        blob = f"{row.get('title', '')}。{row.get('snippet', '')}"
        blob_key = blob.casefold()
        if product_tokens and not any(token in blob_key for token in product_tokens):
            continue
        for match in re.finditer(
            r"([A-Za-z][A-Za-z0-9+.&-]*(?:\s+[A-Za-z][A-Za-z0-9+.&-]*){0,2})\s+(?:vs\.?|versus)\s+"
            r"([A-Za-z][A-Za-z0-9+.&-]*(?:\s+[A-Za-z][A-Za-z0-9+.&-]*){0,2})",
            blob, re.I,
        ):
            add(match.group(1)); add(match.group(2))
        for match in re.finditer(r"([^，。；、|]{2,24})\s*(?:与|和|VS|vs\.?)\s*([^，。；、|]{2,24})", blob):
            add(match.group(1)); add(match.group(2))
        for match in re.finditer(
            r"(?:包括|例如|如|分别是|including|include|such as|alternatives are)[:：]?\s*([^。；.!]{2,120})",
            blob, re.I,
        ):
            segment = match.group(1)
            for part in re.split(r"[,，、/|]|\s+(?:and|or)\s+", segment, flags=re.I):
                latin_names = re.findall(
                    r"\b[A-Z][A-Za-z0-9+.-]*(?:\s+[A-Z][A-Za-z0-9+.-]*){0,2}\b", part,
                )
                if latin_names:
                    for name in latin_names:
                        add(name)
                elif re.search(r"[\u4e00-\u9fff]", part):
                    add(part)
        if len(candidates) >= limit:
            break
    return candidates[:limit]


def analysis_mode_policy(mode: object) -> AnalysisModePolicy:
    """返回模式策略；未知值安全回落到标准模式。"""
    return MODE_POLICIES.get(str(mode or "").strip().lower(), MODE_POLICIES["standard"])


def build_scope_snapshot(
    product: str,
    broad_track: str,
    competitor_names: list[str],
    *,
    sub_track: str = "",
) -> dict[str, object]:
    """创建可冻结的研究范围快照。"""
    relationships = ("direct_substitute", "capability_chaser", "distribution_power")
    competitors = []
    for index, name in enumerate(competitor_names):
        relationship = relationships[min(index, len(relationships) - 1)]
        reason = {
            "direct_substitute": "面向相近用户并解决相近需求",
            "capability_chaser": "核心能力与目标产品存在明显重合",
            "distribution_power": "渠道或生态可能分流目标用户",
        }[relationship]
        competitors.append({
            "name": name,
            "relationship_type": relationship,
            "reason": reason,
            "confidence": round(max(0.55, 0.86 - index * 0.07), 2),
            "selected": True,
        })
    raw = "|".join([product, broad_track, sub_track, *competitor_names])
    return {
        "subject": product,
        "broad_track": broad_track or "待用户确认",
        "sub_track": sub_track or broad_track or "待用户确认",
        "competitors": competitors,
        "scope_version": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12],
        "confirmed": False,
    }


async def discover_analysis_scope(
    product: str,
    broad_track: str,
    manual_names: list[str],
    limit: int,
    *,
    seed_names: list[str] | None = None,
    model_call: Callable[[str, str], Awaitable[str]] | None = None,
    search_discover: Callable[[str, str, int], Awaitable[list[str]]] | None = None,
    track_only: bool = False,
) -> dict[str, object]:
    """利用模型推断赛道和竞品；track_only 模式只执行赛道识别。"""
    fallback_names = [] if track_only else list(dict.fromkeys(manual_names or seed_names or []))[:limit]
    if model_call is None:
        from src.client.deepseek import call_deepseek

        async def bounded_model_call(system: str, user: str) -> str:
            return await call_deepseek(
                system, user, temperature=0.2, max_tokens=1400, max_retries=1, timeout=15,
            )

        model_call = bounded_model_call

    system_prompt = """你是竞品研究范围规划员。只做实体识别和范围建议，不做威胁评分。
只返回一个 JSON 对象，不要使用 Markdown。竞品必须是现实存在、与研究对象有明确替代、能力追赶或渠道竞争关系的产品。"""
    if track_only:
        user_prompt = f"""研究对象：{product}
请只识别该产品所属的大赛道和细分赛道，不要返回竞品。
只返回字段：broad_track、sub_track、competitors；competitors 必须为空数组。"""
    else:
        user_prompt = f"""研究对象：{product}
用户初选大赛道：{broad_track or '未确定'}
用户手动指定竞品：{json.dumps(manual_names, ensure_ascii=False)}
系统种子候选：{json.dumps(seed_names or [], ensure_ascii=False)}
最多返回 {limit} 个竞品。若用户手动指定了竞品，不得改名、删除或新增，只补充关系和理由。
返回字段：broad_track、sub_track、competitors；每个 competitor 包含 name、relationship_type、reason、confidence。
relationship_type 只能是 direct_substitute、capability_chaser、distribution_power。confidence 为 0 到 1。"""
    payload: dict = {}
    try:
        raw = (await model_call(system_prompt, user_prompt)).strip()
        if "```" in raw:
            raw = raw.split("```json", 1)[-1].split("```", 1)[0] if "```json" in raw else raw.split("```", 2)[1]
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        # 范围确认必须可用；模型不可用时继续使用确定性种子和用户输入。
        payload = {}

    search_names: list[str] = []
    if not payload and not fallback_names and search_discover is not None:
        try:
            search_names = list(dict.fromkeys(await search_discover(product, broad_track, limit)))[:limit]
        except Exception:
            search_names = []

    model_rows = [] if track_only else [item for item in payload.get("competitors", []) if isinstance(item, dict)]
    model_by_name = {str(item.get("name", "")).strip(): item for item in model_rows}
    if track_only:
        names = []
    elif manual_names:
        names = fallback_names
    else:
        names = [str(item.get("name", "")).strip() for item in model_rows if str(item.get("name", "")).strip()]
        names = list(dict.fromkeys(names))[:limit] or fallback_names or search_names
    inferred_track = str(payload.get("broad_track") or broad_track or "").strip()
    inferred_sub_track = str(payload.get("sub_track") or inferred_track or "").strip()
    snapshot = build_scope_snapshot(product, inferred_track, names, sub_track=inferred_sub_track)
    for row in snapshot["competitors"]:
        model_row = model_by_name.get(str(row["name"]), {})
        relationship = str(model_row.get("relationship_type") or row["relationship_type"])
        if relationship not in {"direct_substitute", "capability_chaser", "distribution_power"}:
            relationship = str(row["relationship_type"])
        try:
            confidence = float(model_row.get("confidence") or row["confidence"])
        except (TypeError, ValueError):
            confidence = float(row["confidence"])
        row.update({
            "relationship_type": relationship,
            "reason": str(model_row.get("reason") or row["reason"]),
            "confidence": min(1.0, max(0.0, confidence)),
        })
    snapshot["discovery_method"] = (
        "model" if payload else "search_fallback" if search_names else "deterministic_fallback"
    )
    return snapshot


def validate_scope_snapshot(snapshot: object, product: str, track: str, names: list[str]) -> str:
    """验证分析请求没有偏离前端确认过的不可变范围。"""
    if not isinstance(snapshot, dict):
        return ""
    if not snapshot.get("confirmed"):
        return "研究范围尚未确认"
    if str(snapshot.get("subject", "")).strip() != product.strip():
        return "研究对象与已确认范围不一致"
    frozen_track = str(snapshot.get("broad_track", "")).strip()
    if frozen_track not in {"", "待用户确认"} and frozen_track != track.strip():
        return "赛道与已确认范围不一致"
    frozen = [
        str(item.get("name", "")).strip()
        for item in snapshot.get("competitors", [])
        if isinstance(item, dict) and item.get("selected", True)
    ]
    if frozen != names:
        return "竞品集合与已确认范围不一致"
    return ""


def policy_payload(policy: AnalysisModePolicy) -> dict[str, object]:
    """输出适合 API 展示的中文预算明细。"""
    return {
        "mode": policy.name,
        "competitor_limit": policy.competitor_limit,
        "rework_rounds": policy.rework_rounds,
        "acquisition_budget": asdict(policy.budget),
    }


__all__ = [
    "AnalysisModePolicy", "MODE_POLICIES", "analysis_mode_policy",
    "build_scope_snapshot", "discover_analysis_scope", "extract_competitor_names_from_search",
    "policy_payload", "validate_scope_snapshot",
]
