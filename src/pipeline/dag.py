"""构建并运行 LangGraph 多智能体流程。

拓扑：
    START -> tool_planner -> tools? -> collector -> analyst_a ---> qa -> quality_gate
                                                    -> analyst_b -/             |
                      ^----------------- collect rework ------------------------|
                                           analyze rework -> analyst_a + analyst_b
                                           pass/max rounds -> writer -> END

两个分析 Agent 都只依赖 Collector，因此会并行运行；QA 会等待两者完成后汇合。
"""

import json
import logging
from pathlib import Path
from typing import Annotated, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from src.agents.tools import COLLECTOR_TOOLS
from src.intake.plan import build_evidence_acquisition_plans, build_evidence_gaps
from src.intake.quality import build_source_quality_context
from src.intake.enrich import build_cache_from_user_data
from src.intake.acquisition import merge_tool_observations, stamp_evidence_identities
from src.intake.search import search_api_provider
from src.memory import LongTermMemoryStore
from src.models.output import AgentNodeOutput, AgentStatus
from src.server.ws import broadcast_node_update, reset_state as _reset_ws_state

from .cache import load_cache
from .nodes import run_collector, run_analyst_a, run_analyst_b, run_qa, run_writer
from .format import _default_threat_target
from .quality_gate import evaluate_quality_gate

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
FALLBACK_FILE = DATA_DIR / "demo-fallback.json"
_SHORT_TERM_MEMORY = MemorySaver()
_LONG_TERM_MEMORY = LongTermMemoryStore()


# ── LangGraph 流程状态 ──────────────────────────────────────────────────

class _PipelineGraphState(TypedDict, total=False):
    """在 LangGraph DAG 中流动的短期共享状态。

    每个节点从中读取上游结果并写回局部更新；LangGraph 负责并行分支和汇合。
    """
    track: str
    threat_target: dict[str, object]
    competitors: list[str]
    cache_data: dict
    agent_tools_enabled: bool
    long_term_memory: str
    messages: Annotated[list[BaseMessage], add_messages]
    collector_output: dict | None
    analyst_a_output: dict | None
    analyst_b_output: dict | None
    qa_output: dict | None
    writer_output: dict | None
    rework_round: int
    max_rework_rounds: int
    rework_feedback: str
    quality_gate_decision: dict[str, object]
    quality_history: list[dict[str, object]]
    evidence_gaps: list[dict[str, object]]
    processed_tool_call_ids: list[str]
    acquisition_ledger: dict[str, object]
    acquisition_trace: list[dict[str, object]]
    error: str | None


# ── 序列化辅助函数 ──────────────────────────────────────────────────────

def _node_to_state(output: AgentNodeOutput) -> dict:
    return output.model_dump()


def _state_to_node(data: dict | None) -> AgentNodeOutput | None:
    if data is None:
        return None
    return AgentNodeOutput(**data)


# ── LangGraph 节点适配函数 ──────────────────────────────────────────────
#
# 每个适配函数从共享状态读取输入，调用异步 Agent，并返回局部状态更新。
# WebSocket 广播仍由各 Agent 内部负责。两个分析 Agent 都只依赖 Collector，
# 因此 LangGraph 会自动并行运行它们。

