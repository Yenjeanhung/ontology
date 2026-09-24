# 主流 Agent 框架对比与选型（面向本体驱动知识平台）

> 版本：v1.0（2026-09-24）
> 范围：DeerFlow、DeepAgents、AgentScope 及其他主流框架横向对比，以及"哪些能用到本系统、怎么用"的落地方案。
> 关联：`doc/智能体/多智能体/多智能体场景.md`、`多智能体交互.md`、`多智能体DataAgent·本体驱动NL2SQL.md`

---

## 1. 结论速览（TL;DR）

| 结论 | 对象 | 说明 |
|---|---|---|
| **不整体替换** | DeerFlow 2.0 / AgentScope 1.0 / CrewAI / AutoGen | 自研引擎已深度绑定 SSE 事件契约、场景适配器、OTel 链路，整体迁移成本远大于收益 |
| **推荐引入（P1）** | **DeepAgents** | 与本环境 langchain 1.4 / langgraph 1.2 同源兼容，作为"复杂任务深度模式"第二执行路径，复用现有工具与 MCP |
| **零成本启用（P0）** | LangGraph Checkpointer | `langgraph-checkpoint-sqlite` 已装未用，给现有引擎加断点恢复/回放，不引入任何新框架 |
| **思想借鉴（P2 观察）** | DeerFlow 的 supervisor/技能分层、AgentScope 的"单智能体优先" | 本系统 router chat 直答、skill_service 已暗合同理念，抄作业而非引依赖 |

一句话：**正确姿势不是"换引擎"，而是"在 LangGraph 生态内做增量"**——本系统引擎本来就是 LangGraph 上的 supervisor 式编排，缺的能力（断点恢复、长任务上下文管理、子智能体隔离）都能以库的形式补齐。

---

## 2. 评估背景：本系统的智能体现状

### 2.1 自研引擎架构（`backend/services/multi_agent/engine.py`）

```
用户任务 ──路由──▶ 澄清判定(clarify_check) ──▶ Planner(LLM 分解 1~4 子任务)
                                                        │
                    ┌───────────────┬───────────────┬───┴───────────┐
                    ▼               ▼               ▼               ▼
               Retriever       DataAgent       GraphAgent      ToolAgent/Custom   ← 同 superstep 并行
              (全库向量)      (本体NL2SQL)     (jieba图谱)     (Function Calling/MCP)
                    └───────┬───────┴───────┬───────┴───────────────┘
                            ▼               ▼
                    Critic(可选) ──▶ Synthesizer（汇总结论）
                                                        │
                              SSE 事件流（node_start/node_done/evidence/
                              fact/clarify/token）──▶ 前端聊天式回放
```

技术要点：
- **LangGraph StateGraph 黑板模式**：共享 state + reducer（`merge_evidence`/`merge_facts`），并行角色写同一黑板；
- **场景适配器**：引擎业务无关，业务下沉 `scenarios/universal.py`，新场景 = 新适配器、引擎与前端零改动；
- **事件外抛**：`engine.emit()` 推队列转 SSE，前端按节点回放（含 elapsed_ms）；
- **OTel 双产出**：`_wrap` 同时埋 span（事后调用树）与 emit 事件（实时回放）；
- **工具循环**：`agent_loop.run_tool_loop`（`TOOL_LOOP_MAX_ITERATIONS=4` 强制收尾，不支持 bind_tools 降级单轮）；
- **MCP 双向**：`tool_registry.py` 热加载注册中心 + `scripts/mcp_server.py` 对外提供三工具（stdio/streamable-http + token，已实测 mcp 2.x）；
- **周边**：任务澄清（0.6B 第三复用服务）、chat 直答（route mode=chat）、mem0 长期记忆、本体驱动 NL2SQL、技能管理（skill_service）。

### 2.2 技术栈实际版本（conda `ontology`，2026-09 实测）

