# 环境部署：Neo4j 图库 + Milvus 向量库

本文说明如何把项目的图数据库从 Kùzu 切到 Neo4j、向量库从 Chroma 切到 Milvus，
并导入 10 万级民航维修图谱数据作为图计算的数据基础。

## 一、现状

| 项 | 状态 |
|---|---|
| `docker-compose.yml`（Neo4j + Milvus + etcd + MinIO + Attu） | 已就绪 |
| 后端适配层 | `Neo4jGraphAdapter` / `MilvusAdapter` 已存在，本次补全了 Milvus 的 `delete_collection` / `list_kb_documents` / `kb_document_count` / `enrich_index_records` |
| `backend/.env` | 已切换为 `GRAPH_STORE_PROVIDER=neo4j`、`VECTOR_STORE_PROVIDER=milvus` |
| Python 依赖 | `neo4j 5.28`、`pymilvus 3.0`、`langchain-milvus 0.4` 已安装 |
| 图谱数据集 | 已生成：100,995 节点 / 319,029 边 |
| **Docker Desktop** | **未安装，需你手动安装** ← 唯一的阻塞项 |

## 二、安装 Docker Desktop（必须先做）

Windows 上 Milvus 只能跑在 Docker 里（依赖 etcd + MinIO），没有替代方案。

1. 下载：<https://www.docker.com/products/docker-desktop/>
2. 安装时勾选 **WSL 2 backend**（本机 `wsl` 命令已可用，无需额外配置）
3. 安装后重启，启动 Docker Desktop，等托盘图标显示 *Running*
4. 验证：

```powershell
docker --version
docker compose version
```

## 三、启动图库与向量库

在项目根目录执行：

```powershell
docker compose up -d
```

首次启动需要拉取约 2GB 镜像，耗时取决于网络。启动后：

| 服务 | 地址 | 账号 |
|---|---|---|
| Neo4j 浏览器 | <http://localhost:7474> | `neo4j` / `ontology123` |
| Neo4j Bolt | `bolt://localhost:7687` | 应用连接用 |
| Attu（Milvus 管理界面） | <http://localhost:8001> | 无需登录 |
| MinIO 控制台 | <http://localhost:9001> | `minioadmin` / `minioadmin` |

> Neo4j 与 Milvus 的密码写在 `docker-compose.yml`，必须与 `backend/.env` 保持一致。
> 改密码时两处都要改。

等待就绪（Milvus 首次初始化约需 60~90 秒）：

```powershell
docker compose ps
```

环境自检：

```powershell
cd backend
python scripts/check_env.py
```

全部显示 `[OK]` 即可进入下一步。

## 四、导入图谱数据

```powershell
cd backend

# 重新生成数据集（可选，已生成过可跳过）
python scripts/build_aviation_graph.py --fleet 370

# 导入 Neo4j（--reset 会先清空图库）
python scripts/load_graph_to_neo4j.py --reset

# 只做校验不导入
python scripts/load_graph_to_neo4j.py --verify-only
```

导入过程约 1~3 分钟，完成后会打印节点/关系构成、数据抽查结果，并自动创建 GDS 图投影。

## 五、数据集说明

### 规模

```
节点 100,995        关系 319,029
```

| 节点类型 | 数量 | 说明 |
|---|---:|---|
| Component | 48,100 | 装机部件实例（带 TSN/CSN/TSR） |
| WorkOrder | 48,100 | 维修工单（3 年历史） |
| FaultMode | 1,609 | 故障模式 |
| ComponentType | 924 | 部件类型（件号级） |
| AirworthinessDirective | 903 | 真实 FAA 适航指令 |
| Engine | 740 | 发动机实例 |
| Aircraft | 370 | 飞机（B-xxxx 注册号） |
| System | 96 | ATA 章节下的子系统 |
| ATAChapter | 52 | ATA 100 章节 |
| Symptom | 30 | 故障征兆 |
| AircraftType | 25 | 机型 |
| EngineType | 24 | 发动机型号 |
| MaintenanceAction | 22 | 维修措施 |

| 关系类型 | 数量 | 含义 |
|---|---:|---|
| OF_TYPE | 49,210 | 实例 → 型号 |
| INSTALLED_ON | 48,100 | 部件装在哪架飞机 |
| ON_AIRCRAFT | 48,100 | 工单属于哪架飞机 |
| REPORTS_FAULT | 48,100 | 工单报告了什么故障 |
| PERFORMS_ACTION | 48,100 | 工单执行了什么措施 |
| CATEGORIZED_BY | 48,100 | 工单归属 ATA 章节 |
| REPLACED_COMPONENT | 16,591 | 工单拆换了哪个部件 |
| RECURRENCE_OF | 5,932 | 重复故障 |
| OCCURS_AT | 1,609 | 故障发生在什么部件 |
| MAY_CAUSE | 784 | 故障传播链 |
| MITIGATES | 561 | AD 缓解了什么故障 |
| APPLIES_TO | 274 | AD 适用于什么机型 |

