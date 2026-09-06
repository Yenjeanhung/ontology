# 基于 Neo4j GDS 的实体链接与知识补全 功能设计

> 状态：**待确认** —— 确认本方案后进入开发。
> 日期：2026-08-29
> 前置依赖：`GRAPH_STORE_PROVIDER=neo4j`（含 GDS 插件），见 4.1。
> 关联代码：`backend/services/oag_service.py`、`backend/providers/graph_store/__init__.py`、
> `backend/services/graph_extraction_service.py`、`backend/services/entity_service.py`、
> `backend/routers/graph.py`、`backend/models.py`、`front/src/components/AgentView.vue`、
> `front/src/components/ontology/SuggestionListPage.vue`

---

## 0. 一句话目标

给系统接上真正的**图计算能力**（Neo4j GDS）：把智能体（OAG）的实体链接从「双通道」升级为「双通道 + 图嵌入扩展 + PPR 过滤排序」，并新增「**知识补全**」能力——用链接预测生成缺失关系建议，进入人工审核闭环。让图谱从"抽取出来就静止的资产"变成**可计算、可自我生长的知识资产**。

---

## 1. 背景与目标

### 1.1 现状：图的结构价值完全没挖

| 现有能力 | 局限 |
|---|---|
| 实体链接双通道（词面匹配 A + 向量分片反查 B，`oag_service.py:148`） | 同义词、别名、代词指代会漏召；"华为的手机业务"只能链接到「华为」，链接不到「海思」「Mate 系列」 |
| 图谱召回排序 | 只按 `mention_count` 计数，无图结构信号；`entity_neighborhood` 固定 1 跳 |
| 图谱内容 | 只抽取不补全：文档没有明说的关系（但结构上强烈暗示）永远不会出现 |
| 图算法 | 全项目唯一的"图算法"是图谱清洗的并查集；PageRank / 社区发现 / 图嵌入 / 链接预测均为空白 |

> 对应岗位方向：图计算框架（GraphX、Plato）或 GNN 经验是明确加分项；"推理计算"是知识图谱岗位职责的一条主线。

### 1.2 目标

1. **实体链接 v2**：图嵌入近邻扩展 + Personalized PageRank 过滤，提升多跳 / 指代类问题的召回（有评测集可验证）
2. **知识补全**：GDS 结构信号 + LLM 语义判型 + 本体约束校验的三层流水线，产出「关系建议」进入人工审核闭环
3. **可插拔、零回归**：GDS 不可用时全部自动降级为现状行为；Kùzu 模式有 NetworkX 降级路径
4. 顺带沉淀 **GDS 基础层**（投影管理 + 算法封装），后续图分析 Tab、图谱清洗增强（社区预聚簇）直接复用

---

## 2. 核心思想

### 2.1 三层分工（贯穿两个功能，与抽取管线同哲学）

| 层 | 职责 | 回答的问题 | 现有系统里的对应物 |
|---|---|---|---|
| **GDS 结构信号** | 候选召回 / 排序 | "结构上**该不该**连？""谁和谁强相关？" | 新增（本方案） |
| **LLM 语义判型** | 关系定名 + 置信度 | "语义上**是什么**关系？" | 抽取 Prompt（已有） |
| **本体约束** | 合法性兜底 | "这个三元组**合不合法**？" | `constraint_set` 硬校验（已有） |

与抽取管线「Prompt 引导 + 代码兜底 = 双保险」一脉相承：**结构负责召回，语义负责定名，约束负责合法**。

### 2.2 图嵌入的正确用法（关键设计决策）

**图嵌入空间与问题文本空间不对齐**——不能拿问题的文本向量直接去查实体的图嵌入向量（这是最常见的错误设计）。因此图嵌入在本方案中的职责是**扩展与消歧**，不是直接召回：

```
问题文本 ──文本嵌入──► 向量召回分片 ──MENTIONS 反查──► 候选种子 S0（通道 A/B，现状）
                                                          │
                 图嵌入空间（GDS FastRP/node2vec）          ▼
                 S0 的 KNN 近邻 = "词面不匹配但结构强相关"的实体（通道 C，新增）
                                                          │
                 PPR 过滤 = 以 S0 为源的全图 Personalized PageRank（新增）
                 分数低于阈值的扩展实体 → 丢弃（防 KNN 引噪声）
```

