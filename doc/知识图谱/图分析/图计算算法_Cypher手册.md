# 图计算算法 Cypher 手册

> 用途：在 **Neo4j Browser** 中直接运行图分析工作台「计算任务」Tab 的 5 个算法，
> 与后端 `graph_analysis_service.py` / `gds.py` 的实际执行语句逐字一致（GDS 2.13 验证通过）。
>
> 下文所有 Cypher 已内联示例类别 `0b9891ae7fab`（航空图，约 20 万节点 / 77.6 万关系），
> 换其他本体类别时把 `0b9891ae7fab` 全部替换为新 category_id 即可。
>
> 运行环境要求：Neo4j + GDS 插件（`RETURN gds.version()` 能出结果）；
> 「度中心性」一节除外（纯 Cypher，无 GDS 也能跑）。

## 0. 前置：GDS 内存投影

算法不直接扫库，先要在内存投影 `cat_0b9891ae7fab` 上运行。
投影 = 按 `category_id` 圈节点 + 排除业务系统关系 + **无向模式**（`-[r]-`，每条边产生双向两行，等价 UNDIRECTED）。

### 0.1 查看投影是否已存在

```cypher
CALL gds.graph.list('cat_0b9891ae7fab')
YIELD graphName, nodeCount, relationshipCount;
```

有结果且 nodeCount > 0 → 跳到第 1 节直接跑算法；否则执行 0.2 重建。

### 0.2 重建投影（drop + create）

```cypher
// 幂等：存在则先删（第二参 failIfMissing=false，不存在时静默跳过）
CALL gds.graph.drop('cat_0b9891ae7fab', false);

// Cypher 投影（与后端 gds.py ensure_category_projection 完全一致）
CALL gds.graph.project.cypher(
  'cat_0b9891ae7fab',
  'MATCH (e:Entity {category_id: ''0b9891ae7fab''}) RETURN id(e) AS id',
  'MATCH (a:Entity {category_id: ''0b9891ae7fab''})-[r]-(b:Entity)
   WHERE NOT type(r) IN [''RELATES'', ''MENTIONS'', ''HAS_RELATION'', ''RELATION_SOURCE'',
                         ''RELATION_TARGET'', ''HAS_CHUNK'', ''HAS_DOCUMENT'', ''NEXT_CHUNK'']
     AND b.category_id = ''0b9891ae7fab''
   RETURN id(a) AS source, id(b) AS target, type(r) AS type'
)
YIELD graphName, nodeCount, relationshipCount, projectMillis;
```

> 排除列表说明：`RELATES / MENTIONS / ...` 是运行时业务抽取图的系统关系类型，
> 不属于分析图语义关系；业务图节点不带 `category_id` 本就圈不进来，此处是双保险。

---

## 1. PageRank 中心性（关键节点）

### 1.1 直接看榜单（stream，不写库）

```cypher
CALL gds.pageRank.stream('cat_0b9891ae7fab')
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS n, score
RETURN n.id AS id, n.name AS name, n.entity_type AS type, score
ORDER BY score DESC
LIMIT 20;                       // ← top_n，可改
```

只看某类型（label_filter，如只看"故障模式"）：在第 3 行后加

```cypher
WHERE n.entity_type = '故障模式'   // ← 换成实际本体类型名
```

### 1.2 写回节点属性（write，落 `pagerank_score`）

```cypher
CALL gds.pageRank.write('cat_0b9891ae7fab', {writeProperty: 'pagerank_score'})
YIELD nodePropertiesWritten;

// 写回后从库里读榜单（与后端 write 模式的读取语句一致）
MATCH (e:Entity {category_id: '0b9891ae7fab'})
WHERE e['pagerank_score'] IS NOT NULL
RETURN e.id AS id, e.name AS name, e.entity_type AS type,
       e['pagerank_score'] AS score
ORDER BY score DESC LIMIT 20;
```

---

## 2. 介数中心性（桥接节点）

精确算法 O(V·E)，**节点数 > 5 万时后端自动切近似采样模式**（20 万节点图建议直接用近似版）。

