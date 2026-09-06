# GDS 高级图计算能力：评估与扩展设计

> 状态：**待确认**
> 日期：2026-09-06
> 前置：《[图迁入与图计算推理_功能设计](./图迁入与图计算推理_功能设计.md)》（v1 五算法 + 规则推理）
> 本文回答两个问题：① GDS 除了手册里那 5 个查询还有什么？② 哪些能落到本系统、值不值得做。
>
> **本文所有"实测"结论均来自 `backend/test/diag_gds_catalog.py`（本次新增，可复跑）**

---

## 0. 结论摘要（先看这段）

1. **当前只用了 GDS 的一小部分**。现有 5 个算法（PageRank / 介数 / Louvain / 相似度 / 度）都属于
   **"图算法族"里的中心性 + 社区两格**；GDS 还有嵌入、KNN、路径搜索、链路预测、局部结构指标、
   图采样、图目录工程化执行模式等一整层没碰。
2. **其中 6 项在本环境（GDS 2.13.2 社区版）已实测可跑**，不是"要买企业版才行"：
   FastRP 嵌入、KNN 近邻、Dijkstra/Yen 最短路、链路预测三件套、WCC 连通分量、
   （改投影为 UNDIRECTED 后）三角计数 / 局部聚类系数 / Leiden。
3. **真正卡脖子的不是算法，是图质量**：实测 20 万节点的投影有 **88,507 个连通分量**
   （精确分布见第 3 节）：主分量仅覆盖 **49.64%**（99,529 节点），第二大分量 12,424（6.20%），
   **孤立点 88,475 个（44.13%）**。在这张图上继续堆算法收益递减，
   **先做连通性治理**才是 ROI 最高的事。
4. 建议本期新增 4 项能力（详见第 5 节）：**图质量体检（WCC）→ 投影分层与 UNDIRECTED 改造 →
   路径类升级（Dijkstra/Yen）→ 嵌入 + KNN 相似检索**；链路预测与局部结构指标放下一期。

---

## 1. 先回答：图计算是不是就是这些查询？

不是。GDS 是一套**分层能力**，现在的用法只覆盖了中层的一小块：

```
┌ L4 生产化/平台  图目录管理 · estimate 预估 · 并发与内存配置 · 采样与子图 · 导出(EE) · Arrow(云端)
├ L3 图机器学习   节点嵌入 fastRP / node2vec(CE) · GraphSAGE(EE) · 分类/链路预测 Pipeline(EE)
├ L2 图算法族     中心性 · 社区 · 相似度 · 路径搜索 · 链路预测 · 局部结构指标
└ L1 图投影       native/cypher 投影 · 有向/无向 · 带属性投影 · 多投影并存
        ↑ 现有设计站在这里：L1 一个投影 + L2 的 5 个算法
```

现有手册的 5 个算法，本质是"**跑一个算法、出一个榜单**"的一次性查询模式。
更高级的用法不在"换更炫的算法"，而在三件事：

| 维度 | 现在的用法 | 更高级的用法 |
|---|---|---|
| **执行模式** | `stream`（出榜）+ `write`（写回） | 加 `mutate`（结果留在内存图，供下一个算法串联）+ `estimate`（跑前预估内存/耗时，防 OOM） |
| **算法之间** | 各自独立跑 | **串联**：fastRP.mutate 生成嵌入 → knn 基于嵌入找近邻 → wcc 先切主分量再算中心性 |
| **图的组织** | 一个投影包打天下 | **多专题投影**：传播图（导致/表现为）算介数、结构图（组成/装于）算社区、全图算 PageRank |
| **结果去向** | 榜单 + 节点属性 | 嵌入可导出到 **Milvus** 做混合召回；社区可作为 **GraphRAG 的摘要单元** |

---

## 2. 本环境能力账本（实测，非推测）