### 2.3 建议闭环的复用

知识补全的审核闭环**完全复用现有两套成熟模式**：

- 「本体建议」的两段式审核（`ontology_suggestions` 表 + SuggestionListPage 审核交互）
- `agent_skill_seed_tombstones` 的防重推模式（拒绝过的建议不再推荐）

---

## 3. 总体架构

```
┌────────────────────────────────────────────────────────────────┐
│                          前端 (Vue 3)                           │
│  智能体 AgentView：推理过程面板 + 图扩展实体 chips（M1）          │
│  图谱菜单 →「知识补全」Tab：关系建议审核卡片（M2）                │
└───────────────┬────────────────────────────┬───────────────────┘
                │ SSE                        │ HTTP
┌───────────────▼──────────────┐  ┌─────────▼─────────────────────┐
│ OAGService（改）              │  │ GraphCompletionService（新）    │
│ 3. 实体链接 + 通道C/PPR       │  │ 候选生成→打分→约束→LLM判型→落库 │
│ 4. 图谱召回（PPR 加权排序）    │  │ 审核批准 → 复用关系创建链路     │
└───────┬──────────────────────┘  └────────┬──────────────────────┘
        │ 只读调用                          │
┌───────▼──────────────────────────────────▼──────────────────────┐
│            graph_store provider（扩展 GDS 方法层）                │
│   GraphStoreAdapter 基类：默认 NotImplementedError               │
│   ├─ Neo4jGraphAdapter：gds.graph.project / fastRP / knn /       │
│   │   pageRank / nodeSimilarity（本方案主体）                     │
│   └─ KuzuGraphAdapter：NetworkX node2vec 降级实现（可选，见 9.5） │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                     Neo4j + GDS 插件                              │
│   内存投影图（Entity + RELATES 无向）                              │
│   embedding 属性（FastRP 128 维，mutate 到投影图）                 │
│   审核通过的关系 → upsert_relation 双写回库                        │
└──────────────────────────────────────────────────────────────────┘
```

**与现有架构的关系**：不动 SQLite 权威存储原则；GDS 计算全部发生在 Neo4j 内存投影上（无锁、不污染库内数据）；审核通过的关系走 `EntityService.create_relation`（`entity_service.py:759`）+ `graph_store.upsert_relation` 双写，与手工建关系完全同链路。

---

## 4. M0：GDS 基础层（两功能共用）

### 4.1 环境与配置

```bash
# Neo4j 5 + GDS 插件（社区版即可：并发 GDS 任务 = 1，单机场景够用）
docker run -d --name neo4j-gds -p 7474:7474 -p 7687:7687 \
  -e NEO4J_PLUGINS='["graph-data-science"]' \
  -e NEO4J_AUTH=neo4j/<password> \
  neo4j:5
```

`backend/config.py` 新增（沿用现有命名风格）：

```python
# GDS 图计算
NEO4J_GDS_ENABLED: bool = True          # 总开关，关闭即回到现状行为
GDS_EMBEDDING_ALGO: Literal["fastrp", "node2vec"] = "fastrp"
GDS_EMBEDDING_DIM: int = 128
GDS_KNN_TOPK: int = 8                   # 实体链接：每个种子的嵌入近邻扩展数
GDS_PPR_THRESHOLD: float = 0.01         # PPR 过滤阈值（低于丢弃扩展实体）
GDS_PPR_DAMPING: float = 0.85
# 知识补全
COMPLETION_TOP_N: int = 50              # 每次运行的建议上限
COMPLETION_SCORE_THRESHOLD: float = 0.35 # 结构分阈值
COMPLETION_MIN_ENTITIES: int = 30       # 实体数低于此值的 KB 不跑
COMPLETION_LLM_TYPING: bool = True      # LLM 判型开关（关则只出结构分建议）
```

`.env.example` 同步补充上述项及 Neo4j 连接说明。

### 4.2 graph_store 新增 GDS 方法

