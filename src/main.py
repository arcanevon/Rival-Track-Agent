"""
Entry point — starts the WebSocket server + HTTP static file server,
runs the pipeline, and opens the browser.
"""

import asyncio
import errno
import json
import logging
import os
import re
import sys
import unicodedata
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

# 将项目根目录加入 sys.path，确保可以把 src 作为包导入。
# 直接运行 `python src/main.py` 时，Python 会把 sys.path[0] 设为脚本目录，
# 因此需要显式加入项目根目录。
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

# 优先加载环境变量文件
load_dotenv()

from aiohttp import ClientSession, ClientTimeout, web

from src.server.ws import (
    start_server, broadcast_error, broadcast_node_update,
    broadcast_pipeline_complete, reset_state,
)
from src.pipeline.dag import run_pipeline, run_pipeline_custom
from src.pipeline.dag import load_fallback
from src.intake.constants import (
    READABLE_FETCH_HEADERS,
    SOURCE_BUCKETS,
    source_status_label as _source_status_label,
    source_ui_group as _source_ui_group,
)
from src.intake.discovery import (
    build_source_candidates as _build_source_candidates,
    build_source_candidates_with_search as _build_source_candidates_with_search,
)
from src.intake.acquisition import AcquisitionBudget, acquire_competitor_inputs
from src.intake.scope import (
    analysis_mode_policy, discover_analysis_scope, extract_competitor_names_from_search,
    policy_payload, validate_scope_snapshot,
)
from src.intake.hydrate import (
    fetch_readable_source as _fetch_readable_url,
)
from src.intake.search import (
    search_api_provider as _search_api_provider,
    search_web_api as _search_web_api,
)
from src.intake.url_security import is_safe_public_url
from src.models.output import AgentNodeOutput, AgentRole, AgentStatus
from src.memory.evidence_workspace import EvidenceWorkspaceStore
from src.reporting.revision import compile_annotation_gap, propose_revision

MAX_CUSTOM_COMPETITORS = 8
_EVIDENCE_WORKSPACE = EvidenceWorkspaceStore()

@web.middleware
async def _cors_middleware(request, handler):
    """Add CORS headers so the frontend can connect from any origin during development."""
    if request.method == "OPTIONS":
        return web.Response(status=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        })
    response = await handler(request)
    response.headers.setdefault("Access-Control-Allow-Origin", "*")
    return response


# ── 日志 ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

# ── 路径 ─────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.parent
FRONTEND_DIR = PROJECT_DIR / "src" / "frontend"
DATA_DIR = PROJECT_DIR / "data"

# 允许提供静态文件的目录，用于阻止路径穿越
SERVE_ROOTS: dict[str, Path] = {
    "frontend": FRONTEND_DIR,
    "data": DATA_DIR,
}

# 文件扩展名到 MIME 类型的映射
MIME_TYPES: dict[str, str] = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def _resolve_safe_path(request_path: str) -> Path | None:
    """Resolve a request path to a file within allowed serve roots.

    Returns the resolved Path or None if the path escapes the sandbox.
    """
    # 规范化路径：去掉开头斜杠并拒绝路径穿越模式
    clean = request_path.lstrip("/")
    # Unicode 规范化：折叠全角句点等形似字符
    clean = unicodedata.normalize("NFKC", clean)
    if "\x00" in clean or ".." in clean or "\\" in clean or clean.startswith("~"):
        return None

    # 依次尝试每个静态文件根目录
    for _prefix, root in SERVE_ROOTS.items():
        # 特殊处理 /data/...：去掉 data/ 前缀后映射到项目数据目录
        if clean.startswith("data/") and root == DATA_DIR:
            rel = clean[len("data/"):]  # strip "data/" prefix
            candidate = (root / rel).resolve()
            if str(candidate).startswith(str(root.resolve())):
                if candidate.is_file():
                    return candidate
        candidate = (root / (clean or "index.html")).resolve()
        if str(candidate).startswith(str(root.resolve())):
            if candidate.is_file():
                return candidate

    # 默认返回前端目录中的 index.html
    default = (FRONTEND_DIR / "index.html").resolve()
    return default if default.is_file() else None