环境：**GDS 2.13.2，社区版（`gds.isLicensed() = False`）**，投影 `cat_0b9891ae7fab`
（200,488 节点 / 776,618 关系，当前为 **NATURAL 有向**）。

| 能力 | GDS 过程 | 实测结果 | 前提 / 备注 |
|---|---|---|---|
| 弱连通分量 WCC | `gds.wcc.*` | ✅ **可跑** | 拿到 88,507 分量（见第 3 节） |
| FastRP 节点嵌入 | `gds.fastRP.*` | ✅ **可跑**，20 万节点 8 维 | ⚠️ 注册名是 `gds.fastRP`（RP 大写），`fastrp` 报 ProcedureNotFound |
| KNN 向量近邻 | `gds.knn.stream` | ✅ **可跑**，产出 1,002,440 相似对 | 需先有节点向量属性；`randomSeed` 与 `concurrency>1` 不可同时给 |
| node2vec 嵌入 | `gds.node2vec.*` | 过程存在（CE 可用） | 未实测，随机游走比 FastRP 慢一个量级 |
| 最短路 Dijkstra | `gds.shortestPath.dijkstra.stream` | ✅ **可跑**（无权重） | 加权需投影带 `relationshipProperties` |
| K 最短路 Yen | `gds.shortestPath.yens.stream` | ✅ **可跑**，k=3 返回 3 条 | 给"多条候选传播链" |
| 链路预测三件套 | `gds.alpha.linkprediction.{adamicAdar,commonNeighbors,preferentialAttachment}` | ✅ **可跑**（单对函数） | 直接进现有建议审核闭环 |
| 三角计数 | `gds.triangleCount.*` | ❌ 当前报错 | 要求 **UNDIRECTED** 投影，改投影后可用 |
| 局部聚类系数 | `gds.localClusteringCoefficient.*` | ❌ 当前报错 | 同上 |
| Leiden 社区 | `gds.leiden.*` | ❌ 当前报错 | 同上；Leiden 是 Louvain 的改良版 |
| 图采样 | `gds.graph.sample.{rwr,cnarw}` | 过程存在 | 大图上抽样子图做交互探索 |
| 子图抽取 | `gds.beta.graph.project.subgraph` | ❌ 本环境无此过程 | 可用 cypher 投影 + 条件实现 |
| GraphSAGE / HashGNN | `gds.beta.graphSage.*` / `gds.hashgnn.*` | ⛔ **企业版** | 需商业授权 |
| 节点分类 / 链路预测 Pipeline | `gds.beta.pipeline.*` | ⛔ **企业版** | 需授权 + 标注数据 |
| 图导出 | `gds.graph.export.*` | ⛔ **企业版** | 导出可用 Cypher 手动取 |

> 复跑方式：`cd backend && python test/diag_gds_catalog.py`（列能力） /
> `... --probe`（实测可调用性） / `... --wcc`（连通性精确分布，只读）

---

## 3. 最重要的一条发现：图是碎的

```
CALL gds.wcc.stats('cat_0b9891ae7fab') YIELD componentCount, componentDistribution
→ componentCount: 88507
  componentDistribution: {min:1, p50:1, p75:1, p90:1, p95:1, p99:1, p999:1, max:99529, mean:2.27}
```

**精确分布**（`gds.wcc.stream` 聚合，只读不写库，脚本 `--wcc` 可复跑）：

| 分量大小 | 分量个数 | 涉及节点 | 占比 |
|---|---|---|---|
| 99,529（主分量） | 1 | 99,529 | 49.64% |
| 12,424（第二分量） | 1 | 12,424 | 6.20% |
| 2 | 30 | 60 | 0.03% |
| **1（孤立点）** | **88,475** | **88,475** | **44.13%** |
| 合计 | 88,507 | **200,488** | 100% ✅ 与 `nodeCount` 对账一致 |

