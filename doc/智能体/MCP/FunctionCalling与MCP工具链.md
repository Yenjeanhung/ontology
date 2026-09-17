# Function Calling / Tool Use 与 MCP 工具链设计

> 对标岗位要求：「设计 Function Calling / Tool Use 机制，支持内部/外部工具接入
> （API、DB、MCP 等），打通多业务场景应用」与「熟悉 MCP（Model Context Protocol）
> 等 Agent 间协议」。本文对应实现：`services/tool_registry.py`、
> `services/agent_loop.py`、`services/multi_agent/scenarios/universal.py`（ToolAgent）、
> `scripts/mcp_server.py`。

## 1. 总体架构

```
                    ┌────────────────────────────────────────────┐
                    │  ToolAgent（多智能体并行节点之一）           │
                    │  LLM 自主决策：调什么工具/带什么参数/调几次   │
                    └───────────────┬────────────────────────────┘
                                    │ run_tool_loop（services/agent_loop.py）
              bind_tools(规范) → ainvoke → tool_calls → 执行 → ToolMessage 回填
                                    │ 直到最终回答 / 轮数上限强制收尾
                    ┌───────────────┴────────────────────────────┐
                    │  ToolRegistry（services/tool_registry.py）  │
                    │  register / specs(OpenAI tools) / call      │
                    └──────┬─────────────────────────┬───────────┘
              内置工具（平台能力工具化）        MCP 动态接入（外部工具）
              ├─ kb_search   知识库向量检索    ├─ stdio（子进程）
              ├─ graph_search 图谱实体+关系    └─ streamable_http（远程服务）
              └─ data_query  台账统计+明细       inputSchema 即 JSON Schema，直接透传

  反向输出：scripts/mcp_server.py 把同一批内置 handler 暴露为 MCP 服务器，
  外部 Agent 宿主（Claude Desktop / Cursor / 任意 MCP Client）可接入消费。
```

## 2. 三层职责

### 2.1 工具层 `tool_registry.py`
- **Tool**：name / description / parameters（JSON Schema）/ handler / source；
  `spec()` 产出 OpenAI tools 数组元素，`LangChain.bind_tools` 原生可用。
- **执行容错**：arguments 兼容 JSON 字符串；参数错（TypeError）把「期望 schema」
  回传给 LLM 供其纠正；工具内部异常包装为 `{"error": ...}` 文本回填（循环继续，
  LLM 可换参数重试）；单次执行超时 `TOOL_TIMEOUT`；结果超长截断
  （`RESULT_MAX_CHARS`）。**错误永远是数据而非异常**——这是工具循环健壮性的关键。
- **返回二元组** `(text, raw)`：text 给 LLM（截断后），raw 保留结构化数据
  给前端素材卡渲染。
- **MCP 接入**：`PlatformTools` 异步上下文统一管理连接生命周期；
  逐服务器降级（连不上记 `mcp_status`，不阻断其余工具）；远程工具命名加
  `mcp_<server>_` 前缀防撞名；`mcp` SDK 为可选依赖，未安装时自动跳过。

### 2.2 循环层 `agent_loop.py`
标准 OpenAI tool-calling 循环，与 LLM 提供商无关（任何实现 `bind_tools/ainvoke`
的 LangChain ChatModel）：

```
messages=[System, Human]
loop ≤ TOOL_LOOP_MAX_ITERATIONS 轮:
    AIMessage = bind_tools 后的 llm.ainvoke(messages)
    有 tool_calls → 逐个执行工具 → ToolMessage(tool_call_id) 回填 → 继续
    无 tool_calls → 最终回答，结束
超出上限 → 摘除工具强制收尾一轮（循环必然终止）
```

- 每次 ToolCallRecord 记录：名称/参数/结果文本/结构化 raw/耗时/成败；
- `on_event` 回调外抛 `tool_call` / `tool_result` / `tool_degrade` 过程事件
  （多智能体引擎转发 SSE，前端可观测工具执行轨迹）；
- 降级兜底：LLM 不支持 `bind_tools` → 单轮直接回答（`degraded=True`），
  不中断上层流水线；LLM 未配置（RuntimeError）原样上抛由场景处理。

### 2.3 场景层 ToolAgent（`universal.py`）
与既有取证成员（Retriever/DataAgent/GraphAgent）的本质差异：**后三者的取数
路径由代码写死，ToolAgent 由 LLM 在循环中自主规划工具调用**。

- 编制：能力智能体 `tool_agent`（`OPTIONAL_AGENTS`，前端组队器自动出现）；
  并行角色（`PARALLEL_ROLES`），与其它取证节点同 superstep 执行；
- 产出：每次工具调用 → 一张工具事实卡（`grade=tool_result`，前端「工具产出」
  tab），进入黑板 facts，与图谱/台账事实同一编号体系，供 Synthesizer 以
  `[事实N]` 引用、Critic 统计素材充分性；
- node_done 摘要：`Function Calling 3 轮：kb_search×2、data_query×1，
  产出 3 张工具事实卡，MCP 接入 1/1 个外部服务器`。

## 3. 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `TOOL_LOOP_MAX_ITERATIONS` | 4 | 单节点工具循环 LLM 轮数上限 |
| `TOOL_TIMEOUT` | 25s | 内置工具单次执行超时 |
| `MCP_TOOL_TIMEOUT` | 30s | MCP 远程工具单次调用超时 |
| `MCP_SERVERS` | "" | MCP 服务器清单（JSON 数组），空=不接入 |

```bash
# 平台作为 MCP 客户端接入外部工具（stdio 子进程 / streamable_http 均支持）
MCP_SERVERS=[{"name":"fs","transport":"stdio","command":"npx",
              "args":["-y","@modelcontextprotocol/server-filesystem","D:/data"]}]

# 平台作为 MCP 服务器被外部消费（与内置工具同一批 handler，一处能力两种消费）
cd backend && python scripts/mcp_server.py
```

## 4. 验证

```bash
# 单元测试（FakeLLM 脚本化，不依赖真实模型/数据库）
cd backend && python -m pytest test/test_tool_loop.py -q   # 12 passed

# 工具链自检（内置工具规范 + MCP 连接状态，不跑流水线）
curl http://127.0.0.1:8000/api/agent/multi/tools
```

## 5. 对标说明

- **ReAct / OpenAI tool-calling 范式**：本实现即原生 Function Calling
  （结构化 tool_calls + JSON Schema 参数约束），非「提示词里写个函数名让模型
  猜」的伪工具调用；参数错误把期望 schema 回传模型自我纠正，对应工业界
  tool-use eval 中常见的 arg-recovery 能力。
- **MCP**：同时落地客户端（外部工具接入平台）与服务器（平台工具对外暴露）
  两侧，传输支持 stdio 与 streamable_http——Agent 间/Agent-工具间协议的
  完整实践，而不仅是「听说过 MCP」。