抽象基类 `GraphStoreAdapter` 增加方法（默认 `raise NotImplementedError`），仅 `Neo4jGraphAdapter` 实现：

| 方法 | 职责 | 底层 GDS 调用 |
|---|---|---|
| `gds_available() -> bool` | 探测 GDS 是否安装可用 | `CALL gds.debug.sysInfo()` |
| `gds_ensure_projection(kb_id) -> str` | 确保 KB 的内存投影存在（幂等），返回投影名 | `gds.graph.project.cypher` |
| `gds_compute_embeddings(kb_id) -> bool` | 计算实体图嵌入（mutate 到投影图） | `gds.fastRP.mutate` / `gds.node2vec.mutate` |
| `gds_knn_neighbors(kb_id, entity_ids, top_k) -> list[dict]` | 种子实体的嵌入近邻 | `gds.knn.stream` |
| `gds_personalized_pagerank(kb_id, seed_ids) -> dict[str, float]` | 以种子为源的 PPR 分数 | `gds.pageRank.stream` + `sourceNodes` |
| `gds_candidate_pairs(kb_id, top_k) -> list[dict]` | 知识补全候选对（邻居 Jaccard） | `gds.nodeSimilarity.stream` |
| `gds_graph_stats(kb_id) -> dict` | 投影指纹（实体数/关系数），供失效判断 | `gds.graph.stream` |

投影定义（只算该 KB 的 Entity + RELATES，无向化——嵌入与社区类算法都需要无向）：

```cypher
CALL gds.graph.project.cypher('kg_<kb_id>',
  'MATCH (e:Entity {kb_id: "<kb_id>"}) RETURN id(e) AS id',
  'MATCH (a:Entity {kb_id: "<kb_id>"})-[r:RELATES]->(b:Entity)
   RETURN id(a) AS source, id(b) AS target')
```

> **注意**：cypher projection 的子句**不支持参数化**，`kb_id` 必须先通过
> `^[a-f0-9]{12}$`（uuid hex 截断格式）白名单校验后再内联，杜绝注入。

FastRP 嵌入（快、确定性强，作为默认；node2vec 作为可配置项）：

```cypher
CALL gds.fastRP.mutate('kg_<kb_id>', {
  embeddingDimension: $dim,
  mutateProperty: 'embedding'
})
```

### 4.3 投影生命周期（关键工程点）

嵌入 / PPR 不能每次查询重算，用**指纹失效**管理：

```
内存指纹表 {kb_id: hash(实体数, 关系数, max(entities.updated_at))}
```

- **惰性检查**：OAG 查询 / 补全运行前比对指纹，不一致才 `gds.graph.drop` + 重建投影 + 重算嵌入
- **主动标脏**：`graph_extraction_service` 写图完成后、实体/关系 CRUD（entity_service）成功后，使该 KB 指纹失效
- **定时兜底**：复用现有 scheduler 模块，低峰重建（可选，v1 可不做）
- 嵌入结果同时 `gds.fastRP.write` 写回库内 `Entity.graph_embedding` 属性：调试可视 + Kùzu 降级路径复用

### 4.4 Kùzu 降级路径（保住可插拔设计）

未切 Neo4j 时，图嵌入通道降级为：SQLite 导出边表 → NetworkX（+ node2vec 实现）计算 → 存 `entities.graph_embedding`（JSON）。KNN / PPR 同理由 NetworkX 提供（`networkx.pagerank(personalization=...)` 原生支持 PPR）。

> 若决定本期不实现 Kùzu 降级（见 9.5 决策点），基类默认抛 `NotImplementedError`，
> 调用方捕获后跳过通道 C，行为退回现状双通道——同样零回归。

### 4.5 GDS 可用性探测与并发控制

- 启动时 `gds_available()` 探测；失败时 `NEO4J_GDS_ENABLED` 视为 False，日志告警，监控页（现有 monitor 模块）黄标
- 社区版 GDS 并发任务数 = 1：仿照 `_kuzu_write_lock` 加模块级 `_gds_lock`，串行化投影重建类重操作；查询类（读投影）不加锁

---

## 5. M1：实体链接 v2（图嵌入扩展 + PPR 过滤排序）