def _plan_collector_tool_calls(state: _PipelineGraphState) -> list[dict]:
    """依据证据缺口生成有限的搜索和网页读取动作。"""
    if not state.get("agent_tools_enabled", False):
        return []
    calls: list[dict] = []
    cache_data = state.get("cache_data", {})
    track = state.get("track", "")
    ledger = state.get("acquisition_ledger", {})
    query_fingerprints = set(ledger.get("query_fingerprints", []) if isinstance(ledger, dict) else [])
    for company, item in cache_data.items():
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if isinstance(metadata, dict):
            query_fingerprints.update(metadata.get("query_fingerprints", []) or [])

    search_budget = 0
    if search_api_provider():
        has_structured_gaps = "evidence_gaps" in state
        if has_structured_gaps:
            gaps = [gap for gap in state.get("evidence_gaps", []) if isinstance(gap, dict)]
        else:
            gaps = []
            for company, plan in build_evidence_acquisition_plans(cache_data, track).items():
                for query in plan.get("queries", []) or []:
                    if isinstance(query, dict):
                        gaps.append({"competitor": company, **query})
        # 每轮至少让每个竞品获得一次返工机会，深度模式最多覆盖 8 个竞品。
        search_budget = min(8, len({str(gap.get("competitor") or "") for gap in gaps}))
        if gaps:
            offset = (int(state.get("rework_round", 0) or 0) * max(1, search_budget)) % len(gaps)
            gaps = gaps[offset:] + gaps[:offset]
        planned_companies: set[str] = set()
        for gap in gaps:
            company = str(gap.get("competitor") or "").strip()
            query_text = str(gap.get("query") or "").strip()
            fingerprint = f"{company}|{query_text}"
            if (not company or company in planned_companies or not query_text
                    or fingerprint in query_fingerprints):
                continue
            source_types = set(gap.get("source_types") or gap.get("required_source_types") or [])
            tool_name = "search_community_evidence" if "community" in source_types else "search_competitor_evidence"
            calls.append({
                "name": tool_name,
                "args": {
                    "query": query_text,
                    "competitor": company,
                    "track": track,
                    "freshness": str(gap.get("freshness", "noLimit")),
                    "source_type": sorted(source_types)[0] if source_types else "benchmark",
                },
                "id": f"search-{state.get('rework_round', 0)}-{len(calls)}",
                "type": "tool_call",
            })
            planned_companies.add(company)
            if len(calls) >= search_budget:
                break

    retry_statuses = {"fetch_failed", "candidate_text", "needs_fetch", "pending"}
    reader_companies: set[str] = set()
    for company, competitor in cache_data.items():
        if not isinstance(competitor, dict):
            continue
        for bucket in ("official_sources", "benchmark_sources", "community_sources", "leading_sources"):
            for source in competitor.get(bucket, []) or []:
                if not isinstance(source, dict):
                    continue
                status = str(source.get("evidence_status", ""))
                url = str(source.get("evidence_url") or source.get("url") or "").strip()
                if not url or status not in retry_statuses:
                    continue
                calls.append({
                    "name": "read_evidence_page",
                    "args": {"url": url, "competitor": company},
                    "id": f"reader-{state.get('rework_round', 0)}-{len(calls)}",
                    "type": "tool_call",
                })
                reader_companies.add(company)
                break
            if company in reader_companies:
                break
        if len(calls) >= search_budget + 4:
            break
    return calls[:12]


async def _lg_tool_planner(state: _PipelineGraphState) -> dict:
    """执行 ReAct 的 Thought/Action 阶段。"""
    tool_calls = _plan_collector_tool_calls(state)
    thought = "发现证据缺口，调用工具补充观察。" if tool_calls else "无需调用工具，直接使用现有证据。"
    return {"messages": [AIMessage(content=thought, tool_calls=tool_calls)]}


def _route_after_tool_planning(state: _PipelineGraphState) -> str:
    """根据 Action 是否存在选择 ToolNode 或 Collector。"""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else "collector"


def _format_tool_observations(messages: list[BaseMessage]) -> str:
    """把 ToolNode 的 Observation 压缩为 Collector 上下文。"""
    observations = [message.content for message in messages if isinstance(message, ToolMessage)]
    return "\n".join(str(item) for item in observations[-6:])[-10000:]


async def _lg_merge_tool_results(state: _PipelineGraphState) -> dict:
    """把本轮工具结果写回证据缓存，使 Collector 与 Quality Gate 读取同一事实集。"""
    tool_messages = [message for message in state.get("messages", []) if isinstance(message, ToolMessage)]
    cache, processed, trace = merge_tool_observations(
        state.get("cache_data", {}),
        tool_messages,
        state.get("processed_tool_call_ids", []),
    )
    query_fingerprints = {
        fingerprint
        for item in cache.values() if isinstance(item, dict)
        for fingerprint in ((item.get("metadata") or {}).get("query_fingerprints", [])
                            if isinstance(item.get("metadata"), dict) else [])
    }
    return {
        "cache_data": cache,
        "processed_tool_call_ids": processed,
        "acquisition_trace": [*state.get("acquisition_trace", []), *trace],
        "acquisition_ledger": {"query_fingerprints": sorted(query_fingerprints)},
        "evidence_gaps": build_evidence_gaps(cache, state.get("track", "")),
    }