> `49.64%` = 最大分量 99,529 ÷ 投影总节点 200,488（总节点取自 `gds.graph.list()` 的 `nodeCount`）。
> 注：`wcc.stats` 只给分位数（`p999 = 1` 只能推算孤立点约 8.8 万），精确值需用 `wcc.stream` 聚合。

**解读**：整张图实质是"**两个大块（主分量 10 万 + 次分量 1.2 万）+ 8.8 万个散点**"，
中间没有任何过渡规模的中等分量——说明图不是"自然分簇"，而是**大量节点的边在建投影时被丢弃**。

**后果**（对现有 5 个算法是实打实的伤害）：

- PageRank / 介数：孤立点得分恒为 0 或退化，榜单被"主分量"垄断，跨簇比较失真
- Louvain：约 8.8 万个"社区"其实是单个孤立节点，社区榜被噪声稀释
- 相似度 / 度中心性：孤立点直接无邻居可算

**可疑成因**（待验证）：设计文档 3.1 的投影关系查询要求两端**同 `category_id`**：
`WHERE type(r) <> 'RELATES' AND b.category_id = '<cid>'`。跨类别的语义关系、
以及指向 Chunk/Document 等非 Entity 节点的边，在建投影时被整体丢弃，于是一批节点失去所有边。

排查 Cypher（**只读**，先看孤立点都是什么类型）：

```cypher
// 1) 精确分布（不写库）
CALL gds.wcc.stream('cat_0b9891ae7fab') YIELD nodeId, componentId
WITH componentId, count(*) AS sz
RETURN sz, count(*) AS 分量个数 ORDER BY sz DESC;

// 2) 孤立点是什么本体类型（需要分量号，用 write 落属性后分析）
CALL gds.wcc.write('cat_0b9891ae7fab', {writeProperty: 'wcc_component'})
YIELD componentCount;

MATCH (n:Entity {category_id: '0b9891ae7fab'})
WITH n.wcc_component AS c, collect(n) AS ns WHERE size(ns) = 1
UNWIND ns AS n
RETURN [l IN labels(n) WHERE l <> 'Entity'][0] AS 本体类型, count(*) AS 孤立点数
ORDER BY 孤立点数 DESC;
```

若确认是投影丢弃跨类别边，则两种修法：

| 修法 | 做法 | 代价 |
|---|---|---|
| 放宽投影（推荐先试） | 关系查询去掉 `b.category_id = '<cid>'`，改为按 `a` 侧圈定 + 目标端只要求是 `:Entity` | 投影变大；跨类别实体会混进结果 |
| 迁入补边 | 迁入时把跨类别关系统一落到一个"全局图"投影（如 `all_graph`） | 多一个投影、多一份内存 |

> **建议**：把 WCC 体检查出的连通率做成迁入完成后的**必显指标**（下一节 A/B 两项）。

---

## 4. 四项"不只是算法"的工程化能力（建议一并纳入）

1. **`estimate` 跑前预估**：`CALL gds.pageRank.stream.estimate('cat_x', {...})` 返回所需内存与
   是否可执行 → 任务提交前先估一次，避免 20 万节点图上跑到一半 OOM。当前任务引擎直接跑，缺这一步。
2. **`mutate` 结果留内存、算法串联**：如 fastRP.mutate → knn，中间不落库、不落属性，
   跑完整条链再决定写回什么。当前 `stream/write` 两模式做不到串联。
3. **多投影并存 + 按用途选型**：一个 `category_id` 维护 2~3 个投影（见 5.B），
   投影管理已有指纹失效机制，扩展成本低。
4. **并发与超时治理**：`concurrency`、`sudo`（以我为准，忽略内存限制）、`nodeLabels`/`relationshipTypes`
   过滤，让"只算故障模式"这类需求在投影层解决，而非事后过滤。

---

## 5. 落地候选：能不能用到我这系统里（按 ROI 排序）

### A. 图质量体检（WCC + 度分布 + 孤立点清单）—— **强烈建议，本期做**