### 5.1 流水线（改造 `_link_entities`，`oag_service.py:148`）

```
通道 A 词面匹配 ──┐
通道 B 向量分片反查 ─┼→ 候选种子 S0（现状逻辑一行不改）
                   │
通道 C（新增）GDS KNN 嵌入近邻扩展：
   gds_knn_neighbors(S0, top_k=GDS_KNN_TOPK)
   → 扩展集 E：source='graph_emb'，score=嵌入余弦
                   │
PPR 过滤（新增）：
   gds_personalized_pagerank(S0)
   → E 中 ppr_score < GDS_PPR_THRESHOLD 的实体丢弃
   → PPR 分数同时用于 5.2 的召回排序
                   ▼
融合排序（替换现有 205-208 行的排序键）：
   score = 0.5 × 来源分（lexical=1.0 / mention 归一化）
         + 0.5 × ppr 归一化分
   lexical 命中仍优先置前（保现状体验）
→ top OAG_SEED_ENTITY_LIMIT
```

- 整个 GDS 段包 `try/except`：任何失败返回现状双通道结果（**降级即现状**）
- 函数签名不变，`query_stream` 调用处（`oag_service.py:270`）无需改动

### 5.2 图谱召回与 RRF 排序改造（`oag_service.py:273-311`）

- `chunks_mentioning_entities` 返回的图召回分片，排序信号从 `mention_count`
  改为 `mention_count × ppr(提及实体)`（一个分片提及多个种子实体时取最大 PPR）
- `_rrf_fuse` 的图通道 rank list 按上述加权分排序（RRF 公式本身不动）
- `entity_neighborhood`（1 跳图谱事实注入）**保持不动**——事实注入要稳定，扩展只发生在召回侧

### 5.3 SSE 协议扩展

`entities` 事件（`oag_service.py:360`）负载扩展：

| 字段 | 说明 |
|---|---|
| `entities[].source` | 新增枚举值 `graph_emb`、`lexical+graph_emb` 等 |
| `entities[].score` | 融合后的最终分 |
| `expanded`（新增） | `[{id, name, type, score, seed_entity_id}]`——图嵌入扩展进来的实体及其种子来源 |

> 前端不处理 `expanded` 也不报错（向后兼容），与 OAG v1 的事件扩展方式一致。

### 5.4 前端：AgentView 推理过程面板

```
▍推理过程（可折叠）
  识别实体: [任正非](lexical) [华为](lexical+mention)          ← 现有
  图扩展:   [海思](0.87 ←华为) [Mate 系列](0.81 ←华为)        ← 新增 chips
  图谱事实 / 检索路径: 向量 8 · 图谱 4 · 交集 2                ← 现有
```

- 扩展 chips 样式与现有实体 chip 一致，加浅色角标区分来源，点击仍跳 `/entities/:id`
- 检索路径行加 `图扩展 N` 计数

### 5.5 改动文件清单（M1）

| 文件 | 动作 | 说明 |
|---|---|---|
| `backend/providers/graph_store/__init__.py` | 改 | 基类 + Neo4j 实现：KNN / PPR / 嵌入 / 投影管理 |
| `backend/services/oag_service.py` | 改 | `_link_entities` 通道 C + 融合排序；图召回 PPR 加权 |
| `backend/config.py` | 改 | GDS 配置项 |
| `front/src/components/AgentView.vue` | 改 | 推理面板渲染 `expanded` chips |
| `front/src/api/index.js` | 不动 | SSE 事件向后兼容 |

---

## 6. M2：知识补全（链接预测 + 人工审核闭环）

### 6.1 总流水线（新 `backend/services/graph_completion_service.py`）