def _aggregate_evidence_relevance(cache_data: dict) -> dict[str, object]:
    """汇总各竞品抓取后的相关性指标，供 QA Quality Gate 使用。"""
    rows = []
    for competitor in cache_data.values():
        if not isinstance(competitor, dict):
            continue
        metadata = competitor.get("metadata")
        metrics = metadata.get("evidence_relevance") if isinstance(metadata, dict) else None
        if isinstance(metrics, dict) and int(metrics.get("evaluated_sources", 0) or 0) > 0:
            rows.append(metrics)
    if not rows:
        return {}
    evaluated = sum(int(row.get("evaluated_sources", 0) or 0) for row in rows)
    accepted = sum(int(row.get("accepted_sources", 0) or 0) for row in rows)
    return {
        "evaluated_sources": evaluated,
        "accepted_sources": accepted,
        "precision_at_5": round(sum(float(row.get("precision_at_5", 0) or 0) for row in rows) / len(rows), 3),
        "official_precision": round(sum(float(row.get("official_precision", 0) or 0) for row in rows) / len(rows), 3),
        "claim_answer_rate": round(sum(float(row.get("claim_answer_rate", 0) or 0) for row in rows) / len(rows), 3),
        "bad_domain_leakage": round(sum(float(row.get("bad_domain_leakage", 0) or 0) for row in rows) / len(rows), 3),
        "unique_domains_at_5": sum(int(row.get("unique_domains_at_5", 0) or 0) for row in rows),
    }


async def _lg_collector(state: _PipelineGraphState) -> dict:
    """LangGraph 节点：采集 Agent。"""
    output = await run_collector(
        state["track"], state["competitors"], state.get("cache_data", {}),
        state.get("threat_target"),
        state.get("long_term_memory", ""),
        "\n\n".join(filter(None, [
            _format_tool_observations(state.get("messages", [])),
            state.get("rework_feedback", ""),
        ])),
    )
    if output.status == AgentStatus.ERROR:
        return {"error": f"Collector failed: {output.output_summary}",
                "collector_output": _node_to_state(output)}
    trace = list(state.get("acquisition_trace", []))
    if trace:
        output.quality_metrics = {**output.quality_metrics, "acquisition_trace": trace[-40:]}
    return {"collector_output": _node_to_state(output)}


async def _lg_analyst_a(state: _PipelineGraphState) -> dict:
    """LangGraph node: Analyst A (VRIO over shared O/B/C/L evidence)."""
    collector = _state_to_node(state["collector_output"])
    if collector is None:
        return {"error": "Analyst A: missing collector output"}
    output = await run_analyst_a(
        collector,
        state.get("cache_data", {}),
        state.get("threat_target"),
        state.get("rework_feedback", ""),
        state.get("track", ""),
    )
    return {"analyst_a_output": _node_to_state(output)}


async def _lg_analyst_b(state: _PipelineGraphState) -> dict:
    """LangGraph node: Analyst B (SWOT over shared O/B/C/L evidence)."""
    collector = _state_to_node(state["collector_output"])
    if collector is None:
        return {"error": "Analyst B: missing collector output"}
    output = await run_analyst_b(
        collector,
        state.get("cache_data", {}),
        state.get("threat_target"),
        state.get("rework_feedback", ""),
        state.get("track", ""),
    )
    return {"analyst_b_output": _node_to_state(output)}


