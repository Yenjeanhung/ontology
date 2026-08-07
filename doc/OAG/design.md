# 智能体（OAG）设计方案

> 状态：**待确认** —— 确认本方案后进入开发。
> 作者：Claude　　日期：2026-08-07
> 关联代码：`backend/services/rag_service.py`、`backend/providers/graph_store/__init__.py`、`backend/services/ontology_service.py`、`front/src/components/QueryView.vue`、`front/src/App.vue`

---

## 0. 一句话目标

新增「**智能体**」菜单，基于 **OAG（Ontology-Augmented Generation，本体增强生成）** 回答用户问题；现有「问答」菜单保持纯向量 RAG 不变。两者并存，定位互补。

---

## 1. 背景与目标

### 1.1 现状：数据齐了，但「读」没用上

知识库、本体、图谱三层都已建成，但**只有写入侧用到了本体与图谱，读取侧（查询）完全没用**：

```
写入侧（ingest）✅ 已沉淀
  文件 → 分块 → [按本体约束抽取实体/关系] → KuzuDB 图谱
                                     ↘ ChromaDB 向量
  本体在这里起作用：约束抽取 prompt（graph_extraction_service.py:_build_constrained_system_prompt）

读取侧（query）❌ 缺口
  问题 → 向量 top-K(k=50) → 拼 prompt → LLM
  见 rag_service.py:36-95，全程不碰图谱、不碰本体
```

图谱里的 `MENTIONS`、`RELATES` 边、`ontology_id`、`properties` 字段，今天都是「只写不读」的死数据。

### 1.2 目标

把写入侧沉淀的结构化知识（实体、关系、本体 schema）接进读取侧的检索与生成，做成一个「智能体」入口，回答比纯向量 RAG 更准、更全、更可解释。

### 1.3 「问答」与「智能体」定位区分

| 维度 | 问答（QueryView，保持不变） | 智能体（AgentView，新建） |
|------|----------------------------|---------------------------|
| 检索方式 | 单一向量召回 | 向量召回 + 图谱召回 + 子图事实，融合重排 |
| 用到本体 | 否 | 是（KB 绑定的本体 schema 参与理解/约束） |
| 用到图谱 | 否 | 是（实体、关系、属性） |
| 回答依据 | 文本分片 | 文本分片 + 结构化图谱事实 |
| 过程透明度 | 仅展示来源分片 | 展示「识别实体 → 子图事实 → 检索路径 → 回答」推理链 |
| 适用问题 | 事实查阅、片段定位 | 关系型、跨片段、多跳、属性筛选类问题更强 |
| 接口 | `POST /api/query`（不变） | `POST /api/agent/query`（新增） |

> 设计原则：**问答菜单一行代码不改**，智能体完全独立新增，避免回归风险。

---

## 2. OAG 核心思想：三个融合点

OAG 在本系统里落地为三个「融合点」，按性价比与难度递增：

1. **检索融合（Retrieval Fusion）**
   向量召回 + 图谱召回（按实体 `MENTIONS` 反查分片 + 邻居实体）→ RRF 融合重排。
   解决「语义不相似但实体相关」的片段被向量漏掉的问题。

2. **上下文融合（Context Fusion）**
   把命中实体的**属性 + 1 跳关系三元组**序列化为「图谱事实」注入 prompt。
   解决「一个事实散落在多个分片、或根本没成段」导致 LLM 拼不出来的问题。

3. **理解融合（Understanding Fusion）**
   用本体 schema 做查询分类/改写；结构化、多跳问题走 Text2Cypher 直查图谱。
   解决向量 RAG 天然做不好的结构化问题。

**v1 范围（本次确认后开发）**：融合点 ① + ②，并做「推理过程可视化」让智能体可解释。
**v2（后续）**：融合点 ③ Text2Cypher + 真正的多步 agent 循环。

> 理由：①② 复用现有 `MENTIONS`/`RELATES` 边，改动集中、收益最大、风险最低；③ 需要可靠的 schema→Cypher 生成与安全沙箱，单独立项更稳。

---

## 3. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│                        前端 (Vue 3)                           │
│   问答(QueryView) ──POST /api/query────► RAGService（不变）    │
│   智能体(AgentView) ──POST /api/agent/query──► OAGService(新) │
└──────────────────────────────────────────────────────────────┘
                                  │ SSE 流式
┌─────────────────────────────────▼─────────────────────────────┐
│                      FastAPI 路由层                            │
│   routers/query.py（不变）   routers/agent.py（新增）          │
└─────────────────────────────────┬─────────────────────────────┘
                                  │