```
① 候选对生成（GDS 结构信号，只看"未连接"对）
   v1：gds.nodeSimilarity.stream —— 邻居 Jaccard
       "连接模式相似但尚未连接"的实体对（Co-occurrence 思路）
   v2：嵌入余弦 top-N 未连接对（复用 M0 的 embedding，零额外成本）
   v3（后期可选）：gds.beta.pipeline.linkPrediction 训 MLP（见 6.7）
        │
② 结构打分
   score = 0.6 × jaccard归一 + 0.4 × 嵌入余弦
   score < COMPLETION_SCORE_THRESHOLD → 丢弃
        │
③ 本体约束过滤（复用现有 constraint_set 校验逻辑）
   (source_type, ?, target_type) 无任何合法关系类型 → 丢弃
   合法关系类型可能多个 → 交给 ④ 判型
        │
④ LLM 判型 + 置信度（批量，复用 graph_extraction_service 的 LLM 调用模式）
   输入：两实体 name/description/properties + 候选关系类型 + 共同邻居上下文
   输出：relation_type + confidence(0-1) + reason
        │
⑤ 落库 relation_suggestions（status=pending）
        │
⑥ 审核闭环
   批准 → EntityService.create_relation（entity_service.py:759）
          + graph_store.upsert_relation 双写（与手工建关系同链路）
   拒绝 → 写 tombstone，之后不再推荐该 (kb, source, target, type) 组合
```

### 6.2 数据模型（models.py + migrations.sql 新增）

```python
class RelationSuggestion(Base):
    __tablename__ = "relation_suggestions"
    id = Column(String, primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    kb_id = Column(String, nullable=False, index=True)
    source_entity_id = Column(String, nullable=False)
    target_entity_id = Column(String, nullable=False)
    suggested_relation_type = Column(String, nullable=False)
    score = Column(Float, default=0)         # GDS 结构分
    confidence = Column(Float, default=0)    # LLM 置信度
    evidence = Column(Text, default="")      # JSON：共同邻居、jaccard、嵌入余弦明细
    reason = Column(String, default="")      # LLM 判型理由（审核者看）
    status = Column(String, nullable=False)  # pending / approved / rejected
    created_at = Column(String, default=lambda: datetime.now().isoformat())
    reviewed_at = Column(String)
    reviewer = Column(String)


class RelationSuggestionTombstone(Base):
    __tablename__ = "relation_suggestion_tombstones"
    kb_id = Column(String, primary_key=True)
    source_entity_id = Column(String, primary_key=True)
    target_entity_id = Column(String, primary_key=True)
    suggested_relation_type = Column(String, primary_key=True)
    created_at = Column(String, default=lambda: datetime.now().isoformat())
```

### 6.3 LLM 判型 Prompt（要点）

```
你是知识图谱的关系判定器。给出两个实体及其上下文，从【候选关系类型】中
选择最可能成立的一个，并给出置信度与理由。
规则：
1. 只能从候选关系类型中选择；都不成立时输出 NONE
2. 依据共同邻居与实体属性推断，不要编造
3. 输出 JSON 数组，每项 {pair_id, relation_type, confidence, reason}
```

- 批量组织（一次 LLM 调用处理多个候选对），失败的对降级为"仅结构分"建议
- `COMPLETION_LLM_TYPING=False` 时跳过本步，建议卡只显示结构分（功能开关分级）

### 6.4 API 设计（沿用 `routers/graph.py` 风格）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/graph/{kb_id}/completion/run` | 触发补全任务（BackgroundTasks 异步），返回任务标识 |
| GET | `/api/graph/{kb_id}/completion/suggestions` | 建议列表，`?status=pending`，含实体名联表查询 |
| POST | `/api/graph/completion/{suggestion_id}/approve` | 批准：走关系创建双写链路 |
| POST | `/api/graph/completion/{suggestion_id}/reject` | 拒绝：写 tombstone |
| GET | `/api/graph/{kb_id}/completion/status` | 最近一次运行状态 / 统计 |

运行完成推送现有 notifications（与"本体建议"审核提醒同机制）。

### 6.5 前端：「知识图谱」菜单组新增「知识补全」子项

侧边导航「知识生产 → 知识图谱」组（App.vue:116-120）新增第三个子项，与图谱浏览、图谱清洗并列：

```
知识图谱 ▾
  ├─ 图谱浏览   /graph            （看）
  ├─ 图谱清洗   /graph-cleanup    （修）
  └─ 知识补全   /graph-completion （长）← 新增，路由注册进 router/index.js「实体管理」注释块附近
```