async def _lg_qa(state: _PipelineGraphState) -> dict:
    """LangGraph 节点：质检与反思。

    单个分析 Agent 失败时使用中性占位结果，使 QA 仍可降级输出。
    """
    analyst_a = _state_to_node(state.get("analyst_a_output"))
    analyst_b = _state_to_node(state.get("analyst_b_output"))

    if analyst_a is None and analyst_b is None:
        return {"error": "QA: both analysts missing output"}

    if analyst_a is None:
        analyst_a = AgentNodeOutput(
            node_id="analyst-a", role="analyst-a", status=AgentStatus.ERROR,
            label="能力持久性分析 Agent · VRIO", framework="VRIO",
            output_summary="Analyst A did not produce output.",
        )
    if analyst_b is None:
        analyst_b = AgentNodeOutput(
            node_id="analyst-b", role="analyst-b", status=AgentStatus.ERROR,
            label="市场动态与用户替代分析 Agent · SWOT", framework="SWOT Analysis",
            output_summary="Analyst B did not produce output.",
        )
    expected = state.get("competitors") or []
    source_quality_context = build_source_quality_context(state.get("cache_data", {}))
    output = await run_qa(analyst_a, analyst_b, state.get("threat_target"), expected, source_quality_context)
    return {"qa_output": _node_to_state(output)}


async def _lg_writer(state: _PipelineGraphState) -> dict:
    """LangGraph 节点：撰写 Agent。"""
    qa = _state_to_node(state.get("qa_output"))
    if qa is None:
        return {"error": "Writer: missing QA output"}
    source_quality_context = build_source_quality_context(state.get("cache_data", {}))
    output = await run_writer(qa, state.get("threat_target"), source_quality_context)
    return {"writer_output": _node_to_state(output)}


async def _lg_quality_gate(state: _PipelineGraphState) -> dict:
    """评估 QA 质量，记录复审指标并准备条件返工。"""
    qa = _state_to_node(state.get("qa_output"))
    if qa is None:
        return {
            "error": "Quality Gate: missing QA output",
            "quality_gate_decision": {"route": "write", "reason": "缺少 QA 输出"},
        }

    history = list(state.get("quality_history", []))
    previous_metrics = history[-1].get("metrics") if history else None
    expected = state.get("competitors", [])
    result = evaluate_quality_gate(
        qa,
        expected,
        rework_round=state.get("rework_round", 0),
        max_rework_rounds=state.get("max_rework_rounds", 1),
        tools_enabled=state.get("agent_tools_enabled", False),
        previous_metrics=previous_metrics if isinstance(previous_metrics, dict) else None,
        evidence_metrics=_aggregate_evidence_relevance(state.get("cache_data", {})),
        analyst_outputs=tuple(
            item for item in (
                _state_to_node(state.get("analyst_a_output")),
                _state_to_node(state.get("analyst_b_output")),
            ) if item is not None
        ),
    )
    entry = result.as_dict()
    history.append(entry)
    updated_qa = qa.model_copy(update={
        "quality_metrics": result.metrics,
        "rework_history": history,
    })
    await broadcast_node_update(updated_qa)

    qa_structured_gaps = [
        gap for gap in qa.evidence_gaps
        if isinstance(gap, dict) and gap.get("competitor") and gap.get("query")
    ]
    deterministic_gaps = build_evidence_gaps(state.get("cache_data", {}), state.get("track", ""))
    updates: dict[str, object] = {
        "qa_output": _node_to_state(updated_qa),
        "quality_gate_decision": entry,
        "quality_history": history,
        "rework_feedback": (
            "=== QUALITY GATE 返工要求 ===\n"
            f"原因：{result.reason}\n"
            f"指标：{json.dumps(result.metrics, ensure_ascii=False)}\n"
            f"证据缺口：{json.dumps(qa.evidence_gaps[:8], ensure_ascii=False)}"
        ),
        "evidence_gaps": qa_structured_gaps or deterministic_gaps,
    }
    if result.route != "write":
        updates["rework_round"] = state.get("rework_round", 0) + 1
    return updates


def _route_after_quality_gate(state: _PipelineGraphState) -> str | list[str]:
    """按 Quality Gate 判定进入采集返工、双分析返工或写作。"""
    route = state.get("quality_gate_decision", {}).get("route", "write")
    if route == "collect":
        return "tool_planner"
    if route == "analyze":
        return ["analyst_a", "analyst_b"]
    return "writer"