┌─────────────────────────────────▼─────────────────────────────┐
│                    OAGService（新增，核心）                    │
│  1. 加载本体 schema（OntologyService.get_kb_extraction_…）     │
│  2. 向量召回（复用 create_vector_store）                        │
│  3. 实体链接（从命中分片反查 MENTIONS + 词面匹配）              │
│  4. 图谱召回（chunks_mentioning_entities + 1跳子图）           │
│  5. RRF 融合重排                                                │
│  6. 上下文组装（图谱事实 + 来源分片）                          │
│  7. LLM 流式生成（复用 create_llm）                            │
└─────────────────────────────────┬─────────────────────────────┘
                                  │ 复用，不改
        ┌──────────────┬──────────┴──────────┬───────────────┐
        ▼              ▼                     ▼               ▼
  providers/embedding  providers/vector_store  providers/llm  providers/graph_store
  (bge-small-zh)       (ChromaDB)              (OpenAI兼容)    (KuzuDB，新增检索方法)
```

**与现有 RAG 的关系**：OAGService **不继承/不改动** RAGService，而是**复用同一套 providers**（embedding / vector_store / llm / graph_store）。这样向量召回、嵌入、LLM 调用逻辑与 RAG 一致，仅在 graph_store 上新增几个只读检索方法。

---

## 4. 后端设计

### 4.1 OAG 查询流水线

一次智能体查询的 7 个步骤（`backend/services/oag_service.py`，新增）：

```
用户问题 q
  │
  1. schema = OntologyService.get_kb_extraction_constraints(db, kb_id)   # 本体（可能为 None）
  │
  2. vec_docs = vector_store.similarity_search_with_score(q, k=VEC_K)     # 向量召回（复用）
  │     └─ 过滤 score >= SIMILARITY_THRESHOLD
  │
  3. seed_entities = 实体链接(q, vec_docs)                                # 见 4.2
  │
  4. graph_chunks, subgraph = 图谱召回(seed_entities)                     # 见 4.3、4.4
  │     ├─ graph_chunks: 提到 seed_entities 的其它分片（补向量漏召）
  │     └─ subgraph: seed_entities 的属性 + 1跳关系三元组（图谱事实）
  │
  5. fused = RRF_fusion(vec_docs, graph_chunks) → top-N                  # 见 4.5
  │
  6. prompt = 组装(schema, subgraph, fused)                              # 见 4.6
  │
  7. 流式 yield: entities → subgraph → chunks(fused) → tokens → [DONE]   # SSE，见 4.7
```

### 4.2 实体链接（核心难点）

**目标**：从问题 q 识别出图谱里真实存在的实体，作为图谱召回的种子。

**v1 方案（推荐，零额外索引、纯只读）——「从命中分片反查 + 词面匹配」双通道：**

```
通道 A：词面匹配（快、精确）
  取该 KB 下所有 Entity.name（SQLite entities 表，已存在）
  命中 = name 作为子串出现在 q 中
  → 候选实体集 A

通道 B：向量分片反查（覆盖同义/指代）
  从第 2 步向量召回的 vec_docs 里，取出它们 MENTIONS 的实体
  MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) WHERE c.id IN $vec_chunk_ids
  → 候选实体集 B（按在分片中的出现频次排序）

seed_entities = (A ∪ B) 去重，取 top-M（默认 M=8）
```

- 通道 A 解决「问题里直接带了实体名」的情况（最常见）。
- 通道 B 解决「问题用代词/同义指代，但相关分片已被向量召回」的情况。
- 两者都失败 → `seed_entities` 为空 → 图谱召回为空，**自动降级为纯向量 RAG**（与问答菜单等价），保证不比现状差。

> **v2 增强（不在本次范围）**：为每个 KB 维护「实体名向量索引」（Chroma 单独 collection 或内存索引），支持「语义近邻」实体链接，覆盖 A/B 都漏的情况。

### 4.3 图谱召回（新增 graph_store 只读方法）

在 `GraphStoreAdapter` 抽象基类与 Kuzu/Neo4j 实现里新增三个**只读**方法（不触碰写入路径）：

```python
class GraphStoreAdapter(ABC):
    # ... 现有抽象方法 ...

    @abstractmethod
    def entities_mentioned_by_chunks(self, kb_id: str, chunk_ids: list[str]) -> list[dict]:
        """分片 → 它们 MENTION 的实体（带出现计数）。"""

    @abstractmethod
    def chunks_mentioning_entities(self, kb_id: str, entity_ids: list[str], limit: int) -> list[dict]:
        """实体 → 提到它们的分片（含 file_id/file_name/chunk_index/content）。"""

    @abstractmethod
    def entity_neighborhood(self, kb_id: str, entity_ids: list[str], hops: int = 1, limit: int = 40) -> dict:
        """实体的 1 跳邻居 + 这些实体的属性/类型，返回 {entities, relations}。"""