> 实体链接 v2（M1）**不新增菜单**——它是问答链路的一环，在智能体问答 `/agent`
> 内自动生效，价值展示就在推理过程面板（5.4）。

新页面复用 [SuggestionListPage.vue](../../front/src/components/ontology/SuggestionListPage.vue) 的审核交互骨架：

```
┌─ 知识补全（金融研报）                    [重新计算]  待审 12 ─┐
│                                                              │
│  [华为] ──任职于──► [海思]        结构分 0.82 · 置信 0.90    │
│  证据：共同邻居 [任正非][深圳]；jaccard 0.74；嵌入余弦 0.88   │
│  理由：海思为华为全资子公司，高管重叠……          [批准] [拒绝] │
│  ──────────────────────────────────────────────────────────  │
│  [张三] ──投资于──► [A公司]       结构分 0.41 · 置信 0.55    │
│  ……                                            [批准] [拒绝] │
└──────────────────────────────────────────────────────────────┘
```

- 实体名可点击跳实体详情页；批准后建议卡片置灰 / 移出待审列表

### 6.6 护栏（沿用图谱清洗的工程化风格）

1. `COMPLETION_TOP_N` 上限 + `COMPLETION_SCORE_THRESHOLD`（防建议洪水）
2. **已存在关系去重**：Cypher `NOT (a)-[:RELATES]->(b)` + SQLite relations 双查
3. tombstone 去重（拒绝过的不重推）
4. 实体数 < `COMPLETION_MIN_ENTITIES` 的 KB 直接返回"图规模不足"（小图嵌入无意义）
5. LLM 只对通过结构分与约束过滤的候选调用（控成本）
6. 运行互斥：同 KB 补全任务进行中不允许重复触发

### 6.7 后续演进（v3，本期不做）

`gds.beta.pipeline.linkPrediction`：nodeFeatures（degree 等）+ MLP 训练，
AUCPR 评估，作为打分器的可配置替换项（`COMPLETION_SCORER=heuristic|mlp`）。

---

## 7. 数据流（时序）

### 7.1 OAG 查询（M1 后）

```
AgentView POST /api/agent/query (SSE)
   │
   ├─ 1-2. 向量召回（不变）
   ├─ 3. _link_entities：
   │      A词面 ∪ B分片反查 → S0
   │      → gds_ensure_projection（指纹命中则跳过重建）
   │      → gds_knn_neighbors(S0) → 扩展集 E
   │      → gds_personalized_pagerank(S0) → 过滤 E + 排序分
   ├─ 4. 图谱召回：mention_count × ppr 加权重排
   ├─ 5. RRF 融合（不变）
   └─ yield entities(含 expanded) → subgraph → chunks → tokens
```

### 7.2 知识补全运行（M2）

```
图谱菜单 [重新计算] → POST completion/run
   → GraphCompletionService.run(kb_id)  [BackgroundTasks]
      ├─ 护栏检查（规模/互斥）
      ├─ gds_ensure_projection + gds_candidate_pairs（+ 嵌入）
      ├─ 打分 → 约束过滤 → 去重 → top-N
      ├─ LLM 判型（可选）
      └─ relation_suggestions 落库 + 通知
前端轮询/刷新 → 待审列表 → [批准] → create_relation 双写 → 完成
```

---

## 8. 分阶段实施计划

| 阶段 | 内容 | 产出 | 依赖 |
|---|---|---|---|
| **M0**（地基） | GDS 环境、graph_store GDS 方法层、投影指纹管理、探测降级 | 可用的 GDS provider 层 | 切 Neo4j 默认 |
| **M1** | 实体链接通道 C + PPR 排序 + SSE/前端扩展 + **评测对比** | 智能体召回提升（可量化） | M0 |
| **M2-v1** | 启发式候选 + 建议表 + 审核闭环（无 LLM 判型） | 知识补全上线 | M0 |
| **M2-v2** | 嵌入余弦候选 + LLM 判型 + 证据展示 | 建议质量提升 | M0 + M1 嵌入 |
| **M2-v3**（可选） | linkPrediction MLP pipeline | 打分器升级 | M2-v2 |

---

## 9. 关键决策点（需确认）