# ── 图构建 ──────────────────────────────────────────────────────────────

def build_pipeline_dag(checkpointer=None) -> StateGraph:
    """构建带短期记忆和证据工具的 LangGraph DAG。

    Topology:
        START -> tools? -> collector -> analyst_a ---> qa -> quality_gate
                                      -> analyst_b -/             |
                         quality_gate 可返回采集、双分析或进入 writer。

    LangGraph runs analyst_a and analyst_b in parallel because both
    depend only on collector and neither depends on the other.
    QA automatically waits for both analysts to complete.
    """
    graph = StateGraph(_PipelineGraphState)

    graph.add_node("tool_planner", _lg_tool_planner)
    graph.add_node("tools", ToolNode(COLLECTOR_TOOLS, handle_tool_errors=True))
    graph.add_node("merge_tool_results", _lg_merge_tool_results)
    graph.add_node("collector", _lg_collector)
    graph.add_node("analyst_a", _lg_analyst_a)
    graph.add_node("analyst_b", _lg_analyst_b)
    graph.add_node("qa", _lg_qa)
    graph.add_node("quality_gate", _lg_quality_gate)
    graph.add_node("writer", _lg_writer)

    graph.add_edge(START, "tool_planner")
    graph.add_conditional_edges(
        "tool_planner",
        _route_after_tool_planning,
        {"tools": "tools", "collector": "collector"},
    )
    graph.add_edge("tools", "merge_tool_results")
    graph.add_edge("merge_tool_results", "collector")
    graph.add_edge("collector", "analyst_a")
    graph.add_edge("collector", "analyst_b")
    graph.add_edge("analyst_a", "qa")
    graph.add_edge("analyst_b", "qa")
    graph.add_edge("qa", "quality_gate")
    graph.add_conditional_edges("quality_gate", _route_after_quality_gate)
    graph.add_edge("writer", END)

    return graph.compile(checkpointer=checkpointer or _SHORT_TERM_MEMORY)


# ── 流程编排与降级 ──────────────────────────────────────────────────────

def load_fallback() -> list[AgentNodeOutput]:
    """读取预先验证的演示数据作为最终降级结果。"""
    if not FALLBACK_FILE.exists():
        logger.warning("Fallback file not found: %s", FALLBACK_FILE)
        return []
    try:
        data = json.loads(FALLBACK_FILE.read_text(encoding="utf-8"))
        outputs = [AgentNodeOutput(**item) for item in data]
        logger.info("Loaded %d fallback outputs", len(outputs))
        return outputs
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("Failed to load fallback: %s", e)
        return []


_OUTPUT_STATE_KEYS = (
    "collector_output",
    "analyst_a_output",
    "analyst_b_output",
    "qa_output",
    "writer_output",
)


def _outputs_from_graph_state(state: _PipelineGraphState) -> list[AgentNodeOutput]:
    """按 DAG 拓扑顺序从图状态重建智能体输出。"""
    outputs: list[AgentNodeOutput] = []
    for key in _OUTPUT_STATE_KEYS:
        output = _state_to_node(state.get(key))
        if output is not None:
            outputs.append(output)
    return outputs


async def _execute_pipeline_graph(
    track: str,
    threat_target: dict[str, object],
    competitors: list[str],
    cache_data: dict,
    *,
    enable_agent_tools: bool,
    max_rework_rounds: int,
) -> tuple[_PipelineGraphState, list[AgentNodeOutput]]:
    """集中处理构图、记忆注入、线程隔离、执行和输出重建。"""
    app = build_pipeline_dag()
    initial_trace = [
        event
        for item in cache_data.values() if isinstance(item, dict)
        for event in ((item.get("metadata") or {}).get("acquisition_trace", [])
                      if isinstance(item.get("metadata"), dict) else [])
        if isinstance(event, dict)
    ]
    initial_state: _PipelineGraphState = {
        "track": track,
        "threat_target": threat_target,
        "competitors": competitors,
        "cache_data": cache_data,
        "agent_tools_enabled": enable_agent_tools,
        "long_term_memory": _LONG_TERM_MEMORY.recall(track, threat_target),
        "messages": [],
        "collector_output": None,
        "analyst_a_output": None,
        "analyst_b_output": None,
        "qa_output": None,
        "writer_output": None,
        "rework_round": 0,
        "max_rework_rounds": max(0, max_rework_rounds),
        "rework_feedback": "",
        "quality_gate_decision": {},
        "quality_history": [],
        "evidence_gaps": build_evidence_gaps(cache_data, track),
        "processed_tool_call_ids": [],
        "acquisition_ledger": {"query_fingerprints": []},
        "acquisition_trace": initial_trace,
        "error": None,
    }
    final_state = await app.ainvoke(
        initial_state,
        config={"configurable": {"thread_id": f"pipeline-{uuid4()}"}},
    )
    error = final_state.get("error")
    if error:
        logger.warning("图执行状态包含错误：%s", error)
    return final_state, _outputs_from_graph_state(final_state)