async def http_handler(request: web.Request) -> web.Response:
    """Serve static files, sandboxed to frontend/ and data/ directories."""
    path: str = request.match_info.get("path", "index.html")
    file_path = _resolve_safe_path(path)

    if file_path is None or not file_path.is_file():
        file_path = FRONTEND_DIR / "index.html"

    content_type = MIME_TYPES.get(file_path.suffix, "application/octet-stream")
    use_charset = content_type.startswith("text/") or content_type == "application/javascript"

    if file_path.name == "index.html":
        body = file_path.read_text(encoding="utf-8").replace(
            "__RIVALTRACK_WS_PORT__",
            os.environ.get("WS_PORT", "8765"),
        ).encode("utf-8")
    else:
        body = file_path.read_bytes()

    kwargs: dict = {"body": body, "content_type": content_type}
    if use_charset:
        kwargs["charset"] = "utf-8"
    return web.Response(**kwargs)


def _is_address_in_use(error: OSError) -> bool:
    return (
        error.errno == errno.EADDRINUSE
        or getattr(error, "winerror", None) == 10048
    )


def _port_in_use_message(kind: str, host: str, port: int) -> str:
    return (
        f"{kind} port {port} is already in use on {host}.\n"
        f"Windows check: Get-NetTCPConnection -LocalPort {port} | "
        "Select-Object LocalAddress,LocalPort,State,OwningProcess\n"
        f"Option A: stop the existing process that owns port {port}.\n"
        f"Option B: start RivalTrack with another port, e.g. "
        f"$env:{'WS_PORT' if kind == 'WebSocket' else 'HTTP_PORT'}="
        f"\"{port + 1}\"; "
        "python src/main.py"
    )


def _normalise_needs_confirmation(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，、;\n；]+", value) if item.strip()]
    return []


async def api_analyze_handler(request: web.Request) -> web.Response:
    """POST /api/analyze — accept user-provided track + competitor data, run pipeline."""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON body",
                            content_type="text/plain")

    threat_target = body.get("threat_target", {})
    product_name = str(
        body.get("product_name")
        or body.get("product")
        or (threat_target.get("name") if isinstance(threat_target, dict) else "")
        or ""
    ).strip()
    track = str(body.get("track", "")).strip()
    mode_policy = analysis_mode_policy(body.get("analysis_mode"))
    user_data = body.get("competitors", [])
    if not product_name:
        return web.Response(status=400, text="Missing required product name",
                            content_type="text/plain")
    if not isinstance(threat_target, dict):
        threat_target = {}
    needs_confirmation = _normalise_needs_confirmation(
        threat_target.get(
            "needs_confirmation",
            ["positioning", "target_users", "core_capabilities", "competitive_concern"],
        )
    )
    threat_target = {
        "name": product_name,
        "positioning": str(threat_target.get("positioning", "")).strip(),
        "target_users": str(threat_target.get("target_users", "")).strip(),
        "core_capabilities": str(threat_target.get("core_capabilities", "")).strip(),
        "competitive_concern": str(
            threat_target.get("competitive_concern", "识别竞品对我方产品的威胁")
        ).strip(),
        "confidence": str(threat_target.get("confidence", "low")).strip() or "low",
        "needs_confirmation": needs_confirmation,
    }
    if not isinstance(user_data, list):
        return web.Response(status=400, text="'competitors' must be an array when provided",
                            content_type="text/plain")
    if len(user_data) > MAX_CUSTOM_COMPETITORS:
        return web.Response(status=400, text=f"单次分析最多支持 {MAX_CUSTOM_COMPETITORS} 个竞品",
                            content_type="text/plain", charset="utf-8")
    if len(user_data) > mode_policy.competitor_limit:
        return web.Response(status=400, text=f"单次分析最多支持 {MAX_CUSTOM_COMPETITORS} 个竞品",
                            content_type="text/plain")
    if len(user_data) == 0:
        user_data = _auto_discover_competitor_inputs(
            product_name, track, limit=mode_policy.competitor_limit,
        )

    if len(user_data) == 0:
        return web.Response(status=400,
                            text="无法自动发现竞品。请手动填写赛道名称和至少 1 个竞品名称。",
                            content_type="text/plain",
                            charset="utf-8")
    logger.info("API: custom analysis request — track=%s, %d competitors",
                track, len(user_data))

    # 为新任务清空展示状态
    reset_state()

    # 异步运行流程，避免阻塞 HTTP 响应
    competitor_names = _normalise_names(user_data)
    scope_error = validate_scope_snapshot(
        body.get("scope_snapshot"), product_name, track, competitor_names,
    )
    if body.get("scope_snapshot") is not None and scope_error:
        return web.Response(
            status=409, text=scope_error, content_type="text/plain", charset="utf-8",
        )
    reset_state()
    analysis_id = f"report_{uuid.uuid4().hex[:12]}"
    asyncio.create_task(_run_custom_and_broadcast(
        track, user_data, threat_target, analysis_id=analysis_id, mode=mode_policy.name,
    ))

    return web.json_response({
        "status": "started", "analysis_id": analysis_id,
        "analysis_mode": mode_policy.name, "policy": policy_payload(mode_policy),
    }, status=202)


