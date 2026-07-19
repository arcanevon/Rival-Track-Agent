# RivalTrackAgent 多智能体系统源码索引

RivalTrackAgent 是一个基于 LangGraph 的多智能体竞品分析系统。本文档只说明多智能体组件在代码中的实现位置。

## 1. 五个 Agent

Agent 的执行函数统一位于 [`pipeline/nodes.py`](pipeline/nodes.py)，角色提示词位于 [`agents/prompts.py`](agents/prompts.py)。

| Agent | 职责 | 执行函数 | 提示词 |
| --- | --- | --- | --- |
| 采集 Agent | 抽取、过滤和组织 O/B/C/L 证据，记录证据缺口 | `run_collector()` | `COLLECTOR_SYSTEM` / `COLLECTOR_USER` |
| 能力持久性分析 Agent | 使用 VRIO 分析竞品能力的价值性、稀缺性、难模仿性和组织承接能力 | `run_analyst_a()` | `ANALYST_A_SYSTEM` / `ANALYST_A_USER` |
| 市场动态与用户替代分析 Agent | 使用 SWOT 分析优势、劣势、机会、威胁及用户替代路径 | `run_analyst_b()` | `ANALYST_B_SYSTEM` / `ANALYST_B_USER` |
| 质检 Agent | 汇合 VRIO 和 SWOT 结果，追溯分歧证据，执行 Reflection 并生成正式威胁矩阵 | `run_qa()` | `QA_SYSTEM` / `QA_REFLECTION_SYSTEM` |
| 撰写 Agent | 将 QA 正式结果转换为完整报告和行动建议 | `run_writer()` | `WRITER_SYSTEM` / `WRITER_USER` |

## 2. LangGraph 协作流程

图的共享状态、节点包装、条件边和运行入口都在 [`pipeline/dag.py`](pipeline/dag.py)中实现。

```text
START
  → 工具规划器 → ToolNode → Observation 合并
  → Collector
  → Analyst A (VRIO) ─┐
                         ├→ QA + Reflection → Quality Gate
  → Analyst B (SWOT) ─┘                         ├→ 返回采集
                                                    ├→ 返回双分析
                                                    └→ Writer → END
```

关键代码：

- `_PipelineGraphState`：LangGraph 共享状态定义。
- `_lg_collector()`、`_lg_analyst_a()`、`_lg_analyst_b()`、`_lg_qa()`、`_lg_writer()`：五个 Agent 的图节点。
- `build_pipeline_dag()`：注册节点、并行分支和条件边。
- `_route_after_quality_gate()`：根据证据或分析问题选择返工路径。
- `run_pipeline()` / `run_pipeline_custom()`：流水线运行入口。

`Quality Gate`、工具规划器、`ToolNode` 和 Observation 合并节点属于控制或工具节点，不是额外的业务 Agent。

## 3. Agent 输入输出契约

统一输出模型 `AgentNodeOutput` 定义在 [`models/output.py`](models/output.py)，契约校验实现在 [`models/contracts.py`](models/contracts.py)。

主要字段：

- `dependencies`：上游 Agent 依赖。
- `evidence` / `evidence_gaps`：已验收证据和待补采缺口。
- `threat_scores`：分析 Agent 的候选矩阵或 QA 的正式矩阵。
- `method_findings`：“方法准则—证据—推理—不确定性—影响维度”记录。
- `disagreements`：QA 记录的 VRIO/SWOT 分歧与裁决。
- `threat_assessment`：QA 生成的正式竞品威胁结论。
- `report_sections` / `response_actions`：Writer 生成的报告章节和行动建议。

## 4. 推理与规划

### ReAct

实现位于 [`pipeline/dag.py`](pipeline/dag.py)：

- `_lg_tool_planner()`：读取证据缺口并产生 Thought 与 Tool Action。
- `ToolNode(COLLECTOR_TOOLS)`：执行工具调用。
- `_lg_merge_tool_results()`：将 `ToolMessage` Observation 写回证据缓存。
- Quality Gate 可将证据缺口再次路由回工具规划节点。

### Reflection

实现位于 [`pipeline/nodes.py`](pipeline/nodes.py) 的 `run_qa()`：

- 先使用 `QA_SYSTEM` 生成 QA 初稿。
- 再使用 `QA_REFLECTION_SYSTEM` 复审竞品覆盖、四维矩阵、证据、方法推导和分歧。
- 反思结果需再次通过数据契约，否则保留已验证的初稿。

## 5. 记忆机制

| 记忆 | 实现 | 代码位置 |
| --- | --- | --- |
| 短期记忆 | LangGraph `MemorySaver`，按 `thread_id` 保存当前图状态、Agent 输出、工具消息和返工历史 | [`pipeline/dag.py`](pipeline/dag.py) 中的 `_SHORT_TERM_MEMORY` |
| 长期记忆 | `LongTermMemoryStore`，使用 JSON 持久化历史结论和待验证线索 | [`memory/store.py`](memory/store.py) |

报告快照、稳定证据 ID 和人工复审历史由 [`memory/evidence_workspace.py`](memory/evidence_workspace.py) 保存。

## 6. Agent 工具

三个 LangChain `@tool` 在 [`agents/tools.py`](agents/tools.py) 中定义，并通过 `COLLECTOR_TOOLS` 注册给 `ToolNode`：

| 工具 | 作用 |
| --- | --- |
| `search_competitor_evidence` | 通用搜索，发现官网、权威媒体、行业基准和前瞻信号 |
| `search_community_evidence` | 社区搜索，发现用户反馈和替代信号 |
| `read_evidence_page` | 读取网页正文，包括动态网页降级读取 |

搜索、抓取、平台适配、实体匹配和证据质量评估的底层实现位于 [`intake/`](intake/) 目录。

## 7. 其他相关组件

| 组件 | 代码位置 |
| --- | --- |
| Quality Gate 指标、路由和返工决策 | [`pipeline/quality_gate.py`](pipeline/quality_gate.py) |
| 证据覆盖与方法推导覆盖计算 | [`pipeline/coverage.py`](pipeline/coverage.py) |
| 模型调用、JSON 解析、重试和错误分类 | [`client/deepseek.py`](client/deepseek.py) |
| 行业路由与分析视角 | [`config/router.py`](config/router.py)、[`agents/analysis_lenses.py`](agents/analysis_lenses.py) |
| HTTP 入口与分析任务启动 | [`main.py`](main.py) |
| WebSocket Agent 状态广播 | [`server/ws.py`](server/ws.py) |