### 2.1 精确模式（小图，节点 ≤ 5 万）

```cypher
CALL gds.betweenness.stream('cat_0b9891ae7fab')
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS n, score
RETURN n.id AS id, n.name AS name, n.entity_type AS type, score
ORDER BY score DESC LIMIT 20;
```

### 2.2 近似采样模式（大图，与后端 20 万节点图的实际配置一致）

```cypher
CALL gds.betweenness.stream('cat_0b9891ae7fab',
  {samplingSize: 10000, samplingSeed: 42})
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS n, score
RETURN n.id AS id, n.name AS name, n.entity_type AS type, score
ORDER BY score DESC LIMIT 20;
```

> samplingSeed 固定 42：同图重跑结果可复现。

### 2.3 写回节点属性（`betweenness_score`）

```cypher
// 大图用近似：{writeProperty: 'betweenness_score', samplingSize: 10000, samplingSeed: 42}
CALL gds.betweenness.write('cat_0b9891ae7fab', {writeProperty: 'betweenness_score'})
YIELD nodePropertiesWritten;
```

写回后读取方式同 1.2（属性名换成 `betweenness_score`）。

---

## 3. Louvain 社区发现（故障综合征聚类）

### 原理

Louvain 是基于**模块度（modularity）最大化**的贪心聚类算法，核心思想：把图划分成若干社区，使
**社区内部连边远多于随机情形下的期望**（"内部紧、外部松"），模块度值就是衡量这种聚簇强度的指标
（取值约 -0.5~1，0.3~0.7 即认为有明显的社区结构）。

算法分两阶段迭代：

1. **局部移动**：初始每个节点自成一社区；节点依次尝试移入"能让模块度增益最大"的邻居社区，
   收益为正就搬过去，反复扫到无人再动；
2. **社区凝聚**：把每个社区压缩成一个**超节点**（社区内边变自环、社区间边合并权重），
   在缩小后的图上重复阶段 1。

两阶段循环直到模块度不再提升。凝聚把图逐层压小，复杂度近 O(n·log n)，20 万节点图秒级~分钟级可跑。

在本图谱上：投影是**无向语义关系**（诱发/配套/涉及……），所以社区 = **互相牵连最紧密的
装备件号与故障模式簇**。`communityId` 只是算法编号，无业务含义。

### 作用（业务解读）

| 用途 | 说明 |
|---|---|
| **故障综合征识别** | 同一社区内的故障模式经常共同出现、互相诱发——可打包成"综合征"制定巡检/排故策略，而非逐个故障应对 |
| **机群故障谱圈定** | 实测社区成员多为 `B-xxxxxxx-部件名/SN序列号` 件号与故障模式混聚——一个社区近似刻画"某类机件群的典型故障组合" |
| **共因定位** | 多个看似无关的故障/件号落入同一簇，提示背后可能有共同诱因（环境、批次、使用工况），值得横向排查 |
| **聚类强度评估** | `modularity` 输出可判断整张图是否天然分簇；对比不同迁入版本的分簇变化可感知图谱结构演化 |

与中心性算法的分工：PageRank / 介数找的是**关键节点**（"哪个点重要"），
Louvain 找的是**节点群**（"哪些点是一伙的"）——前者用于聚焦单点，后者用于分簇治理。

### 3.1 社区列表 + 成员样例（stream，与后端聚合逻辑一致）

```cypher
CALL gds.louvain.stream('cat_0b9891ae7fab')
YIELD nodeId, communityId
WITH gds.util.asNode(nodeId) AS n, communityId
RETURN communityId,
       count(*) AS 规模,
       collect(n.name)[..12] AS 成员样例      // 每社区取前 12 个名字
ORDER BY 规模 DESC
LIMIT 50;                                     // 后端取前 50 个社区
```

可视化某个社区（挑一个 communityId 替换）：