### 真实性说明

数据的**术语体系全部来自行业权威来源**，实例层按业务规则派生：

- **真实枚举**：ATA 100 章节（52 章）、机型（B737/A320/A350/B787 等 25 种真实型号）、
  发动机型号（CFM56-7B/LEAP-1B/Trent XWB 等 24 种）、部件名、故障模式措辞、
  维修检查等级（GVI/DET/NDT-UT 等）。
- **来自真实语料**：903 条 FAA 适航指令原文，以及从中抽取的 156 个部件名词短语。
- **按业务规则派生**：机号（B-xxxx）、件号、工单号、TSN/CSN/TSR、故障间隔
  （威布尔分布，shape=2.2 对应磨损型故障）、NFF 占比 18%（行业典型 15%~25%）。

已通过以下一致性校验（`load_graph_to_neo4j.py --verify-only` 会自动检查）：

- 同一架飞机不混装不同型号发动机（应为 0 例违规）
- 飞行小时数不小于飞行循环数（应为 0 例违规）
- NFF 占比落在行业典型区间

## 六、图计算

数据导入后会自动创建 GDS 图投影 `aviationFaultGraph`
（包含 ComponentType / FaultMode / System 三类节点）。

在 Neo4j 浏览器（<http://localhost:7474>）里可直接跑：

```cypher
// 1) 关键部件识别：哪些部件类型在故障网络中最"中心"
CALL gds.pageRank.stream('aviationFaultGraph')
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).name AS 部件, score
ORDER BY score DESC LIMIT 20

// 2) 故障社区发现：把互相诱发的故障聚类成"故障综合征"
CALL gds.louvain.stream('aviationFaultGraph')
YIELD nodeId, communityId
RETURN communityId, collect(gds.util.asNode(nodeId).name) AS 故障群, count(*) AS 规模
ORDER BY 规模 DESC LIMIT 10

// 3) 故障传播路径：从某个故障出发能波及哪些部件
MATCH path = (f:FaultMode {name:'疲劳裂纹'})-[:MAY_CAUSE*1..3]->(g:FaultMode)
RETURN f.name, [n IN nodes(path) | n.name] AS 传播链, length(path) AS 跳数
ORDER BY 跳数 DESC LIMIT 20

// 4) NFF 识别：反复拆换却查不出故障的部件（维修成本黑洞）
MATCH (ct:ComponentType)<-[:OF_TYPE]-(c:Component)<-[:REPLACED_COMPONENT]-(w:WorkOrder)
WHERE w.is_nff = true
RETURN ct.name AS 部件, count(*) AS NFF拆换次数
ORDER BY NFF拆换次数 DESC LIMIT 20

// 5) 重复故障排行：同一机型同一故障反复出现
MATCH (a:Aircraft)-[:OF_TYPE]->(t:AircraftType),
      (a)<-[:ON_AIRCRAFT]-(w:WorkOrder)-[:REPORTS_FAULT]->(f:FaultMode)
RETURN t.name AS 机型, f.name AS 故障模式, count(*) AS 工单数
ORDER BY 工单数 DESC LIMIT 20
```

## 七、故障排查

**`docker compose up -d` 报端口占用**

7687 / 7474 / 19530 / 9000 / 9001 / 8001 被占用时，改 `docker-compose.yml` 的宿主端口映射（冒号左边）。

**Neo4j 连不上**

```powershell
docker compose logs neo4j --tail 50
```

通常是还在初始化，等 30 秒重试。

**Milvus 连不上**

Milvus 依赖 etcd 和 MinIO，任一未就绪都会导致连接失败：

```powershell
docker compose ps
docker compose logs milvus --tail 50
```

**GDS 插件未加载**

`docker-compose.yml` 已配置 `NEO4J_PLUGINS`，但只有**首次创建容器**时才会下载插件。
若容器是先于该配置创建的，需要重建：

```powershell
docker compose down
docker compose up -d
```

**后端启动报连不上 Neo4j / Milvus**

`.env` 已切到 neo4j/milvus，但 Docker 未启动时后端会起不来。
临时回退到 Kùzu + Chroma：

```diff
- VECTOR_STORE_PROVIDER=milvus
+ VECTOR_STORE_PROVIDER=chroma
- GRAPH_STORE_PROVIDER=neo4j
+ GRAPH_STORE_PROVIDER=kuzu
```

## 八、常用命令

```powershell
docker compose up -d          # 启动
docker compose down           # 停止（保留数据）
docker compose down -v        # 停止并清空所有数据（慎用）
docker compose ps             # 查看状态
docker compose logs -f neo4j  # 跟踪日志

cd backend
python scripts/check_env.py                          # 环境自检
python scripts/fetch_aviation_corpus.py --max-pages 3 # 抓语料
python scripts/build_aviation_graph.py --fleet 370    # 生成图谱数据
python scripts/load_graph_to_neo4j.py --reset         # 导入图库
```
