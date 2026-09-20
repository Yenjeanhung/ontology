# OpenTelemetry 接口链路追踪方案

> 目标范围：RAG 全流程 + 多智能体协作链路 + 慢接口定位（一期）
> 状态：**一期已实现**（2026-09-20，冒烟验证通过：埋点落库 / 聚合榜 / span 树 / 并行节点瀑布 / FastAPI 自动埋点 / 排除路径全过）
> 关联文档：`doc/监控/00-监控模块设计.md`（系统监控模块，本方案菜单与其同级）

---

## 1. 背景与目标

### 1.1 现状

系统已有一套可观测能力，但都是「平铺日志」，回答不了"**一次请求内部，每一步各花了多久**"：

| 层 | 现状 | 位置 | 缺口 |
|----|------|------|------|
| HTTP 入口 | 访问日志中间件（method/path/status/耗时，一行一请求） | `middleware/access_log.py` | 只有接口总耗时，看不到内部 |
| 服务层方法 | AOP 式方法级日志（入参摘要/耗时/慢调用告警） | `core/tracing.py` | 日志散落，无结构化父子关系，无法还原调用树 |
| LLM/Agent 轨迹 | LangSmith 云端 trace（`LANGSMITH_TRACING` 开关） | `config.py`、`scripts/langsmith/` | 依赖外部 SaaS，看内部步骤要去别家平台，且无耗时聚合视图 |
| 离线评测 | perf_counter 手动计时 + CSV | `scripts/rag_eval/` | 离线专用，与线上无关 |

典型排查场景今天的做法：接口慢 → 翻访问日志找记录 → 再去翻 `trace` 日志人工对时间戳拼出各步耗时。**没有一处能直接看到"这条调用 = 改写 320ms + 向量 45ms + BM25 12ms + 重排 890ms + 生成 2.1s"**。

### 1.2 目标（一期）