def _normalise_names(raw_names) -> list[str]:
    """Return unique non-empty competitor/product names."""
    names: list[str] = []
    if isinstance(raw_names, str):
        raw_names = [n.strip() for n in raw_names.replace("，", ",").split(",")]
    if not isinstance(raw_names, list):
        return names

    for item in raw_names:
        if isinstance(item, dict):
            name = item.get("company") or item.get("name") or item.get("product")
        else:
            name = item
        if not isinstance(name, str):
            continue
        cleaned = name.strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names


DOMAIN_COMPETITOR_SEEDS: list[dict[str, object]] = [
    {
        "keywords": [
            "洛克王国", "游戏与互动娱乐", "手游", "二次元游戏", "开放世界游戏",
            "mobile game", "gacha game",
        ],
        "competitors": [
            "原神", "崩坏：星穹铁道", "明日方舟", "重返未来：1999", "鸣潮", "幻塔",
        ],
    },
    {
        "keywords": [
            "\u534e\u4e3a\u624b\u673a", "huawei phone", "huawei smartphone",
            "\u534e\u4e3a mate", "\u534e\u4e3a pura", "\u667a\u80fd\u624b\u673a", "\u624b\u673a",
        ],
        "competitors": ["\u82f9\u679c iPhone", "\u4e09\u661f Galaxy", "\u5c0f\u7c73\u624b\u673a"],
    },
    {
        "keywords": ["小马智行", "pony.ai", "pony ai", "robotaxi", "自动驾驶", "无人驾驶", "l4"],
        "competitors": ["文远知行 WeRide", "百度 Apollo", "Waymo"],
    },
    {
        "keywords": ["新能源汽车", "电动车", "ev", "智能汽车"],
        "competitors": ["比亚迪", "特斯拉", "蔚来"],
    },
    {
        "keywords": [
            "ai代码助手", "ai coding", "代码助手", "coding assistant", "codex",
            "github copilot", "copilot", "cursor", "claude code",
        ],
        "competitors": ["Cursor", "Claude Code", "OpenAI Codex"],
    },
]


def _seed_competitors_for_domain(product_name: str, track: str, limit: int) -> list[str]:
    text = f"{product_name} {track}".lower()
    for seed in DOMAIN_COMPETITOR_SEEDS:
        keywords = [str(keyword).lower() for keyword in seed["keywords"]]
        if any(keyword in text for keyword in keywords):
            names = [
                str(name) for name in seed["competitors"]
                if str(name).strip().lower() != product_name.strip().lower()
            ]
            return names[:limit]
    return []