```

对应 Kuzu Cypher（示例）：

```cypher
-- entities_mentioned_by_chunks
MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
WHERE c.kb_id = $kb_id AND c.id IN $chunk_ids
RETURN e.id AS entity_id, e.name AS name, e.entity_type AS entity_type,
       e.description AS description, e.properties AS properties,
       count(c) AS mention_count
ORDER BY mention_count DESC

-- chunks_mentioning_entities
MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
WHERE c.kb_id = $kb_id AND e.id IN $entity_ids
RETURN DISTINCT c.id AS chunk_id, c.file_id AS file_id, c.chunk_index AS chunk_index,
       c.content AS content
LIMIT $limit

-- entity_neighborhood（1 跳）
MATCH (e:Entity) WHERE e.kb_id = $kb_id AND e.id IN $entity_ids
OPTIONAL MATCH (e)-[r:RELATES]-(n:Entity)
RETURN e, r.relation_type AS relation_type, r.relation_id AS relation_id, n
```

> 这些方法天然适配 Neo4j（同抽象），保持 provider 可插拔设计。

### 4.4 子图事实（上下文融合）

对 `seed_entities` 调 `entity_neighborhood`，序列化成结构化文本块，例如：

```
【图谱事实】
- 实体[任正非]（类型:人物）属性: {职位:CEO, 国籍:中国}
  ─ 任职于 → [华为]（组织）
  ─ 创立   → [华为]（组织）
- 实体[华为]（类型:组织）属性: {总部:深圳, 行业:通信}
  ─ 总部位于 → [深圳]（地点）
```

这部分是纯向量 RAG 拿不到的「跨分片聚合事实」。

### 4.5 融合重排（RRF）

向量候选与图谱候选各是一个有序列表，用 **Reciprocal Rank Fusion** 合并，无需额外模型：

```python
def rrf_fuse(rank_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, chunk_id in enumerate(ranks):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])
```

- 输入两路：向量召回排序、图谱召回排序（按 mention_count）。
- 输出 top-N（默认 N=12）作为最终来源分片。
- 每个分片保留**来源标记**（`vector` / `graph` / `both`），供前端展示检索路径。

### 4.6 上下文组装 & Prompt

```
OAG_SYSTEM_PROMPT = (
    "你是基于知识库与知识图谱的智能体。回答必须依据【参考资料】与【图谱事实】。"
    "图谱事实是结构化、可信的关系与属性，优先采信；若与参考资料冲突，请指出。"
    "引用参考资料标注[来源N]，引用图谱事实标注[事实]。"
    "资料与事实都没有时，如实说明，不要编造。"
)