| 组件 | requirements 声明 | 实际安装 | 对选型的意义 |
|---|---|---|---|
| langgraph | `>=0.2.0` | **1.2.11** | 已在 1.x 线，DeerFlow 2.0 / DeepAgents 同源 |
| langchain / core | `>=0.3.0` | **1.4.0 / 1.6.3** | DeepAgents 的 middleware 抽象可直接用 |
| langgraph-checkpoint-sqlite | 已登记（注释"预留"） | **3.1.1（未启用）** | P0 断点恢复零成本 |
| langgraph-prebuilt | — | 1.1.0 | create_react_agent 现成可用 |
| mcp | 声明锁 `<2`，环境实际 **2.x**（双版本兼容已做） | 2.x | 对外/对内均已实测 |

> 注意：requirements 下限与实际版本差距很大，引入新框架以 conda 实测为准。已知坑：全局 Python 3.12.9 的 langchain-core 1.6 与旧 langgraph 组合收集 pytest 会挂，验证一律在 conda `ontology` 环境做。

### 2.3 已有能力 vs 框架能力对照

| 能力 | 本系统现状 | 主流框架对应物 |
|---|---|---|
| 任务规划 | Planner（LLM 分解 + 15s 回落 + 1~4 子任务） | DeepAgents `write_todos`、DeerFlow supervisor |
| 多智能体编排 | StateGraph 并行 superstep + critic 可选 | DeerFlow supervisor、AutoGen 群聊、AgentScope MsgHub |
| 工具调用 | bind_tools 循环 + MCP 注册中心 | 各框架 tool 抽象（MCP 支持度不一） |
| 人机协同 | 任务澄清；无工具级审批 | DeepAgents `interrupt_on`（LangGraph interrupts） |
| 断点恢复 | ❌（checkpoint 库已装未用） | LangGraph checkpointer（通用底座） |
| 长任务上下文 | 单节点 30s 超时，无长任务 | DeepAgents 虚拟文件系统、DeerFlow 记忆/沙箱 |
| 可观测 | ✅ OTel 自托管 + 前端瀑布 | AgentScope Studio、OpenAI SDK 内置 tracing |
| 对外开放 | ✅ MCP Server（已实测） | A2A 协议（DeerFlow 2.0 主打）等 |

**判断**：本系统在编排、工具、可观测上已达到甚至超过多数框架的开箱能力；真正的缺口只有三个——**断点恢复、工具级人工审批、长任务上下文隔离**，且都能在现有生态内补齐。这是全部结论的依据。

---

## 3. 重点框架逐个解析

### 3.1 DeerFlow 2.0（字节跳动，2026-02 开源）

**定位**：基于 LangGraph 1.0 的"超级智能体运行时"（SuperAgent Harness），是 LangGraph 的上层封装而非竞争者。1.0（2025-05）是深度研究框架；2.0 重写为字节内部数千个 Agent 应用的底座，开源数月 39k+ star。

**核心机制**：Supervisor/子智能体编排（各子代理独立上下文）；沙箱 Sandbox（受控代码执行）；长期记忆（分钟~小时级任务）；可扩展技能系统（技能即插件）；A2A 协议（智能体互联）。

**与本系统关系**：生态同源（LangGraph），但整套引入 = 按它的 supervisor 模型重写编排层 + 前端契约。其价值主张（小时级长任务、沙箱执行代码、技能自主装配）与本系统"知识库问答 + 数据取证 + 图谱溯源"的短平快场景错位。**可借鉴分层思想**：subagent 上下文隔离、技能即插拔（skill_service 理念相同）。

### 3.2 DeepAgents（LangChain 官方，2025-07 发布，v0.7+）

**定位**：构建在 LangChain 1.x 构件 + LangGraph 1.x 运行时之上的 agent harness，灵感来自 Claude Code / Deep Research / Manus。四个重点框架中与本系统**技术栈重合度最高**。

**核心机制**（v0.7+）：
- **任务规划**：`write_todos`/`read_todos` 把计划写入 state（pending/in_progress/completed），v0.7 起 opt-in（传 `TodoListMiddleware`）——与本系统 Planner 的 plan 事件流高度同构；
- **虚拟文件系统**：ls/read_file/write_file/edit_file/glob/grep，后端可插拔（内存 state/磁盘/LangGraph store/沙箱），声明式文件权限——长任务中间产物落盘与上下文卸载；
- **子智能体**：内置 `task` 工具创建临时子代理——全新上下文、自主执行、单次交接只回传最终报告，重型子任务压缩为紧凑结果（Token 隔离）——正是本系统并行节点缺的"证据压缩"能力；
- **HITL**：`interrupt_on` 基于 LangGraph interrupts，工具调用前暂停审批（批准/改参/附加指导）——可自然承接 human_task_service；
- **代码执行**：沙箱 `execute` 或 QuickJS `eval`（本系统暂无此场景）。