def _auto_discover_competitor_names(product_name: str, track: str, limit: int = 3) -> list[str]:
    """Return a small, deterministic competitor set when the user only gives our product.

    This is the offline-safe half of Target & Competitor Discovery. At runtime the
    Collector prompt still asks the model to validate direct substitutes,
    capability chasers, and distribution powers from the provided evidence.
    """
    names: list[str] = []
    normalized_track = track.strip().lower()
    normalized_product = product_name.strip().lower()
    seed_names = _seed_competitors_for_domain(product_name, track, limit)
    if seed_names:
        return seed_names[:limit]
    cache_dir = DATA_DIR / "competitor-data"
    if cache_dir.exists():
        # 赛道为空时收集候选赛道，以便自动识别
        product_track_candidates: set[str] = set()
        scored: list[tuple[int, str]] = []
        all_entries: list[dict] = []
        for path in sorted(cache_dir.glob("**/*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            all_entries.append(data)
            name = str(data.get("company") or path.stem).strip()
            if not name or name.lower() == normalized_product:
                continue
            candidate_track = str(data.get("track", "")).strip().lower()
            metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
            haystack = " ".join([
                name,
                candidate_track,
                str(metadata.get("category", "")),
                str(metadata.get("segment", "")),
                str(metadata.get("positioning", "")),
            ]).lower()
            score = 0
            if normalized_track and candidate_track == normalized_track:
                score += 100
            elif normalized_track and (normalized_track in haystack or haystack in normalized_track):
                score += 60
            if normalized_product and normalized_product in haystack:
                score += 10
            if score > 0:
                scored.append((score, name))
        # 赛道为空时，根据产品是否出现在竞品元数据中推断赛道
        if not normalized_track:
            for entry in all_entries:
                entry_name = str(entry.get("company", "")).strip().lower()
                entry_track = str(entry.get("track", "")).strip()
                if (
                    entry_track
                    and (
                        entry_name == normalized_product
                        or normalized_product in entry_name
                        or entry_name in normalized_product
                    )
                ):
                    product_track_candidates.add(entry_track.lower())
            if product_track_candidates:
                detected_track = next(iter(product_track_candidates))
                for entry in all_entries:
                    name = str(entry.get("company") or "").strip()
                    if not name or name.lower() == normalized_product:
                        continue
                    if str(entry.get("track", "")).strip().lower() == detected_track:
                        scored.append((50, name))
        for _, name in sorted(scored, key=lambda item: (-item[0], item[1].lower())):
            if name not in names:
                names.append(name)
            if len(names) >= limit:
                return names

    return names


def _auto_discover_competitor_inputs(product_name: str, track: str, limit: int = 3) -> list[dict]:
    """Build competitor inputs for the pipeline when competitors are omitted."""
    names = _auto_discover_competitor_names(product_name, track, limit=limit)
    competitors: list[dict] = []
    for idx, name in enumerate(names, start=1):
        relationship = (
            "direct_substitute" if idx == 1
            else "capability_chaser" if idx == 2
            else "distribution_power"
        )
        candidates = _build_source_candidates(name, track)
        official_sources = []
        benchmark_sources = []
        community_sources = []
        leading_sources = []
        for source in candidates:
            entry = {
                "url": source.get("evidence_url") or source.get("url", ""),
                "label": source.get("label", f"{name} candidate source"),
                "scraped_text": (
                    f"自动发现候选来源。relationship_type={relationship}; "
                    f"direct_evidence={source.get('direct_evidence')}; "
                    f"note={source.get('note', '')}"
                ),
            }
            requested_types = set(source.get("requested_source_types") or [])
            channel = str(source.get("channel") or "")
            if channel == "community" or "community" in requested_types:
                community_sources.append(entry)
            elif channel == "leading" or "leading" in requested_types:
                leading_sources.append(entry)
            elif channel == "official" or "official" in requested_types:
                official_sources.append(entry)
            else:
                benchmark_sources.append(entry)
        competitors.append({
            "company": name,
            "official_sources": official_sources[:4],
            "benchmark_sources": benchmark_sources[:4],
            "community_sources": community_sources[:2],
            "leading_sources": leading_sources[:3],
            "metadata": {
                "discovered_by_agent": True,
                "relationship_type": relationship,
                "evidence_insufficient": True,
                "discovery_note": (
                    f"用户只填写了我方产品 {product_name}，系统默认选择前 {limit} 个候选竞品。"
                ),
            },
        })
    return competitors


async def api_discover_scope_handler(request: web.Request) -> web.Response:
    """POST /api/discover-scope：在采集前返回可编辑的赛道与竞品范围。"""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON body", content_type="text/plain")
    product = str(body.get("product") or body.get("product_name") or "").strip()
    track = str(body.get("track") or "").strip()
    sub_track = str(body.get("sub_track") or "").strip()
    stage = str(body.get("stage") or "full").strip().lower()
    if not product:
        return web.Response(status=400, text="请先填写研究对象", content_type="text/plain", charset="utf-8")
    policy = analysis_mode_policy(body.get("analysis_mode"))
    names = _normalise_names(body.get("competitors"))
    if stage == "competitors" and not track:
        return web.Response(status=400, text="请先确认赛道", content_type="text/plain", charset="utf-8")
    discovery_limit = 0 if stage == "track" else policy.competitor_limit
    seed_names = (
        [] if stage == "track"
        else _auto_discover_competitor_names(product, track, limit=policy.competitor_limit)
    )
    async def search_discover(subject: str, subject_track: str, limit: int) -> list[str]:
        if not _search_api_provider():
            return []
        queries = [
            f'{subject} 竞品 替代品 同类产品 {subject_track}',
            f'{subject} 对比 alternatives competitors',
        ]
        rows: list[dict] = []
        async with ClientSession(timeout=ClientTimeout(total=12)) as session:
            for query in queries:
                try:
                    rows.extend(await _search_web_api(session, query, count=max(8, limit * 2)))
                except Exception as exc:
                    logger.info("Scope search query failed: %s", exc)
        return extract_competitor_names_from_search(subject, rows, limit)

    snapshot = await discover_analysis_scope(
        product, track, names[:policy.competitor_limit], discovery_limit,
        seed_names=seed_names, search_discover=search_discover, track_only=stage == "track",
    )
    if stage == "track":
        snapshot["competitors"] = []
        snapshot["confirmed"] = False
    elif stage == "competitors":
        # 竞品发现必须服从用户刚刚确认的赛道，不能被第二次模型调用静默改写。
        snapshot["broad_track"] = track
        snapshot["sub_track"] = sub_track or snapshot.get("sub_track") or track
    return web.json_response({**snapshot, "policy": policy_payload(policy)})


async def api_discover_sources_handler(request: web.Request) -> web.Response:
    """POST /api/discover-sources - return candidate authoritative URLs."""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON body",
                            content_type="text/plain")

    track = str(body.get("track", "")).strip()
    names = _normalise_names(body.get("competitors") or body.get("products") or body.get("names"))
    if not names:
        product = str(body.get("product", "")).strip()
        if product:
            names = _auto_discover_competitor_names(product, track, limit=3) or [product]
    if not names:
        return web.Response(status=400, text="请先填写我方产品名称，或至少填写 1 个竞品名称。",
                            content_type="text/plain", charset="utf-8")

    items = []
    messages: list[str] = []
    async with ClientSession(timeout=ClientTimeout(total=12)) as session:
        for name in names:
            sources, message = await _build_source_candidates_with_search(session, name, track)
            for source in sources:
                source.setdefault("source_group", _source_ui_group(source))
                source.setdefault("source_status", _source_status_label(source))
            items.append({"name": name, "sources": sources})
            if message not in messages:
                messages.append(message)
    return web.json_response({
        "track": track,
        "items": items,
        "verified": bool(_search_api_provider()),
        "search_provider": _search_api_provider() or "",
        "message": "；".join(messages) if messages else "已返回证据来源，请 review 后开始分析。",
    })


async def api_fetch_source_handler(request: web.Request) -> web.Response:
    """POST /api/fetch-source - fetch a concrete page and extract readable text."""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON body",
                            content_type="text/plain")

    url = str(body.get("url", "")).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return web.Response(status=400, text="Missing valid http(s) URL",
                            content_type="text/plain")
    if not is_safe_public_url(url):
        return web.Response(status=400, text="URL blocked: internal/private hosts not allowed",
                            content_type="text/plain")

    timeout = ClientTimeout(total=12)
    try:
        async with ClientSession(timeout=timeout, headers=READABLE_FETCH_HEADERS) as session:
            return web.json_response(await _fetch_readable_url(session, url))
    except Exception as exc:
        logger.info("Source fetch failed for %s: %s", url, exc)
        return web.Response(status=502, text="Fetch failed: the upstream URL could not be read.",
                            content_type="text/plain")