USER_TEMPLATE = """
【图谱事实】
{subgraph_facts}

【参考资料】
{context_with_sources}

用户问题：{question}
"""
```

- `context_with_sources`：复用 RAGService 的 `[来源N]\n{text}` 编号格式。
- `subgraph_facts`：4.4 的结构化文本；无实体命中时为「（无）」。

### 4.7 SSE 协议扩展

在现有 `data: {type, ...}` 协议上新增事件类型，让前端能渲染推理过程：

| 事件 type | 负载 | 说明 |
|-----------|------|------|
| `entities` | `{entities: [{id,name,type,score,source}]}` | 识别到的种子实体 |
| `subgraph` | `{facts: str, entities:[...], relations:[...]}` | 图谱事实（结构化 + 文本） |
| `chunks` | `{chunks: [{..., retrieval:'vector'|'graph'|'both'}]}` | 融合后的来源分片 |
| `token` | `{content}` | 流式回答 token |
| `[DONE]` | — | 结束 |

> `entities`/`subgraph`/`chunks` 在生成前一次性下发；`token` 流式下发。与 QueryView 的 `chunks`/`token` 兼容（AgentView 多处理两种类型即可）。

### 4.8 新增/修改文件清单（后端）

| 文件 | 动作 | 说明 |
|------|------|------|
| `backend/services/oag_service.py` | **新增** | OAGService：流水线 7 步 + SSE 生成器 |
| `backend/routers/agent.py` | **新增** | `POST /api/agent/query`，SSE StreamingResponse |
| `backend/providers/graph_store/__init__.py` | **修改** | 抽象基类 + Kuzu/Neo4j 各加 3 个只读检索方法 |
| `backend/schemas.py` | **修改** | 新增 `AgentQueryRequest(query, kb_id)` |
| `backend/server.py` | **修改** | 注册 agent 路由 |
| `backend/config.py` | **修改** | 新增配置项（见 4.9） |

> 不改动：`rag_service.py`、`query.py`、`ontology_service.py`（只读取其 `get_kb_extraction_constraints`）。

### 4.9 新增配置项（config.py）

```python
# OAG 智能体
OAG_VEC_K: int = 50            # 向量召回数（与 RAG 一致起步）
OAG_TOP_N: int = 12            # 融合后最终来源分片数
OAG_SEED_ENTITY_LIMIT: int = 8 # 种子实体上限
OAG_GRAPH_CHUNK_LIMIT: int = 12# 图谱召回分片上限
OAG_RRF_K: int = 60            # RRF 常数
OAG_NEIGHBOR_HOPS: int = 1     # 子图跳数
OAG_ENABLED: bool = True       # 总开关
```

---

## 5. 前端设计

### 5.1 菜单与路由

**路由**（`front/src/router/index.js`）新增一条，紧邻 query：

```js
import AgentView from '../components/AgentView.vue'
// ...
{ path: '/agent', name: 'agent', component: AgentView, meta: { keepAlive: true } },
```

**菜单**（`front/src/App.vue` 的 `menuItems`）在「问答」后插入：

```js
{ to: '/query', key: 'query', label: '问答', exact: false, hint: '问答' },
{ to: '/agent', key: 'agent', label: '智能体', exact: false, hint: '本体增强问答' },  // 新增
{ to: '/graph', key: 'graph', label: '图谱', exact: false, hint: '图谱' },
```

并在 `App.vue` 模板的图标分支里为 `item.key === 'agent'` 加一个图标（机器人/火花样式 SVG，与现有风格一致）。

> 菜单顺序建议：本体 / 实体 / 文件 / 知识库 / **问答 / 智能体** / 图谱 / 向量 —— 把两个问答入口并排放，便于对比。

### 5.2 AgentView 页面结构（`front/src/components/AgentView.vue`，新增）

复用 QueryView 的视觉与交互骨架（KB 选择器、输入框、来源面板、markdown 渲染、引用联动、PDF 预览），**新增「推理过程」面板**：

```
┌─ 选择知识库 ▼ ─────────────────────────────────────────────┐
├─ [输入问题...]                                   [搜索]   │
├───────────────────────────────────────────────────────────┤
│ ▍推理过程（可折叠）                                         │
│   识别实体: [任正非] [华为] [深圳]  （chip，点击→实体详情） │
│   图谱事实:                                                │
│     • 任正非 ─任职于→ 华为                                  │
│     • 华为 ─总部位于→ 深圳                                  │
│   检索路径: 向量 8 · 图谱 4 · 交集 2                        │
├──────────────────────────────┬────────────────────────────┤
│  回答（markdown，[来源N]/[事实] 引用）│  来源 · N           │
│                                     │  [1] file_a 92% 向量  │
│                                     │  [2] file_b ——  图谱  │
│                                     │  ...                 │
└──────────────────────────────┴────────────────────────────┘
```

- 「来源」每条带检索来源标记（向量/图谱/交集），与 4.7 的 `chunks[].retrieval` 对应。
- 「识别实体」chip 点击跳 `/entities/:entityId`（复用现有实体详情页）。
- 其余（引用联动、双击预览、思考过程 `<think>`）直接沿用 QueryView 实现。

### 5.3 API 封装（`front/src/api/index.js`）

新增 `queryAgentStream`，与 `queryRagStream` 同构，多处理两类事件：

```js
export async function queryAgentStream(kbId, query, { onEntities, onSubgraph, onChunks, onToken }) {
  // POST /api/agent/query，SSE 解析同 queryRagStream
  // data.type === 'entities'  → onEntities(data.entities)
  // data.type === 'subgraph'  → onSubgraph(data)
  // data.type === 'chunks'    → onChunks(data.chunks)
  // data.type === 'token'     → onToken(data.content)
}
```

### 5.4 新增/修改文件清单（前端）

| 文件 | 动作 | 说明 |
|------|------|------|
| `front/src/components/AgentView.vue` | **新增** | 智能体页面（基于 QueryView 扩展推理面板） |
| `front/src/api/index.js` | **修改** | 新增 `queryAgentStream` |
| `front/src/router/index.js` | **修改** | 新增 `/agent` 路由 |
| `front/src/App.vue` | **修改** | menuItems 加「智能体」+ 图标 |

> 不改动：`QueryView.vue`、其余组件。

---

## 6. 数据流（时序）

```
前端 AgentView
   │ POST /api/agent/query {query, kb_id}   (SSE)
   ▼