- **做什么**：迁入完成后自动跑 `wcc.stats` + `degreeDistribution`，产出"连通率 / 主分量占比 /
  孤立点数 / 孤立点类型分布"体检卡；低于阈值（如主分量覆盖率 < 70%）在前端黄标。
- **价值**：现在图是碎的（主分量仅 49.64%、孤立点 44.13%），不体检的话后面所有算法都在半张图上空转。**这是其它能力的前提**。
- **成本**：约半天。纯 `stats` 调用 + 一张展示卡，无新表（可复用 `graph_analysis_tasks`）。
- **对接**：迁入服务"迁入后动作"（设计 4.1）里加一步体检；Tab1 迁入卡片上显示。

### B. 投影分层 + UNDIRECTED 改造 —— **建议本期做（解锁能力最多）**

- **做什么**：每个 `category_id` 维护 3 个投影，并把无向需求显式化：

  | 投影 | 内容 | orientation | 服务算法 |
  |---|---|---|---|
  | `cat_<cid>_prop` | 传播类关系（导致/表现为/诱发） | NATURAL（要方向） | 介数、传播分析、Dijkstra/Yen |
  | `cat_<cid>_struct` | 结构类关系（组成/装于/配套） | **UNDIRECTED** | Louvain/Leiden、LCC、三角计数 |
  | `cat_<cid>`（现状） | 全语义关系 | **UNDIRECTED** | PageRank、相似度、FastRP、KNN |

- **价值**：① 现有 5 算法的语义纯度提升（现在"导致"和"组成"混在一张图上算 PageRank，语义被稀释）；
  ② 一次性解锁 Leiden / 三角计数 / 局部聚类系数（当前因方向问题直接报错）。
- **成本**：1~2 天。改 M0 投影层（`providers/graph_store/gds.py`）+ 算法注册表加 `projection` 字段。
  ⚠️ cypher 投影在 GDS 2.13 **没有 `orientation` 配置键**（旧踩坑记录），UNDIRECTED 需靠
  关系查询写无向模式 `-[r]-`（每边双向两行），或改用 native 投影。

### C. 路径类升级：加权最短路 + K 条候选链 —— **建议本期做**

- **做什么**：现有 6.3 的 `path` API 用原生 `shortestPath`（跳数最少）。升级为：
  - `gds.shortestPath.dijkstra.stream`（带 `relationshipWeightProperty`：置信度/共现次数）
    → 找"**最可能的**传播链"而非"最短的"；
  - `gds.shortestPath.yens.stream(k:5)` → 一次给 5 条候选链，供分析员对比。
- **价值**：直接把"故障 → 部件"的排故证据从一条变多条、从"最短"变"最可信"，是**用户可感知的功能升级**。
- **成本**：约 1 天（含给关系加权重属性：迁入时写 `weight` 或由关系类型映射默认权重）。
- **已验证**：Dijkstra / Yen 在本环境可跑（无权重）。

### D. 节点嵌入 + KNN 相似检索 —— **建议本期做（含跨库玩法）**

- **做什么**：`gds.fastRP.mutate`（8~128 维）→ `gds.knn.stream` 找近邻 → 两种出口：
  1. **图内**：Top-K 相似实体，补强现有 `similar` API（比 Jaccard 更能捕捉"结构等价"：
     两个不直接相连但邻居结构相似的部件会被判为相似，Jaccard 做不到）；
  2. **跨库（进阶）**：`gds.fastRP.write` 把嵌入写到节点，再批量导出写入 **Milvus**，
     与文本向量组成**混合检索**（结构近邻 + 语义相似），喂给 OAG 问答侧（姊妹篇）。
- **价值**：这是"图计算真正反哺 RAG"的通道，也是当前 GraphRAG 热潮里最实用的一招。
- **成本**：图内版约 1 天；跨库版再加 1~2 天（导出 + Milvus collection 设计）。
- **已验证**：FastRP 20 万节点成功、KNN 产出 100 万+ 相似对。
  ⚠️ 注意 `gds.fastRP` 大小写、`knn` 的 `randomSeed` 与并发冲突（见第 2 节）。