async def api_evidence_workspace_handler(request: web.Request) -> web.Response:
    """GET /api/evidence：查询跨报告证据工作区。"""
    return web.json_response({
        "items": _EVIDENCE_WORKSPACE.list_evidence(
            track=request.query.get("track", ""), status=request.query.get("status", ""),
        )
    })


async def api_evidence_review_handler(request: web.Request) -> web.Response:
    """PATCH /api/evidence/{id}/review：保存人工审核覆盖层。"""
    try:
        body = await request.json()
        row = _EVIDENCE_WORKSPACE.review_evidence(
            request.match_info["evidence_id"], str(body.get("status", "")), str(body.get("note", "")),
        )
    except (ValueError, TypeError) as exc:
        return web.Response(status=400, text=str(exc), content_type="text/plain", charset="utf-8")
    return web.json_response(row) if row else web.Response(status=404, text="Evidence not found")


async def api_human_metrics_handler(request: web.Request) -> web.Response:
    """GET /api/quality/human-metrics：返回人工修正质量指标。"""
    return web.json_response(_EVIDENCE_WORKSPACE.metrics())


async def api_report_revise_handler(request: web.Request) -> web.Response:
    """POST /api/report/revise：生成待确认的章节建议稿。"""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON body", content_type="text/plain")
    report_id = str(body.get("report_id", ""))
    section_id = str(body.get("section_id", ""))
    report = _EVIDENCE_WORKSPACE.get_report(report_id)
    if not report:
        return web.Response(status=404, text="Report not found")
    writer = next((row for row in report["outputs"] if row.get("role") == "writer"), {})
    original = str((writer.get("report_sections") or {}).get(section_id, ""))
    if not original:
        return web.Response(status=400, text="Unknown report section")
    annotation = {
        "annotation_id": f"ann_{uuid.uuid4().hex[:12]}",
        "section_id": section_id, "quote": str(body.get("quote", "")),
        "comment": str(body.get("comment", "")), "intent": str(body.get("intent", "comment_only")),
        "competitors": body.get("competitors", []), "dimensions": body.get("dimensions", []),
    }
    annotation["evidence_gap"] = compile_annotation_gap(annotation)
    _EVIDENCE_WORKSPACE.add_annotation(report_id, annotation)
    if annotation["intent"] in {"highlight_only", "comment_only"}:
        return web.json_response({"kind": "annotation", "annotation": annotation})
    new_evidence_ids: list[str] = []
    if annotation["evidence_gap"]["requires_research"]:
        names = list(annotation.get("competitors") or (writer.get("threat_scores") or {}).keys())[:3]
        if names:
            supplemental_inputs = [{
                "company": name, "official_sources": [], "benchmark_sources": [],
                "community_sources": [], "leading_sources": [],
            } for name in names]
            collected, _ = await acquire_competitor_inputs(
                supplemental_inputs, str(report.get("track", "")), analysis_mode_policy("fast").budget,
            )
            rows = [
                source for item in collected for bucket in SOURCE_BUCKETS
                for source in item.get(bucket, []) if isinstance(source, dict)
            ]
            new_evidence_ids = _EVIDENCE_WORKSPACE.upsert_collected_evidence(
                report_id, str(report.get("track", "")), rows,
            )
    evidence = [
        item for item in _EVIDENCE_WORKSPACE.list_evidence()
        if report_id in item.get("report_ids", []) and item.get("human_status") != "rejected"
    ]
    try:
        revision = await propose_revision(section_id, original, annotation, evidence)
    except Exception as exc:
        logger.exception("Report revision failed")
        return web.Response(status=502, text=f"AI 修订失败：{exc}", content_type="text/plain", charset="utf-8")
    revision["annotation_id"] = annotation["annotation_id"]
    revision["new_evidence_ids"] = new_evidence_ids
    _EVIDENCE_WORKSPACE.add_revision(report_id, revision)
    return web.json_response(revision)