routers/agent.py → OAGService.query_stream(kb_id, query)
   │
   ├─ 1. OntologyService.get_kb_extraction_constraints(db, kb_id)   [本体 schema，可能 None]
   ├─ 2. vector_store.similarity_search_with_score(q, k=VEC_K)      [向量召回]
   ├─ 3. 实体链接：词面匹配(SQLite) ∪ 分片反查(graph_store)          [种子实体]
   ├─ 4. graph_store.entities_mentioned_by_chunks / chunks_mentioning_entities / entity_neighborhood
   ├─ 5. RRF 融合 → top-N
   ├─ 6. 组装 prompt（图谱事实 + 来源分片）
   ├─ 7. yield entities → subgraph → chunks
   └─ llm.astream(messages) → yield token ... → [DONE]
```

---

## 7. 分阶段实施计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| **Phase 0**（建议先做，半天） | 建 20~50 条问答评测集（事实型/关系型/多跳型），记录纯 RAG 基线命中率 | `doc/OAG/eval.md` + 基线分数 |
| **Phase 1（v1，本次）** | 检索融合 + 上下文融合 + 推理过程可视化，上线「智能体」菜单 | 本方案全部内容 |
| **Phase 2（后续）** | 理解融合：本体引导的查询分类 + Text2Cypher 直查图谱 + 多步 agent 循环 | 独立设计文档 |

> Phase 0 的意义：没有基线，OAG 做完无法证明「比问答强」。强烈建议先做。

---

## 8. 关键决策点（需你确认）

1. **v1 范围**：确认 v1 = 检索融合 + 上下文融合 + 推理可视化，**不含** Text2Cypher / 多步 agent 循环（留 v2）。✅/❌
2. **实体链接策略**：v1 采用「词面匹配 + 向量分片反查」双通道（零额外索引），v2 再加实体名向量索引。✅/❌
3. **Phase 0 评测基线**：是否在 v1 开发前先花半天建评测集？推荐「是」。✅/❌
4. **降级策略**：KB 未绑定本体 / 实体链接为空时，智能体自动降级为纯向量 RAG（行为等同问答），不报错。✅/❌
5. **菜单命名与位置**：「智能体」放在「问答」与「图谱」之间。✅/❌

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| 实体链接漏召 → 图谱融合无效果 | 双通道 + 自动降级为纯向量，最差不弱于问答 |
| 图谱事实注入使 prompt 过长 | 子图事实按 token 预算裁剪（默认上限 ~800 token）；分片走 top-N |
| 图谱噪声（错误抽取的关系）污染回答 | Prompt 明确「事实优先但冲突时指出」；facts 标注来源实体可追溯 |
| KB 未抽取图谱（只有向量） | 检测图谱为空 → 降级纯向量，UI 提示「该库暂无图谱，已用向量模式」 |
| Kuzu 单写事务锁 | v1 全部为只读查询，无写入，不触碰 `_kuzu_write_lock` |

---

## 10. 验收标准（v1）

1. 「智能体」菜单出现，选择已绑定本体且已建图谱的 KB，提问能返回回答。
2. 回答区能看到「推理过程」：识别实体、图谱事实、检索路径。
3. 来源分片带「向量/图谱」来源标记，引用联动可用。
4. 选未绑定本体 / 无图谱的 KB 时，自动降级为向量模式，不报错，回答与「问答」菜单一致。
5. （若有 Phase 0）关系型/多跳型问题命中率 **≥ 纯向量 RAG 基线**。
6. 「问答」菜单行为完全不变（回归零风险）。

---

## 附：与现有代码的对接点速查

| 需要 | 来自 | 位置 |
|------|------|------|
| 本体 schema | `OntologyService.get_kb_extraction_constraints` | ontology_service.py:977 |
| 向量召回 | `create_vector_store` + `similarity_search_with_score` | providers/vector_store、rag_service.py:48 |
| 嵌入 | `create_embeddings` | providers/embedding |
| LLM 流式 | `create_llm` + `astream` | providers/llm、rag_service.py:145 |
| 图谱遍历 | `GraphStoreAdapter`（新增只读方法） | providers/graph_store/__init__.py:64 |
| 实体名（词面匹配） | `Entity` 表 | models.py |
| 实体详情页跳转 | `/entities/:entityId` 路由 | router/index.js:28 |