```cypher
CALL gds.louvain.stream('cat_0b9891ae7fab')
YIELD nodeId, communityId
WITH gds.util.asNode(nodeId) AS n, communityId
WHERE communityId = 0                         // ← 换成上面查到的社区号
MATCH p = (n)-[]-(m:Entity {category_id: '0b9891ae7fab'})
RETURN p LIMIT 200;
```

### 3.2 写回 `community_id` 并读社区统计

```cypher
CALL gds.louvain.write('cat_0b9891ae7fab', {writeProperty: 'community_id'})
YIELD nodePropertiesWritten, communityCount, modularity;

// 写回后从库里读社区
MATCH (e:Entity {category_id: '0b9891ae7fab'})
WHERE e['community_id'] IS NOT NULL
RETURN e['community_id'] AS communityId,
       count(*) AS size,
       collect({id: e.id, name: e.name, entity_type: e.entity_type})[..12] AS members
ORDER BY size DESC LIMIT 50;
```

---

## 4. 节点相似度（Jaccard，相似故障 / 相似件号）

```cypher
CALL gds.nodeSimilarity.stream('cat_0b9891ae7fab',
  {similarityCutoff: 0.5})                    // ← 相似度下限 0~1，0 = 不过滤
YIELD node1, node2, similarity
WITH gds.util.asNode(node1) AS a, gds.util.asNode(node2) AS b, similarity
RETURN a.name AS 相似源, a.entity_type AS 源类型,
       b.name AS 相似目标, b.entity_type AS 目标类型,
       round(similarity, 4) AS 相似度
ORDER BY similarity DESC
LIMIT 20;
```

> 20 万节点图上此算法最重（分钟级），Browser 跑建议先设 `similarityCutoff`（如 0.5）压输出量。
> 后端不写回此算法结果（「相似于」建议落建议表属 P2）。

---

## 5. 度中心性（高频节点粗排，纯 Cypher / 无需 GDS 和投影）

```cypher
MATCH (e:Entity {category_id: '0b9891ae7fab'})-[r]-(o)
WHERE NOT type(r) IN ['RELATES', 'MENTIONS', 'HAS_RELATION', 'RELATION_SOURCE',
                      'RELATION_TARGET', 'HAS_CHUNK', 'HAS_DOCUMENT', 'NEXT_CHUNK']
  AND o.category_id = '0b9891ae7fab'
RETURN e.id AS id, e.name AS name, e.entity_type AS type,
       count(r) AS degree
ORDER BY degree DESC
LIMIT 20;
```

---

## 6. 清理

```cypher
// 删投影（释放内存，分析图数据不受影响）
CALL gds.graph.drop('cat_0b9891ae7fab', false);

// 清写回的派生属性（可选）
MATCH (e:Entity {category_id: '0b9891ae7fab'})
WHERE e['pagerank_score'] IS NOT NULL OR e['betweenness_score'] IS NOT NULL
      OR e['community_id'] IS NOT NULL
REMOVE e.pagerank_score, e.betweenness_score, e.community_id;
```

## 附：与后端行为的对照

| 算法 | GDS 过程 | 写回属性 | 后端特殊逻辑 |
|---|---|---|---|
| pagerank | `gds.pageRank.stream/write` | `pagerank_score` | — |
| betweenness | `gds.betweenness.stream/write` | `betweenness_score` | 投影节点 > 5 万自动加 `{samplingSize: 10000, samplingSeed: 42}` |
| louvain | `gds.louvain.stream/write` | `community_id` | 取前 50 社区，成员样例 12 个 |
| node_similarity | `gds.nodeSimilarity.stream` | 不写回 | `similarityCutoff` 由参数表单传入 |
| degree | 纯 Cypher | 不写回 | 不依赖 GDS / 投影 |

通用规则（与后端一致）：
- 榜单默认 top 20（上限 100）；`label_filter` 即 `entity_type = '<本体类型名>'` 过滤；
- 写回只落 Neo4j 分析图节点属性（可重建派生数据），**不回写 PostgreSQL 权威库**；
- 同一时刻只应跑一个 GDS 算法（社区版 GDS 单并发）。