async def api_report_revision_decision_handler(request: web.Request) -> web.Response:
    """POST /api/report/revision-decision：接受或拒绝建议稿。"""
    try:
        body = await request.json()
        row = _EVIDENCE_WORKSPACE.decide_revision(
            str(body.get("report_id", "")), str(body.get("revision_id", "")),
            str(body.get("decision", "")),
        )
    except ValueError as exc:
        return web.Response(status=400, text=str(exc), content_type="text/plain", charset="utf-8")
    return web.json_response(row) if row else web.Response(status=404, text="Revision not found")


async def _run_custom_and_broadcast(
    track: str,
    user_data: list[dict],
    threat_target: dict[str, object] | None = None,
    *,
    analysis_id: str = "",
    mode: str = "standard",
):
    """Run custom pipeline and broadcast results."""
    try:
        await broadcast_node_update(AgentNodeOutput(
            node_id="collector", role=AgentRole.COLLECTOR, status=AgentStatus.RUNNING,
            label="采集 Agent", framework="O/B/C/L",
            output_summary="正在并行搜索竞品来源、读取正文并建立证据账本。",
        ))
        policy = analysis_mode_policy(mode)
        user_data, acquisition_trace = await acquire_competitor_inputs(
            user_data,
            track,
            policy.budget,
        )
        logger.info("Acquisition batch complete: %d trace events", len(acquisition_trace))
        results = await run_pipeline_custom(
            track,
            user_data,
            threat_target,
            enable_agent_tools=True,
            max_rework_rounds=policy.rework_rounds,
        )
        if analysis_id:
            _EVIDENCE_WORKSPACE.save_report(analysis_id, track, results)
        await broadcast_pipeline_complete(results)
        logger.info("Custom pipeline complete. %d agents finished.", len(results))
    except Exception as e:
        logger.exception("Custom pipeline error")
        await broadcast_error(f"自定义分析失败：{e}", node_id="pipeline")