async def run_pipeline(
    track: str = "AI代码助手",
    max_rework_rounds: int = 1,
) -> list[AgentNodeOutput]:
    """通过 LangGraph 运行完整五智能体流程。

    The graph handles parallel fan-out (collector -> both analysts) and
    fan-in (both analysts -> QA) automatically. Returns all AgentNodeOutputs
    or falls back to demo data on unrecoverable failure.
    """
    logger.info("Pipeline starting (LangGraph DAG). Track: %s", track)

    cache_data = load_cache()
    cache_data = {
        company: stamp_evidence_identities(item) if isinstance(item, dict) else item
        for company, item in cache_data.items()
    }
    competitors = list(cache_data.keys())
    if not competitors:
        competitors = ["UnknownCompetitor"]
    threat_target = _default_threat_target(track)
    logger.info("Competitors: %s (%d companies in cache)", competitors, len(cache_data))

    final_state, results = await _execute_pipeline_graph(
        track,
        threat_target,
        competitors,
        cache_data,
        enable_agent_tools=True,
        max_rework_rounds=max_rework_rounds,
    )

    if not results:
        logger.warning("No agent outputs in final state — falling back to demo data")
        return load_fallback()

    collector = _state_to_node(final_state.get("collector_output"))
    if collector and collector.status == AgentStatus.ERROR:
        logger.warning("Collector failed — falling back to demo data")
        return load_fallback()

    logger.info("Pipeline complete. %d agents finished.", len(results))
    _LONG_TERM_MEMORY.remember(track, threat_target, results)
    return results


async def run_pipeline_custom(
    track: str,
    user_data: list[dict],
    threat_target: dict[str, object] | None = None,
    enable_agent_tools: bool = False,
    max_rework_rounds: int = 1,
) -> list[AgentNodeOutput]:
    """使用用户提供的竞品数据运行流程。

    user_data is a list of competitor objects matching the CompetitorCache schema.
    """
    logger.info("Pipeline starting (custom). Track: %s, %d competitors from user",
                track, len(user_data))

    cache_data = build_cache_from_user_data(user_data, track)
    cache_data = {
        company: stamp_evidence_identities(item) if isinstance(item, dict) else item
        for company, item in cache_data.items()
    }
    competitors = list(cache_data.keys())
    threat_target = threat_target or _default_threat_target(track)
    logger.info("Competitors: %s", competitors)

    final_state, results = await _execute_pipeline_graph(
        track,
        threat_target,
        competitors,
        cache_data,
        enable_agent_tools=enable_agent_tools,
        max_rework_rounds=max_rework_rounds,
    )

    if not results:
        logger.warning("Custom pipeline produced no agent outputs")
        return []

    collector = _state_to_node(final_state.get("collector_output"))
    if collector and collector.status == AgentStatus.ERROR:
        logger.warning("Collector failed in custom pipeline")
        return [collector] if collector else []

    logger.info("Custom pipeline complete. %d agents finished.", len(results))
    _LONG_TERM_MEMORY.remember(track, threat_target, results)
    return results


def reset_state():
    """为新任务清空 WebSocket 内存展示状态。"""
    _reset_ws_state()