**兼容性结论**：要求 langchain 1.x + LangGraph 1.x——conda 环境实测 **直接满足，`pip install deepagents` 即用，无需升级任何现有依赖**。

### 3.3 AgentScope 1.0（阿里通义实验室，2025-09）

**定位**：企业级开箱即用框架，覆盖开发/部署/监控全生命周期；2025-12 出 Java 版。

**核心机制**：四大抽象 **Message/Model/Memory/Tool**；**ReActAgent** 为核心（动态工具配置、并行工具调用、自动化状态管理）；**Studio** 可视化监控；哲学是"**单智能体优先**"。

**与本系统关系**：完全独立的第二技术栈（非 LangChain 生态），引入 = 双栈并存、同批能力两套抽象各养一份。Studio 与 OTel 自托管重复；"单智能体优先"与本系统 chat 直答路径互相印证——**学理念，不引依赖**。Java 版对纯 Java 企业有价值，本系统 Python 栈不受益。

### 3.4 其他主流框架简评

| 框架 | 范式一句话 | 关键特性 | 不引入的原因 |
|---|---|---|---|
| **LangGraph 1.x** | 流程图/状态机 | 可中断可恢复可 checkpoint、并行 superstep | **已在用**，是一切结论的底座 |
| **AutoGen / AG2** | 群聊（微信隐喻） | 消息传递协商、涌现分工 | 流程涌现=过程不可预期，与"人设可视化 + SSE 回放"诉求相悖 |
| **CrewAI** | 任务板（Trello 隐喻） | Role/Task/Crew 角色团队 | 抽象偏浅，复杂编排仍需回 LangGraph；本系统已有更贴合的场景适配器层 |
| **OpenAI Agents SDK** | Handoff（交接） | 20 行多 Agent、内置 tracing/guardrails | 抽象太薄，控制流能力弱于现有引擎；tracing 已有等价物 |
| **MetaGPT** | SOP 软件公司隐喻 | PM→架构师→工程师标准流程 | 强绑定软件开发场景 |
| **smolagents（HF）** | Code Agent | LLM 写 Python 代码行动，极简 | 代码执行场景不存在（NL2SQL 也是约束式 SQL） |
| **Dify / Coze 类** | 低代码平台 | 可视化编排 + 内置 RAG | 是"平台"非"框架"，引入即推翻自研平台；其 RAG 不及本体驱动深度 |

---

## 4. 横向对比总表

| 维度 | 本系统自研 | DeerFlow 2.0 | DeepAgents | AgentScope 1.0 | AutoGen/AG2 | CrewAI | OpenAI Agents SDK |
|---|---|---|---|---|---|---|---|
| 技术栈 | LangGraph 1.2 | LangGraph 1.0 | LangChain 1.x + LangGraph 1.x | 独立抽象（含 Java 版） | 独立 | 独立 | 独立（轻） |
| 编排范式 | 状态机+黑板 | SuperAgent Harness | 深度智能体（规划+文件+子代理） | ReActAgent + MsgHub | 群聊涌现 | 角色任务板 | Handoff |
| 任务规划 | Planner LLM 分解 | supervisor 规划 | write_todos（state 持久化） | ReAct thoughts | 对话协商 | Task 序列 | 无内置 |
| 上下文管理 | 单轮黑板 | 记忆+沙箱 | **虚拟文件系统+子代理隔离（最强）** | Memory 抽象 | 消息历史 | 共享记忆 | 手写 |
| 断点恢复 | ❌（库已装未用） | ✅ | ✅ | 状态管理 | 有 | 有 | 有 |
| HITL | 任务澄清（无工具审批） | 有 | **interrupt_on（工具级审批）** | 有 | 有 | 有 | guardrails |
| MCP | ✅ 注册中心+对外服务 | 技能系统 | 经 LangChain 工具生态 | tool 抽象可接 | 可接 | 可接 | 有 |
| 可观测 | ✅ OTel + 瀑布图 | 平台级 | LangSmith/LangGraph | Studio | 弱 | 弱 | 内置 |
| 长任务（小时级） | ❌ | ✅ 主打 | ✅ 中等 | 一般 | 一般 | 一般 | 一般 |
| 引入成本 | —（基线） | 高（重写编排+前端） | **低（pip 装、版本兼容）** | 高（第二技术栈） | 中 | 中 | 低但收益低 |