1. **默认嵌入算法**：FastRP（快、确定性强）起步，node2vec 作为配置项。✅/❌
2. **v1 候选生成**：`nodeSimilarity`（Jaccard 启发式），MLP 训练留 v3。✅/❌
3. **LLM 判型默认开启**（`COMPLETION_LLM_TYPING=True`），可配置关闭。✅/❌
4. **审核入口**：「知识图谱」菜单组第三个子项「知识补全」（`/graph-completion`，与图谱浏览/图谱清洗并列）；实体链接 v2 不加菜单，在智能体内自动生效。✅/❌
5. **Kùzu 降级路径**：本期只留接口（`NotImplementedError` + 自动跳过通道 C），NetworkX 实现放后续；还是本期一并实现？推荐**只留接口**（先把 Neo4j 主线跑通）。✅/❌
6. **投影失效**：v1 用"惰性指纹 + 抽取完成标脏"，定时重建不做。✅/❌

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| GDS 未安装 / 版本不符 | 启动探测 + 自动降级 + 监控黄标（4.5） |
| KNN 扩展引入噪声实体污染种子 | PPR 阈值过滤 + topK 上限 + 评测集验证；不达标则 `NEO4J_GDS_ENABLED=False` 一键回退 |
| cypher projection 不支持参数化 | kb_id 白名单校验（hex 格式）后内联 |
| 社区版 GDS 并发 = 1 | `_gds_lock` 串行化重操作（仿 `_kuzu_write_lock`） |
| 小图嵌入无意义 / 建议质量差 | `COMPLETION_MIN_ENTITIES` 门槛 + 分数阈值 + 结构/语义/约束三层过滤 |
| LLM 判型成本 | 只对过结构分的候选批量调用；开关可关 |
| 建议洪水淹没审核者 | TOP_N 上限 + tombstone + 拒绝记录沉淀 |
| 补全写入污染图谱 | 与手工建关系同链路（权威 SQLite 事务先行），批准前不落图 |

---

## 11. 验收标准

**M0**
1. Neo4j 模式下启动日志可见 GDS 探测结果；未装 GDS 时自动降级且监控页有提示
2. 对已建图谱的 KB 能成功创建投影并计算嵌入；二次查询命中指纹不重算

**M1**
1. 智能体推理过程面板出现「图扩展」chips（带分数与来源种子）
2. **评测集（OAG Phase 0 的 20~50 条）中，指代类 / 多跳类问题命中率 ≥ 纯双通道基线**
3. 关闭 `NEO4J_GDS_ENABLED` 后行为与现状完全一致（回归零风险）

**M2**
1. 图谱菜单出现「知识补全」Tab；运行后产生带结构分 / 置信度 / 证据的建议列表
2. 批准一条建议后：SQLite relations 与图库 RELATES 均出现该关系（与手工创建一致）；同组合不再重推
3. 拒绝后 tombstone 生效；已存在关系不出现在建议中
4. 建议量受 TOP_N 约束；无本体绑定的 KB 不产生建议（约束过滤兜底）

---

## 附：与现有代码的对接点速查

| 需要 | 来自 | 位置 |
|---|---|---|
| 实体链接改造点 | `_link_entities` 双通道 | oag_service.py:148 |
| 图召回 / RRF 改造点 | `query_stream` 第 4/5 步 | oag_service.py:273 |
| SSE entities 事件 | `yield _sse({"type": "entities", ...})` | oag_service.py:360 |
| GDS 方法挂载点 | `GraphStoreAdapter` / `Neo4jGraphAdapter` | providers/graph_store/__init__.py |
| 关系创建双写（批准链路） | `EntityService.create_relation` | entity_service.py:759 |
| 审核闭环 UI 模式 | `ontology_suggestions` + SuggestionListPage | models.py:192 |
| 防重推模式 | `agent_skill_seed_tombstones` | models.py:352 |
| 本体约束校验 | `constraint_set` 硬校验 | graph_extraction_service.py |
| 异步任务 / 定时 | scheduler / BackgroundTasks | services/scheduler_engine.py |
| 审核提醒 | notifications | routers/notifications.py |
| 抽取完成标脏 | 写图完成回调 | graph_extraction_service.py |