async def run_pipeline_task():
    """Run the pipeline and broadcast completion."""
    reset_state()
    try:
        results = await run_pipeline()
        await broadcast_pipeline_complete(results)
        logger.info("Pipeline complete. %d agents finished.", len(results))
    except Exception as e:
        logger.exception("Pipeline error")
        fallback = load_fallback()
        if fallback:
            logger.info("Loaded %d fallback outputs.", len(fallback))
            await broadcast_pipeline_complete(fallback)


async def main():
    host = os.environ.get("WS_HOST", "localhost")
    ws_port = int(os.environ.get("WS_PORT", "8765"))
    http_port = int(os.environ.get("HTTP_PORT", "8080"))

    # 启动 WebSocket 服务器
    try:
        ws_server = await start_server(host, ws_port)
    except OSError as e:
        if _is_address_in_use(e):
            logger.error(_port_in_use_message("WebSocket", host, ws_port))
            sys.exit(1)
        raise

    # 启动 HTTP 服务器
    http_app = web.Application(middlewares=[_cors_middleware])
    http_app.router.add_post("/api/analyze", api_analyze_handler)
    http_app.router.add_post("/api/discover-scope", api_discover_scope_handler)
    http_app.router.add_post("/api/discover-sources", api_discover_sources_handler)
    http_app.router.add_post("/api/fetch-source", api_fetch_source_handler)
    http_app.router.add_get("/api/evidence", api_evidence_workspace_handler)
    http_app.router.add_patch("/api/evidence/{evidence_id}/review", api_evidence_review_handler)
    http_app.router.add_get("/api/quality/human-metrics", api_human_metrics_handler)
    http_app.router.add_post("/api/report/revise", api_report_revise_handler)
    http_app.router.add_post("/api/report/revision-decision", api_report_revision_decision_handler)
    http_app.router.add_get("/{path:.*}", http_handler)
    runner = web.AppRunner(http_app)
    await runner.setup()
    http_site = web.TCPSite(runner, host, http_port)
    try:
        await http_site.start()
    except OSError as e:
        ws_server.close()
        await ws_server.wait_closed()
        await runner.cleanup()
        if _is_address_in_use(e):
            logger.error(_port_in_use_message("HTTP", host, http_port))
            sys.exit(1)
        raise
    logger.info("HTTP server on http://%s:%s", host, http_port)

    # 除非自动化验证明确禁用，否则打开浏览器
    url = f"http://{host}:{http_port}"
    if os.environ.get("RIVALTRACK_NO_BROWSER", "").lower() not in ("1", "true", "yes"):
        logger.info("Opening %s", url)
        webbrowser.open(url)
    else:
        logger.info("Browser auto-open skipped. Visit %s", url)

    # 仅在显式请求时运行演示流程；界面默认保持空白，供用户选择历史、样例或新分析。
    autorun = os.environ.get("RIVALTRACK_AUTORUN", "").lower() in ("1", "true", "yes")
    if autorun and os.environ.get("RIVALTRACK_SKIP_AUTORUN", "").lower() not in ("1", "true", "yes"):
        asyncio.create_task(run_pipeline_task())
    else:
        logger.info("Pipeline autorun skipped.")

    # 保持服务器运行
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await runner.cleanup()
        ws_server.close()
        await ws_server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