---

## 5. 适配性分析：逐框架对照本系统

### 5.1 四条红线（任何框架引入/替换的先决条件）

1. **SSE 事件契约不破**：前端协作页按 `node_start/node_done/evidence/fact/clarify/token` 回放，换框架 = 前端重写；
2. **场景适配器模式不破**：新业务 = 新适配器 + 引擎零改动，这是已验证的扩展点；
3. **OTel 链路覆盖不破**：SERVER span → 节点 span → 前端瀑布，是排查依赖；
4. **环境约束不破**：conda `ontology` 环境、mcp 2.x 双版本兼容、fastapi/starlette 约束、业务数据独立实例（biz_aviation.db）等既有事实不得回退。

### 5.2 逐框架判定

**DeerFlow 2.0 —— 不引入，观察名单**
- 踩红线 1/2：supervisor/subagent/技能体系要求按它的模型重组节点与事件流，前端回放、适配器、OTel 全返工。
- 重新评估触发条件（任一出现再议）：①小时级长任务（深度报告生成）；②沙箱代码执行需求；③需与外部 A2A 智能体互联。
- 现在能拿走：supervisor→subagent 的上下文隔离思想（见 P1）；技能即插拔（skill_service 已同理念）。

**DeepAgents —— 推荐 P1 引入（增量，非替换）**
- 四条红线全不踩：不替换 StateGraph，而是在同一 LangGraph 运行时上多开一种 agent 形态；版本兼容已实测（langchain 1.4.0 / langgraph 1.2.11，`pip install deepagents` 即可，不动现有依赖树）。
- 收益定位：**"复杂任务深度模式"第二执行路径**。路由层已有 chat 直答 / 精简组合 / 全量团队分层，deep agent 补上"多阶段、中间产物多、需子任务隔离取证"的一档。
- 桥接方案见 §6.2。

**AgentScope 1.0 —— 不引入**
- 踩红线 4：第二技术栈 = 双倍维护面；编排/监控/工具能力本系统均有等价实现且更贴合本体业务；Studio 与 OTel 重复。
- 重新评估触发条件：团队出现 Java 侧独立智能体需求（可单独评估 AgentScope Java，与本系统解耦）。

**LangGraph Checkpointer（非新框架）—— P0 立即启用**
- `langgraph-checkpoint-sqlite` 3.1.1 已在环境，requirements 注释本就写着"预留"。`_build()` 的 `compile()` 加 checkpointer 即得断点恢复、历史回放、time-travel 调试。零新依赖、零契约变更。

**其余（AutoGen/CrewAI/OpenAI SDK/MetaGPT/smolagents/Dify）—— 均不引入**
- 共性理由：范式与产品形态错位（群聊涌现 vs 可回放的确定性编排）、抽象深度不及现有引擎、或平台化思路与自研平台冲突（§3.4 表）。

---

## 6. 推荐方案与落地路线

### 6.1 P0（零新依赖，本周可做）：启用 Checkpointer

```python
# engine.py（示意）
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# _build() 内：
return g.compile(checkpointer=...)   # thread_id = 协作会话 chat_id
```

- thread_id 复用协作会话 ID，天然获得"按会话断点续跑/回放"；
- 补齐"任务跑到一半服务重启"的恢复能力；
- 存储沿用现有 SQLite 独立文件惯例，勿混业务库/主库/otel 库。

### 6.2 P1（推荐主线）：DeepAgents"深度模式"

**架构位置**——路由层加一档，现有路径不动：

```
router_service 分流：
  mode=chat        → chat 直答（已有）
  复杂度低/信号强  → 精简组合 2~3 节点（已有）
  深度研究型任务   → deep agent（新增）★
  常规多智能体     → 全量团队 StateGraph（已有）
```