### E. 链路预测三件套（共同邻居 / Adamic-Adar / 优先连接）—— 下一期

- **价值**：比现有 Jaccard 更贴近"这两个实体是否该有一条边"的判定，
  Adamic-Adar 对"低度但共享稀有邻居"的组合打分高，适合民航的稀有故障共现场景；
  产物直接进现有 `relation_suggestions`（`source='gds_analysis'`）审核闭环，无需新建流程。
- **成本**：约 1 天（候选对生成是难点：全图 N² 不可行，需用社区/Louvain 结果或度阈值先筛候选）。
- **已验证**：单对函数可跑。

### F. 局部结构指标（三角计数 / 局部聚类系数）—— 下一期，需先完成 B

- **价值**：三角计数高 = 该部件与多个故障互相印证（"系统性问题"信号）；
  局部聚类系数低但度高的节点 = 典型的"枢纽型故障源"。可做**异常结构检测**。
- **成本**：完成 B 后约半天。

### G. 图采样（`gds.graph.sample.rwr`）—— 可选，P3

- 20 万节点规模交互探索仍可用 Cypher 变长路径应付；节点到百万级再上。

### H. 企业版专属（GraphSAGE / HashGNN / 分类与链路预测 Pipeline / 图导出）—— **不建议纳入**

- 需商业授权。其中"节点分类 Pipeline"（如预测某部件是否高风险）还需要**标注数据**，
  当前系统没有故障标签集，即使有授权也用不起来。列为"若未来采购 EE 再评估"。

---

## 6. 与现有设计的对接点（改动清单）

| 现有模块 | 改动 |
|---|---|
| `providers/graph_store/gds.py` | 投影支持 `_prop`/`_struct`/默认三种后缀；加 `gds_estimate()`；暴露 `mutate` 通道 |
| `services/graph_analysis_service.py` | 算法注册表加字段：`projection`（用哪个投影）、`mode`（stream/write/mutate）、`estimate_first`；新增算法：`wcc`、`fastRP`、`knn`、`dijkstra_path`、`yens_path`、`linkprediction` |
| `services/graph_sync_service.py`（设计中） | 迁入完成后自动跑 WCC 体检，指标写入 `graph_sync_runs`（新增 3 列）或 `stats` JSON |
| `routers/graph_analysis.py`（设计中） | 新增 `GET .../{cid}/health`（体检）、`GET .../{cid}/paths`（加权/K 短路） |
| `routers/graph_sync.py`（设计中） | 迁入进度页展示连通率 |
| 前端 Tab1 / Tab2 | 迁入卡片加"图体检"指标；任务卡片加"预估内存/耗时"；结果榜加"相似实体（嵌入）"页签 |
| 数据模型 | `graph_sync_runs` 加 `component_count`/`largest_component`/`isolated_count`；`graph_analysis_tasks.algorithm` 扩枚举 |

---

## 7. 建议的分期（并入现有 P0~P3）

| 阶段 | 新增内容 | 产出 | 估工 |
|---|---|---|---|
| **P0.5**（插在 P0 后） | A 图质量体检 + 第 3 节连通性排查与修投影查询 | 迁入后可见"连通率"，图不再半张空转 | 0.5~1 天 |
| **P1+** | B 投影分层（含 UNDIRECTED 改造）+ C 路径升级 + D 嵌入/KNN（图内版） | 算法语义更纯、排故多链路、结构相似检索 | 3~4 天 |
| **P2+** | E 链路预测进建议闭环 + F 局部结构指标 | 隐含关系挖掘更强 | 1.5 天 |
| **P3** | D 的 Milvus 跨库版（混合检索）+ G 图采样 + GraphRAG 社区摘要 | 图计算反哺问答 | 2~3 天 |