1. **接口维度**：每个接口的调用列表（时间/方法/路径/状态/**总耗时**），支持按耗时排序、按路径过滤、慢调用标红——慢接口一眼可见。
2. **调用维度**：点开任意一次调用，看到**内部每一步的耗时瀑布图**（span 树），RAG 链路的改写/召回/RRF/重排/生成各步耗时、属性（kb_id、命中 chunk 数、token 用量）一目了然。
3. **聚合维度**：按接口聚合的 stats（调用数/avg/p95/max/错误数），默认按 p95 倒序 = 慢接口榜。
4. **多智能体协作链路**：`MultiAgentEngine`（LangGraph 流水线）**每个节点一个 span**——并行角色节点在瀑布图中呈现为同一父下的并列横条，扇出/汇合一目了然，能直接回答"这次协作慢在哪个 agent"。
5. **标准化埋点**：用 OpenTelemetry API/SDK 埋点（行业标准），后端存储**自托管轻量实现**（SQLite，复用现有后端），同时保留 OTLP 导出开关——将来接 Jaeger/Grafana 云平台只需改一行配置，**埋点代码零改动**。

### 1.3 非目标（一期不做）

- Metrics 时序 + 告警闭环（成功率/token 告警）→ 二期；
- OAG 智能体（单 agent 三路检索）全步骤细分 → 一期只埋根 span，二期细化；multi_agent 协作链路的**节点级**埋点纳入一期（见 §3.3）；
- 服务层 AOP 全量织入 span → 二期（避免一次铺开过大）；
- LLM 内容级调试 → 已由 LangSmith 覆盖，不重复建设。

---

## 2. 选型决策

### 2.1 为什么用 OpenTelemetry

| 对比项 | OTel | 继续堆日志 | LangSmith |
|--------|------|-----------|-----------|
| 结构化调用树 | ✅ span 父子关系原生 | ❌ 人工拼 | ✅ |
| 部署成本 | ✅ SDK 级，api 包零依赖 | ✅ | 依赖外部 SaaS，数据出域 |
| 行业通用性 | ✅ 标准，可随时导出 Jaeger/Tempo/Grafana | ❌ | 厂商绑定 |
| LLM 内容调试 | ❌（不管内容） | ❌ | ✅ |
| 开销 | <1%（异步批量上报） | ~0 | 网络出域 |

结论：**OTel 管"系统为什么慢/哪步慢"，LangSmith 管"LLM 为什么这么答"**，分工互补，不替代。

### 2.2 与 LangSmith 的边界（面试必问，先说清）

**LangSmith 看不到 OTel 的埋点，OTel 也收不到 LangSmith 的轨迹——两条互不相通的管道：**

| | LangSmith | OTel（本方案） |
|---|---|---|
| 采集机制 | langchain-core **callback 回调**（`LANGSMITH_TRACING=true` 激活），只覆盖走 LangChain/LangGraph runnable 生态的执行 | 显式 `start_as_current_span` + instrumentation，走 OTel SDK 的 Processor/Exporter 管道 |
| 看得到什么 | LLM 调用、Chain、LangGraph 节点、LangChain Tool | 任何代码位置，包括不走 LangChain 的裸逻辑（向量库/BM25 段、SQLite 存储） |
| 数据去向 | LangSmith 云端 | 自托管 SQLite |

因此本方案的 `rag.retrieve`、`agent.multi.node` 等 span 不会出现在 LangSmith；同一请求两边各有一份轨迹，**互不同步**（不共享 trace_id，只能按时间/接口人工对照）。两个边界情况：① OpenLLMetry 等桥接件可把 LangChain 轨迹**导给** OTel（方向是"LangChain→OTel"，不是"LangSmith 看到 OTel"）；② LangSmith 官方支持 OTLP 接入端点，但需主动把 exporter 指向 LangSmith（数据出域），与自托管定位不符。

### 2.3 关键决策：trace 存储自托管（SQLite），不强制 Jaeger

- 本项目是**单体应用**，无跨服务传播诉求，Jaeger/Tempo 的分布式能力用不上；
- 监控页面要**自研瀑布图**（用户需求），数据在自己库里，前端查询 API 即可，无需嵌第三方 UI；
- 数据不出内网（LangSmith 已是云端，trace 运维数据留本地）；
- **OTel 的价值在埋点标准化，不在存储**：自定义 `SpanExporter` 是 OTel 官方扩展点，一期写 SQLite，将来切 OTLP exporter 到 Jaeger 只改配置。

### 2.4 与 core/tracing.py 的关系

`@traced` AOP 日志**保留不动**（本地调试看日志仍最快）。一期只对 **RAG 链路手动埋 span**（步骤少而固定，精准可控）；二期可让 `@traced` 内部同时开 span，一套装饰器两份产出。

---

## 3. Trace 模型（对齐真实代码）

### 3.1 RAG 问答链路（`POST /api/query` → `RAGService.query_stream`）

```
POST /api/query                          ← FastAPI 自动埋点（http span，含 status）
└─ rag.query_stream                      ← 手动：整个 SSE 流生命周期（流结束才闭合）
   ├─ rag.expand_queries                 ← 查询改写（_expand_queries，LLM 多查询扩展）
   ├─ rag.retrieve                       ← _hybrid_retrieve 整体
   │  ├─ rag.vector_search               ← 向量召回（多查询 embedding + 检索）
   │  ├─ rag.bm25_search                 ← BM25 召回
   │  ├─ rag.rrf_fuse                    ← RRF 融合
   │  └─ rag.rerank                      ← Rerank 精排（RERANK_ENABLED 关闭时不产生）
   ├─ rag.build_context                  ← 拼上下文
   └─ rag.llm_generate                   ← LLM 生成（astream 全程；token 用量记属性）
```

### 3.2 OAG 智能体链路（`POST /api/agent/query`，一期只埋根）

```
POST /api/agent/query
└─ oag.query_stream                      ← 一期只记总耗时；二期细分三路召回/图谱/生成
```

### 3.3 多智能体协作链路（`MultiAgentEngine`，一期节点级埋点）

引擎是 LangGraph 流水线：planner → 并行角色节点（retriever/worker/graph_agent/data_agent/tool_agent）→ critic → synthesizer（黑板模式 + reducer）。埋点利用 `emit` 已有的节点事件时机，在 `_build()` 建图时**统一包一层节点 span**：

```
POST /api/agent/collaborate（多智能体入口，以实际路由为准）
└─ agent.multi.run                       ← 包住 ainvoke 全程（总耗时）
   ├─ agent.multi.node[planner]          ← 建图时包装节点函数
   ├─ agent.multi.node[retriever]        ┐ 并行角色节点：
   ├─ agent.multi.node[graph_agent]      │ 瀑布图中呈同一父下并列横条，
   ├─ agent.multi.node[data_agent]       ┘ 扇出/汇合一目了然
   ├─ agent.multi.node[critic]
   └─ agent.multi.node[synthesizer]
```

与现有 `emit(node_start/node_done + elapsed_ms)` 事件的关系：SSE 前端回放事件**保留不动**（实时进度用），OTel span 是事后持久化的调用树（排查用），一次包装两份产出。

### 3.4 Span 属性设计

| 属性 | 记在哪 | 说明 |
|------|--------|------|
| `http.method` / `http.route` / `http.status_code` | http span | FastAPI instrumentor 自动 |
| `rag.kb_id` / `rag.kb_name` | rag 根 span | 定位是哪个库慢 |
| `rag.query`（截断 200 字） | rag 根 span | 复现请求 |
| `rag.rewrite_count` | expand_queries | 改写出的查询数 |
| `rag.vector_hits` / `rag.bm25_hits` / `rag.merged` | retrieve | 各路命中数 |
| `rag.rerank_enabled` / `rag.final_chunks` | retrieve | 开关与最终条数 |
| `llm.model` / `llm.input_tokens` / `llm.output_tokens` | llm_generate | token 用量（从 usage 取，拿不到记 0） |
| `agent.node` / `agent.role` | agent.multi.node | 节点名与角色（并行节点的区分标签） |
| `agent.team` / `agent.scenario` | agent.multi.run | 团队名 / 场景（多智能体配置定位） |
| `error` | 任意 | 异常 span 标 ERROR + 异常类型 |

SSE 说明：`query_stream` 是 async generator，span 在**生成器结束（或客户端断开）时闭合**，总耗时=整段流式输出时长；http span 由 instrumentor 包住 StreamingResponse 全程，两者耗时基本一致，前端以业务根 span（rag.query_stream）为准展示分步。

---

## 4. 后端设计

### 4.1 新增/修改文件

```
backend/
├── core/
│   └── otel.py                    # 新增：tracer 初始化、SQLite SpanExporter、便捷工具
├── services/
│   ├── rag_service.py             # 修改：RAG 链路各步骤埋 span（改动小，见 4.3）
│   └── multi_agent/engine.py      # 修改：建图时包装节点函数，产出节点级 span（见 4.3）
├── routers/
│   └── monitor.py                 # 修改：追加 /api/monitor/traces* 三个只读接口
└── server.py                      # 修改：lifespan 里 init_otel() + FastAPIInstrumentor
```

依赖（requirements.txt 追加）：`opentelemetry-api`、`opentelemetry-sdk`、`opentelemetry-instrumentation-fastapi`（均纯 Python、轻量）。

### 4.2 core/otel.py 核心设计

```python
init_otel(app)
├─ TracerProvider + Resource(service.name="ontology-backend")
├─ BatchSpanProcessor(SQLiteSpanExporter)     # 异步批量，队列满则丢弃（不阻塞请求）
├─ 可选：BatchSpanProcessor(OTLPSpanExporter) # OTEL_OTLP_ENABLED=true 时追加
└─ FastAPIInstrumentor.instrument_app(app)    # HTTP 自动埋点（排除 /api/monitor/stream 等长连接）
```

- **SQLiteSpanExporter**：实现 `export(spans)`，批量 upsert 到 `otel_spans` 表（自己包一层 sqlite3 同步写，跑在线程池；BatchSpanProcessor 本身已异步化，天然不卡请求路径）；
- **便捷工具**：`@async_span(name)` 装饰器 / `span(name, **attrs)` 上下文管理器，业务侧一行埋点；
- **采样**：内部系统 QPS 低，默认**全量采样**（`OTEL_SAMPLE_RATIO=1.0` 可调）。

### 4.3 rag_service.py 埋点位置（对齐现有代码）

| 步骤 | 位置 | span 名 |
|------|------|---------|
| 改写 | `_expand_queries()` 函数体 | `rag.expand_queries` |
| 向量召回 | `_hybrid_retrieve` 内向量段 | `rag.vector_search` |
| BM25 召回 | `_hybrid_retrieve` 内 BM25 段 | `rag.bm25_search` |
| RRF 融合 | rank_lists 合并段 | `rag.rrf_fuse` |
| Rerank | rerank 调用段（`RERANK_ENABLED` 分支） | `rag.rerank` |
| 整体检索 | `_hybrid_retrieve` 整体 | `rag.retrieve` |
| 上下文 | `_build_context` | `rag.build_context` |
| 生成 | `query`/`query_stream` 的 llm 调用段 | `rag.llm_generate` |
| 根 | `query`/`query_stream` 入口 | `rag.query` / `rag.query_stream` |
| **多 agent 根** | `MultiAgentEngine.run()` 整体 | `agent.multi.run` |
| **多 agent 节点** | `_build()` 建图时包装每个节点函数（非侵入节点实现） | `agent.multi.node`（属性带 node/role） |
| **OAG 根** | `OAGService.query_stream` 入口（一期） | `oag.query_stream` |

改动方式：在方法体首尾包 span（约 15 处单行包裹），**不改任何业务逻辑**；OTEL_ENABLED=false 时工具函数退化为空操作（零开销直通）。

### 4.4 存储：otel_spans 表

```sql
CREATE TABLE otel_spans (
    trace_id     TEXT NOT NULL,      -- 32 hex
    span_id      TEXT NOT NULL,      -- 16 hex
    parent_id    TEXT,               -- 根 span 为空
    name         TEXT NOT NULL,      -- rag.rerank / POST /api/query ...
    span_kind    TEXT,               -- server / internal
    status       TEXT,               -- ok / error
    start_ms     INTEGER NOT NULL,   -- epoch ms
    duration_ms  INTEGER NOT NULL,
    attributes   TEXT,               -- JSON
    PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX idx_spans_start ON otel_spans(start_ms DESC);
CREATE INDEX idx_spans_name_dur ON otel_spans(name, duration_ms DESC);
```

- **保留策略**：默认保留 7 天（`OTEL_RETENTION_DAYS`），服务启动时 + 每日定时清理；
- **容量估算**：一次 RAG 调用 ≈ 9 个 span ≈ 2KB；100 次/天 ≈ 200KB/天，SQLite 毫无压力。

### 4.5 API（挂在现有 `/api/monitor` 下）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/monitor/traces/stats` | 按接口聚合：`{path, method, calls, avg_ms, p95_ms, max_ms, errors, last_at}`，支持 `?window=24h`，默认按 p95 倒序（慢接口榜） |
| GET | `/api/monitor/traces` | 调用明细列表：`{trace_id, method, path, status, duration_ms, start_ms, slow}`，支持 `?path=&min_ms=&start=&end=&limit=&offset=`（前端分页） |
| GET | `/api/monitor/traces/{trace_id}` | 单次调用详情：按 start_ms 排序的 span 数组（含 parent_id/attributes），前端还原瀑布 |

只读查询，不复用 SSE 推送（追踪是事后查询，手动刷新即可）。

### 4.6 配置项（config.py + .env）

| 配置 | 默认 | 说明 |
|------|------|------|
| `OTEL_ENABLED` | `true` | 总开关，false 时全部埋点退化为空操作 |
| `OTEL_SAMPLE_RATIO` | `1.0` | 采样比例 |
| `OTEL_RETENTION_DAYS` | `7` | span 保留天数 |
| `OTEL_OTLP_ENABLED` | `false` | 是否同时导出 OTLP（接 Jaeger 时打开） |
| `OTEL_OTLP_ENDPOINT` | `""` | OTLP gRPC 端点 |
| `OTEL_SLOW_MS` | `3000` | 慢调用阈值（前端标红用） |

---

## 5. 前端设计

### 5.1 入口（已确认：独立二级菜单）

侧边栏「系统配置」分组下新增**独立二级菜单「接口追踪」**，与「系统监控」平级：

- 路由：`/trace` → 新页面 `TracePage.vue`（不嵌入 MonitorPage，避免 Tab 结构改造牵连已有页面）；
- 菜单：`App.vue` 侧边栏「系统配置」分组注册「接口追踪」项；
- 页面布局沿用「聚合榜（上）+ 调用明细（下）+ 点击展开瀑布抽屉」三段结构。

### 5.2 页面结构

```
┌──────────────────────────────────────────────────────────────┐
│ 系统监控   [系统状态] [接口追踪]                                │
├──────────────────────────────────────────────────────────────┤
│ Tab1 聚合榜（慢接口 Top）                                      │
│ 路径            方法   调用数  avg     p95      max     错误    │
│ /api/query      POST   42     1.8s    3.4s ▲   5.1s    1      │
│ /api/agent/query POST  18     4.2s    6.8s ▲   9.3s    0      │
├──────────────────────────────────────────────────────────────┤
│ Tab2 调用明细（默认近 24h，可按路径/耗时/时间过滤）               │
│ 时间        接口            状态   总耗时                       │
│ 15:32:01   POST /api/query  200    3.4s 🔴（>3s 标红）          │
│ 15:31:47   POST /api/query  200    1.2s                        │
│   └─ 点击行 → 右侧抽屉瀑布图                                    │
├──────────────────────────────────────────────────────────────┤
│ 抽屉：瀑布图                                                   │
│ POST /api/query                    ████████████████ 3.41s     │
│ └ rag.query_stream                 ████████████████ 3.40s     │
│    ├ rag.expand_queries            ██ 320ms  9%               │
│    ├ rag.retrieve                  ████ 947ms 28%             │
│    │  ├ rag.vector_search          █ 45ms                     │
│    │  ├ rag.bm25_search            ▍12ms                      │
│    │  ├ rag.rrf_fuse               ▏3ms                       │
│    │  └ rag.rerank                 ███ 887ms 26%              │
│    ├ rag.build_context             ▏2ms                       │
│    └ rag.llm_generate              █████████ 2.1s 62%         │
│ 属性面板：kb_id / chunk 数 / token 用量 / 错误信息               │
└──────────────────────────────────────────────────────────────┘
```

### 5.3 实现要点

- 瀑布图**纯 CSS 实现**（每行一个横条：`left = (start-根start)/根总时长`、`width = duration/根总时长`），不引入第三方图表库；
- 耗时格式化（<1s 显示 ms，≥1s 显示 s，保留 1 位）；横条颜色按 span 类型区分（HTTP/检索/重排/LLM 四色）；
- error span 红色横条 + 展开属性看异常信息；
- 新增 `front/src/api/traces.js` 封装三个只读接口；页面组件：`front/src/views/TracePage.vue`（独立页面，聚合榜 + 明细列表）+ `front/src/components/trace/TraceWaterfall.vue`（瀑布图 + 属性面板抽屉）。

---

## 6. 开发任务拆解

### 阶段一：后端（核心）

1. requirements.txt 加 3 个 opentelemetry 包；
2. `core/otel.py`：init_otel、SQLiteSpanExporter、async_span 工具、清理任务；
3. `server.py`：lifespan 调 init_otel + FastAPIInstrumentor（排除 monitor SSE 长连接）；
4. `rag_service.py`：按 4.3 埋点（不改业务逻辑，开关退化空操作）；
5. `multi_agent/engine.py`：`run()` 根 span + `_build()` 建图包装节点函数（节点名/角色进属性）；
6. `OAGService.query_stream` 入口根 span；
7. `routers/monitor.py`：traces 三个只读接口；config.py 加 6 个配置项。

### 阶段二：前端

1. `api/traces.js`；
2. `TraceWaterfall.vue`（瀑布图 + 属性面板抽屉，支持并行节点并列横条）；
3. `TracePage.vue`（聚合榜 + 明细列表 + 过滤）；
4. `router/index.js` 注册 `/trace`；`App.vue` 「系统配置」分组下新增「接口追踪」二级菜单。

### 阶段三：联调打磨

1. 压测验证：开关关闭时零开销（对比埋点前后接口耗时）；
2. SSE 流式调用的 span 闭合正确性（客户端中途断开）；
3. 慢接口场景演练（临时把 rerank 阈值调大制造慢例）；
4. 保留清理任务验证。

---

## 7. 验收标准

- [ ] 侧边栏「系统配置 → 接口追踪」二级菜单可进入独立页面，展示接口聚合榜，默认按 p95 倒序，能识别慢接口；
- [ ] 调用明细中总耗时超过 `OTEL_SLOW_MS` 的调用标红；
- [ ] 点击任一调用，抽屉展示完整瀑布图：RAG 链路能看到 改写/向量/BM25/RRF/重排/生成 各步耗时与占比；
- [ ] 多智能体协作调用：瀑布图能看到 planner/并行角色节点/critic/synthesizer 各节点耗时，**并行节点呈同一父下并列横条**；
- [ ] 瀑布图属性面板可见 kb_id、命中 chunk 数、LLM token 用量；
- [ ] `RERANK_ENABLED=false` 时瀑布图无 rerank span；开关关闭时无异常；
- [ ] `OTEL_ENABLED=false` 时所有接口行为与现状一致，无额外耗时；
- [ ] trace 详情页数据与 access_log 同请求耗时吻合（±5%）；
- [ ] span 自动清理生效（改小 RETENTION_DAYS 验证）。

---

## 8. 二期规划（本期不做，埋点已预留）

1. **Metrics + 告警**：LLM token counter、接口成功率/延迟直方图（OTel Metrics API 同一套 SDK）→ 导出 Prometheus + Grafana 看板 + Alertmanager 告警（成功率阈值/P95 阈值/token 突增）；
2. **OAG 智能体细分**：三路召回（向量/BM25/图谱）、实体链接、图谱事实注入各埋 span；
3. **服务层 AOP 织入 span**：`@traced` 升级为同时产出 OTel span；
4. **OTLP 双写**：打开 `OTEL_OTLP_ENABLED` 接 Jaeger/Grafana，做分布式 trace 原型（面试演示价值）。

---

## 9. 设计确认记录

| # | 问题 | 确认结果 |
|---|------|----------|
| 1 | 页面入口 | **独立二级菜单**「接口追踪」（系统配置分组下，与系统监控平级，路由 `/trace`） |
| 2 | 多智能体链路埋点深度 | **节点级纳入一期**（`agent.multi.run` + 每节点一个 span）；OAG 单 agent 链路一期只埋根，细分二期 |
| 3 | 采样与保留 | 全量采样 + 保留 7 天（内部流量低，够用可调） |
| 4 | token 用量采集 | OpenAI 兼容接口多数返回 usage，能取就记属性（不额外调用量） |
| 5 | 慢调用阈值 | 默认 3000ms（RAG 含 LLM 生成，别用普通 Web 的 500ms 口径） |
| 6 | LangSmith 关系 | 互不相通的两条管道（见 §2.2）：LangSmith 管 LLM 内容级调试，OTel 管系统分步耗时；不重复建设、不互相同步 |

## 10. 实施记录（一期落地）

| 项 | 结果 |
|---|---|
| 依赖 | opentelemetry-api/sdk（环境已有 1.44.0）+ instrumentation-fastapi 新装；requirements.txt 已登记 |
| 存储 | `backend/data/otel_traces.db`（WAL），`otel_spans` 表；OTEL_ENABLED=false 时不产生数据 |
| 埋点 | rag：query / query_stream 根 + rewrite / recall.vector / recall.bm25 / rrf_fuse / rerank / llm_generate；multi_agent：run 根 + 建图包装节点（并行角色呈并列横条）；oag：query_stream 根（薄壳包裹，实现零改动） |
| 前端 | `/config/trace` 独立二级菜单「接口追踪」（系统配置分组），TracePage.vue + TraceWaterfall.vue（树形折叠 + 瀑布横条 + 属性面板抽屉 + 慢/错标色） |
| **实施坑** | ① OTel Python 的 `SpanKind`/`StatusCode` 是普通 Enum 且 **SpanKind 从 0 编号**（INTERNAL=0/SERVER=1/CLIENT=2，与 OTLP proto 规范不同）——`int()` 直转会抛 TypeError，须取 `.value`；② async generator 里 `async with` 包全函数体是安全模式：客户端断开时 athrow(GeneratorExit) 会正常退出 span；③ span 属性必须在 `async with` 块内设置（退出后 is_recording()=False 静默丢弃） |
| 验证 | 冒烟脚本（已删）：手动 SERVER span + MultiAgentEngine 真实 run() → 落库 10 span、并行节点同父；TestClient 验证 instrument_app：SERVER kind=1、业务 span 挂其下、`/api/monitor/stream` 排除路径零 span |