**接入要点**（对齐四条红线）：
1. **工具复用**：现有 kb_search/graph_search/data_query（与取证节点同源取数）+ MCP 工具经 `@tool` 包装传入 `create_deep_agent(tools=[...])`；NL2SQL 走 `nl2sql_service` 同一入口（dsn 空=老链，安全语义不变）；
2. **SSE 桥接**：deep agent 的 `astream(stream_mode="messages")` 增量转成现有 emit 事件（node_start/node_done/token），前端深度模式渲染为普通消息流 + 工具调用明细，**契约不变**；
3. **子智能体绑定**：把 Retriever/DataAgent/GraphAgent 的职责定义为 DeepAgents 的 custom subagents（各绑自己的工具与人设 prompt），主 agent 经 `task` 工具派发——获得"单次交接、上下文隔离、结果压缩"，证据卡不再直接撑爆主上下文；
4. **HITL 承接**：`interrupt_on` 配在敏感工具（如写操作类 MCP 工具）上，审批动作挂现有 human_task_service；
5. **OTel**：deep agent 整体包一个 `async_span("agent.deep.run")`，与 `agent.multi.run` 平级，瀑布图同一视图；
6. **可关闭**：配置开关（如 `DEEP_AGENT_ENABLED`，默认 false），关闭时回落全量团队路径，风险可控。

**工作量估计**：新增 `services/multi_agent/deep_agent.py`（桥接层 ~200 行）+ 路由分流一个分支 + 前端一个消息形态，2~3 天量级；无依赖树变更。

### 6.3 P2（观察项）

| 项 | 触发条件 | 动作 |
|---|---|---|
| DeerFlow 深度评估 | 出现小时级长任务 / 沙箱需求 / A2A 互联 | 按 §5.2 重新选型，届时优先评估只取其 Sandbox 或 A2A 模块的可行性 |
| DeepAgents 沙箱后端 | 需要代码执行类工具 | 启用其 SandboxBackend（vs 自建容器沙箱再对比） |
| LangGraph 平台化 | 需要托管式持久化/定时任务 | langgraph-checkpoint 已就位，按需换 Postgres saver（与 asyncpg 栈一致） |

### 6.4 面试视角小结（供 `doc/interview/` 引用）

- **选型方法论**：框架对比的锚点不是功能清单，而是"范式 × 现状 × 迁移成本"——状态机（LangGraph/DeerFlow）、对话涌现（AutoGen/CrewAI 偏对话）、深度智能体（DeepAgents/Manus 类）是三条范式线；
- **为什么不换**：自研引擎的价值不在代码量而在**契约**（SSE 事件、适配器、OTel）——契约换框架即全断；
- **为什么 DeepAgents 能进**：与现栈同源（LangGraph 1.x 运行时），引入的是"harness 层"而非新编排层，且补的正是三个真实缺口（断点、审批、上下文隔离）；
- **加分点**：能说出 LangGraph 并行 superstep 与 reducer 黑板、interrupts 的 HITL 语义、checkpointer 的 thread_id 模型——这些是所有框架共通的底层概念，比背框架名更有说服力。

---

## 7. 附录：风险与已知环境约束

| 项 | 说明 |
|---|---|
| 版本声明漂移 | requirements 下限（langgraph>=0.2）与实际（1.2.11）差距大；引入 deepagents 后需回写 requirements 实测版本 |
| 全局 Python 污染 | 全局 3.12.9 langchain-core 1.6 与旧 langgraph 组合 pytest 收集挂；一切验证在 conda `ontology` |
| mcp 版本 | 环境实际 2.x（服务端/客户端已双版本兼容）；requirements 注释仍写锁 <2，与事实不符，P0 时一并修正 |
| 思考型模型时延 | deep agent 多轮工具循环叠加思考型模型可见输出前静默，SSE 桥接需复用 SYNTH_TIMEOUT 的放宽经验 |
| LLM 配置来源 | `create_llm()` 依赖 llm_configs 库表（需先 `load_active_into_settings`），deep agent 桥接层同源取 LLM，勿直连环境变量 |