---

## 8. 关键决策点（需确认）

1. **先修图质量再上算法**：本期先做 A（体检 + 排查孤立点成因），P1 的算法扩展示后。✅/❌
2. **投影拆成 3 个（传播/结构/全图）**：接受多投影带来的内存与重建成本。✅/❌
3. **给关系加权重属性**（用于加权最短路）：权重来源用①迁入时的关系置信度 ②关系类型默认权重 ③先统一为 1。选哪个？
4. **嵌入是否导出到 Milvus**：图内相似检索够了，还是要跨库混合召回？✅/❌
5. **企业版**：确认不采购 GDS EE，EE 能力（GraphSAGE/分类 Pipeline）不纳入路线图。✅/❌

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 多投影内存翻倍（20 万节点 × 3） | 按需创建 + 用完即 drop；`estimate` 先估；`graph.list` 监控 `memoryUsage` |
| `mutate` 属性残留导致重跑失败（本次实测已遇到 `fastrpEmb already exists`） | 串联前先 `gds.graph.drop` 或改用一次性属性名；跑前查 `graph.list` 的 `nodeProperties` |
| UNDIRECTED 改造踩坑（cypher 投影无 `orientation` 键） | 关系查询写 `-[r]-` 双向；必要时换 native 投影 |
| KNN/嵌入参数敏感（randomSeed 与并发冲突等） | 参数集中在注册表维护，`estimate` 预跑；把踩坑写进 `test/diag_gds_catalog.py` 注释 |
| 孤立点成因若在数据层（本体关系本身稀疏），非投影能解 | 体检查出后由业务侧决定：补数据 or 接受并在算法里过滤主分量 |

---

## 附录：已实测可跑的 Cypher 片段

```cypher
// 1) 图质量体检（秒级）
CALL gds.wcc.stats('cat_0b9891ae7fab')
YIELD componentCount, componentDistribution RETURN componentCount, componentDistribution;

// 2) 节点嵌入 → 内存图
CALL gds.fastRP.mutate('cat_0b9891ae7fab',
  {embeddingDimension: 8, mutateProperty: 'fastrpEmb'}) YIELD nodePropertiesWritten;

// 3) 基于嵌入的相似近邻（注意：randomSeed 与 concurrency>1 不能同时给）
CALL gds.knn.stream('cat_0b9891ae7fab',
  {nodeProperties: ['fastrpEmb'], topK: 5, sampleRate: 0.02,
   deltaThreshold: 0.1, maxIterations: 5, concurrency: 4})
YIELD node1, node2, similarity
RETURN gds.util.asNode(node1).name AS a, gds.util.asNode(node2).name AS b, similarity
ORDER BY similarity DESC LIMIT 20;

// 4) 两实体间 K 条候选路径
CALL gds.shortestPath.yens.stream('cat_0b9891ae7fab',
  {sourceNode: <idA>, targetNode: <idB>, k: 5})
YIELD index, path RETURN index, [n IN nodes(path) | n.name] AS 链路;

// 5) 单对链路预测打分（进建议闭环）
MATCH (x),(y) WHERE id(x) = <idA> AND id(y) = <idB>
RETURN gds.alpha.linkprediction.adamicAdar(x, y,
  {relationshipQuery: 'MATCH (a)-[r]->(b) RETURN id(a) AS source, id(b) AS target',
   direction: 'BOTH'}) AS score;

// 6) 跑前预估内存（防 OOM）
CALL gds.pageRank.stream.estimate('cat_0b9891ae7fab', {})
YIELD requiredMemory, bytesMin, bytesMax RETURN requiredMemory, bytesMin, bytesMax;
```

> 上述 1/3/4/5 已在 20 万节点投影 `cat_0b9891ae7fab` 实测通过（GDS 2.13.2 社区版）。
> 2 的 mutate 若重跑会报 `fastrpEmb already exists`，需先 drop 投影或换属性名。
