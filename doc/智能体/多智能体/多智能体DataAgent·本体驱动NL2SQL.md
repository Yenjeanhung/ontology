# 多智能体 DataAgent 重新设计：本体驱动 NL2SQL（v6.4 提案）

> 上游讨论：把**表和字段定义成本体和属性、表关系定义成本体关系**，NL 查询时从
> 图谱检索「当前问题该用哪些表、怎么关联」，再生成 SQL——即 **Ontology-based
> Schema Linking**。本文把该思路落成 DataAgent 的开发定义。
> 平台本体四层模型（`models.py`）已天然承接这套建模，无需另起炉灶：
>
> | schema 概念 | 本体模型（现状复用） | 说明 |
> |---|---|---|
> | 数据表 | `Ontology` | code=物理表名，display_name=中文表名，description=表说明 |
> | 字段 | `OntologyAttribute` | code=物理列名，data_type/description/unit/format/**enum_values** 全部现成 |
> | 表关系 | `OntologyRelation` + `OntologyRelationConstraint` | name=关系业务名，inverse_name、端点基数现成；join 字段为本文新增扩展 |
> | 数据源 | `OntologyCategory`（扩展） | 一个数据源 = 一个「数据源本体」类别，类别上挂数据库方言与只读 DSN |
>
> 定位一句话：**DataAgent 从「实体台账查询器」升级为「本体驱动的 NL2SQL 智能体」——
> 图谱管 schema 选择与 join 链路，LLM 管 SQL 生成，确定性校验器管安全，三级链兜底。**

---

## 0. 现状与差距

### 0.1 现有 DataAgent（v6.3）

`scenarios/universal.py::_data_facts(task, nl_filter)` 两级链：

```
NL2Filter（0.6B 抽 filter JSON：entity_type/name/time_range → 精确查 entities 表）
  └─ 未命中/抽错 → 词频老路（关键词强弱信号分级 + 类型聚合 + 最新 8 条明细）
```

只查平台自身的 `entities` 单表，产出 `grade=data_fact` 统计卡 + 明细卡。

### 0.2 差距

| 差距 | 表现 |
|---|---|
| 只会单表 | 跨表关联（航班↔客票↔旅客↔行李/机组/服务/里程）完全不支持，join 条件无处安放 |
| 口径写死 | 聚合口径固定「count + 最新 8 条」，答不了「平均/占比/TopN/Having」 |
| 谓词贫乏 | 只有 ilike 与时间下界，IN / EXISTS / NOT EXISTS / CASE WHEN / 多时间窗皆不可表达 |
| schema 无处管理 | 字段业务含义、枚举值口径散落在 properties JSON 里，模型看不见 |

### 0.3 重新设计目标

1. **schema 入图**：表/字段/关系/口径全部本体化，schema 演进只改图不动 prompt；
2. **图上检索**：NL → 词项命中 → 沿关系边扩展出最小 schema 子图 + join 链；
3. **受约束生成**：LLM 只产出单条 SELECT，确定性校验器做安全与方言归一化；
4. **查询能力全覆盖**：JOIN / LEFT JOIN / RIGHT JOIN（归一化）/ IN / LIKE / EXISTS、
   聚合分组、时间窗、CASE WHEN、UNION、自连接（§5 矩阵）；
5. **不降可靠性**：NL2SQL 失败自动回落 NL2Filter → 词频老路，三级链永不空手。

---

## 1. 总体架构

```
用户任务（data 意图）
  │
  ├─ ① 分词与词项归一（jieba + 别名词典）
  │     token: [航班, CA1501, 旅客, 会员等级]
  ▼
  ├─ ② 种子命中（schema_store.match_seeds）
  │     「航班」→ Ontology(flights)；「旅客」→ Ontology(passengers)
  │     「会员等级」→ Attribute(members.level)（经关系边挂在 2 跳外的表上）
  ▼
  ├─ ③ 子图扩展（schema_store.expand_subgraph，1~2 跳）
  │     flights ─[flight_no 1:N]→ passengers ─[member_id N:1]→ members
  ▼
  ├─ ④ 上下文装配（render_context）
  │     DDL 片段 + join 条件 + 基数警示 + 枚举值 + few-shot
  ▼
  ├─ ⑤ LLM 生成（nl2sql_service.generate_sql）
  │     输出 {"sql", "used_tables", "explain"} 结构化 JSON
  ▼
  ├─ ⑥ 确定性校验（validate_sql：白名单/黑名单/LIMIT/RIGHT JOIN 归一化）
  ▼
  ├─ ⑦ 只读执行（run_readonly：ro URI + 超时 + 行上限）
  │     报错/空结果 → 错误回灌重试（≤2 轮）→ 仍失败 ↓
  ▼
  └─ ⑧ 三级兜底：NL2SQL → NL2Filter → 词频老路（现有代码原样保留）
        产出事实卡（统计 + 明细 + SQL 解释卡）
```

分工原则（与平台「规则管发现、多智能体管研判」一致）：

| 环节 | 归属 | 性质 |
|---|---|---|
| schema 选择、join 链路、口径注解 | **图谱**（确定性检索） | 快、准、可解释 |
| NL 理解、SQL 表达式拼装 | **LLM** | 生成式，受 few-shot 约束 |
| 安全、方言、LIMIT、重试裁决 | **校验器/执行器**（纯代码） | 永不信任 LLM 输出 |
| 最终失败兜底 | NL2Filter / 词频老路 | 已有代码零改动复用 |

---

## 2. Schema 本体建模（复用本体四层 + migration_042 扩展列）

### 2.1 迁移变更清单（migration_042，全部加列、可空、向后兼容）

| 表 | 新增列 | 用途 |
|---|---|---|
| `ontology_categories` | `datasource_dialect` TEXT DEFAULT '' | 数据源方言：sqlite / mysql / postgres；空 = 普通业务本体类别（不参与 NL2SQL） |
| `ontology_categories` | `datasource_dsn` TEXT DEFAULT '' | **独立业务实例只读连接串（必填）**：业务数据不入平台实体库；SQLite 用 `file:...?mode=ro` URI，PG/MySQL 用标准连接串；DSN 为空 = 数据源位置不明 → NL2SQL 直接回落老链 |
| `ontologies` | `alias` TEXT DEFAULT '' | 表别名，逗号分隔（「航班表,flight表」），检索匹配用 |
| `ontology_attributes` | `alias` TEXT DEFAULT '' | 字段别名（「航班号」→ flight_no） |
| `ontology_relations` | `alias` TEXT DEFAULT '' | 关系别名（「旅客」「乘机人」） |
| `ontology_relation_constraints` | `join_condition` TEXT DEFAULT '' | **join 字段映射**，JSON 数组：`[{"left":"flight_no","right":"flight_no"}]`（多列复合键为数组多项），列名相对 source/target 表 |

### 2.2 建模范式（以国航旅客运输域为例）

| 本体对象 | 实际内容 |
|---|---|
| `Ontology`（类别=数据源本体·国航旅客运输） | name=航班表，**code=flights**，display_name=航班动态，alias=航班,flight,航段，description=国航航班运行主表，含起降机场与计划/实际时刻 |
| `OntologyAttribute`（挂 flights） | name=航班号，**code=flight_no**，data_type=string，alias=航班号，description=CA 两字码+数字，如 CA1501 |
| `OntologyAttribute`（挂 tickets） | name=舱位，code=cabin，**enum_values=["头等舱","公务舱","高端经济舱","经济舱"]**，description=客票舱位等级 |
| `OntologyAttribute`（挂 members） | name=会员等级，code=level，**enum_values=["普通卡","银卡","金卡","白金卡","终身白金卡"]**，description=凤凰知音会员等级 |
| `OntologyRelation` | name=**旅客购买客票**，alias=购票,客票，inverse_name=客票所属旅客，cardinality=ONE_TO_MANY |
| `OntologyRelationConstraint`（关系+两表） | source=passengers，target=tickets，target_max=∞（→ N:1 反读），**join_condition=[{"left":"id","right":"passenger_id"}]** |

要点：

- **基数判读**：`target_max == 1` → source:N→target:1（反之为 1:N），写入 prompt 供
  聚合防翻倍与 LEFT/INNER 选择参考（§5.1）；
- **关系命名用业务语义**（「航班承运旅客」），物理字段名放 `join_condition`——检索
  命中靠 alias 中文词，SQL 拼装抄 join_condition，两端字段名不同（`fno` vs
  `flight_no`）也不会错；
- **枚举值直接复用 `enum_values`**：值链接白名单 + 指导 LLM 用 `IN` 而非多 OR；
- **表数量小（< 几百）无需向量检索**：别名+名称+描述的包含匹配足够，全词典常驻内存。

### 2.3 管理入口（零新页面）

数据源本体 = 一个普通本体类别 + 两列数据源配置，**本体管理页现状能力即可维护**
（建类别、建本体=建表、属性页=字段、关系页=表关系）；migration 后在类别编辑处
补充「数据源方言 / 只读 DSN」两个表单字段即闭环。国航旅客运输域走播种脚本（§7）。

---

## 3. schema_store：图谱装载与子图检索（新服务 `services/multi_agent/schema_store.py`）

### 3.1 数据结构

```python
@dataclass
class SchemaColumn:
    code: str; name: str; data_type: str
    alias: str; description: str
    enum_values: list[str]; unit: str; format: str

@dataclass
class SchemaTable:
    code: str; name: str; display_name: str
    alias: str; description: str
    columns: list[SchemaColumn]

@dataclass
class SchemaEdge:
    relation_name: str; relation_alias: str; inverse_name: str
    left_table: str; right_table: str          # 本体 code
    join: list[dict]                           # [{"left": "flight_no", "right": "flight_no"}]
    cardinality: str                           # "1:N" / "N:1"（source→target 视角）

@dataclass
class SchemaGraph:
    datasource_dialect: str; datasource_dsn: str
    tables: dict[str, SchemaTable]             # code → table
    edges: list[SchemaEdge]
```

`load_schema_graph()` 每轮加载（表级 join 查询，量小；后续可加进程内 TTL 缓存）。

### 3.2 检索算法

```python
match_seeds(task) -> (seed_tables, seed_edges, matched_terms)
expand_subgraph(seeds, max_hops=2) -> SchemaSubGraph
render_context(subgraph) -> str        # 注入 prompt 的 schema 上下文
```

1. **词典构建**：`{词 → [table.code/attr/relation]}`，词源 = name + display_name +
   alias + attribute name/code/alias + relation name/alias（全小写化）；
2. **种子命中**：jieba 分词结果逐词查词典（词长优先，≥2 字）；命中的表为种子，
   命中的关系边/跨表字段直接作为必选边；
3. **子图扩展**：种子表沿 `edges` BFS 扩 `max_hops=2`（默认 2 跳，可截断扇出），
   只保留「连通种子集合」的最小边集；单跳扇出超 `MAX_EXPAND_TABLES=6` 时按
   「边 alias 是否被词项命中 > 目标表 description 相关度」剪枝；
4. **孤表裁剪**：种子中与问题无关的泛化命中（如命中了全库词「数据」）不淘汰但降权，
   render 时放尾部并标注「低置信候选表」。

### 3.3 render_context 输出示例（拼进 prompt 的样子）

```text
【可用表】（PostgreSQL 方言）
TABLE airports 机场: airport_code TEXT(三字码), name TEXT(机场名), city TEXT(城市),
  is_international INTEGER(是否国际 0/1)
TABLE flights 航班动态: flight_no TEXT(航班号), dep_airport TEXT(起飞机场三字码),
  arr_airport TEXT(到达机场三字码), dep_datetime TIMESTAMP(计划起飞),
  arr_datetime TIMESTAMP(计划到达), aircraft_type TEXT(机型), dep_gate TEXT(登机口),
  status TEXT(航班状态, 取值: 计划/起飞/到达/取消), delay_min INTEGER(延误分钟)
TABLE members 凤凰知音会员: member_id TEXT(会员ID), level TEXT(会员等级,
  取值: 普通卡/银卡/金卡/白金卡/终身白金卡), miles INTEGER(里程余额)
TABLE passengers 旅客: id TEXT(旅客ID), name TEXT(姓名), phone TEXT(联系电话),
  member_id TEXT(会员ID, 可空)
TABLE tickets 客票航段: ticket_no TEXT(票号), passenger_id TEXT(旅客ID),
  flight_no TEXT(航班号), cabin TEXT(舱位, 取值: 头等舱/公务舱/高端经济舱/经济舱),
  fare DECIMAL(票价), seat_no TEXT(座位号), status TEXT(客票状态,
  取值: 已出票/已值机/已登机/已成行/退票)
TABLE baggage 行李: bag_tag TEXT(牌号), ticket_no TEXT(票号), weight_kg DECIMAL(重量),
  status TEXT(行李状态, 取值: 已托运/已装机/运输中/已到达/已提取/异常滞留),
  special TEXT(特殊标记, 可空, 取值: 超规/易碎)
TABLE crew_members 机组: crew_id TEXT(工号), name TEXT(姓名), gender TEXT(性别),
  role TEXT(岗位, 取值: 机长/副驾驶/乘务长/乘务员), base_airport TEXT(驻地机场三字码),
  hire_date TEXT(入职日期)
TABLE crew_assignments 排班: id INTEGER(排班ID), flight_no TEXT(航班号),
  crew_id TEXT(工号), duty_role TEXT(执飞岗位, 取值: 机长/副驾驶/乘务长/乘务员)
TABLE service_requests 服务请求: request_no TEXT(单号), passenger_id TEXT(旅客ID, 可空),
  flight_no TEXT(航班号, 可空), type TEXT(类型, 取值: 餐食/住宿/接送机/补偿/票务/投诉),
  channel TEXT(渠道, 取值: App/小程序/柜台/电话), status TEXT(状态,
  取值: 待受理/处理中/已办结), satisfaction INTEGER(满意度 1-5, 可空),
  created_at TIMESTAMP(提交时间), closed_at TIMESTAMP(办结时间)
TABLE mileage_records 里程流水: id INTEGER(流水ID), member_id TEXT(会员ID),
  ticket_no TEXT(票号, 可空), type TEXT(类型, 取值: 乘机累积/活动赠送/兑换/过期调整),
  miles INTEGER(里程变动, 正=累积 负=兑换/过期), created_at TIMESTAMP(发生时间)
TABLE compensations 延误补偿: id INTEGER(补偿ID), flight_no TEXT(航班号),
  passenger_id TEXT(旅客ID), reason TEXT(原因, 取值: 延误4小时以上/航班取消/超售),
  amount DECIMAL(金额), status TEXT(状态, 取值: 应发/已发/已冲正),
  created_at TIMESTAMP(登记时间)

【表关系】（join 时必须使用以下 ON 条件）
- airports →[起飞机场] flights，基数 1:N
  ON airports.airport_code = flights.dep_airport
- airports →[到达机场] flights，基数 1:N
  ON airports.airport_code = flights.arr_airport
- flights →[航班售出客票] tickets，基数 1:N
  ON flights.flight_no = tickets.flight_no
- passengers →[旅客购买客票] tickets，基数 1:N
  ON passengers.id = tickets.passenger_id
- members →[会员关联旅客] passengers，基数 1:N
  ON members.member_id = passengers.member_id
- tickets →[客票托运行李] baggage，基数 1:N
  ON tickets.ticket_no = baggage.ticket_no
- flights →[航班排班机组] crew_assignments，基数 1:N
  ON flights.flight_no = crew_assignments.flight_no
- crew_members →[机组执飞航段] crew_assignments，基数 1:N
  ON crew_members.crew_id = crew_assignments.crew_id
- airports →[机组驻地] crew_members，基数 1:N
  ON airports.airport_code = crew_members.base_airport
- passengers →[旅客发起服务请求] service_requests，基数 1:N
  ON passengers.id = service_requests.passenger_id
- flights →[航班产生服务请求] service_requests，基数 1:N
  ON flights.flight_no = service_requests.flight_no
- members →[会员产生里程流水] mileage_records，基数 1:N
  ON members.member_id = mileage_records.member_id
- tickets →[客票关联里程流水] mileage_records，基数 1:N
  ON tickets.ticket_no = mileage_records.ticket_no
- flights →[航班产生补偿] compensations，基数 1:N
  ON flights.flight_no = compensations.flight_no
- passengers →[旅客获得补偿] compensations，基数 1:N
  ON passengers.id = compensations.passenger_id
【注意】① 可空外键（passenger_id / flight_no / ticket_no）所在边取「全部主表行」
  必须 LEFT JOIN，INNER JOIN 会静默丢行；② 1:N 边上对主表字段 SUM/COUNT 必须先
  按主键去重或先子查询聚合，否则统计翻倍；③ 多路径可达（旅客→客票→航班）时
  以问题主语命中的种子表为起点选唯一边链，禁止同链重复 join 同一条边。
```

---

## 4. nl2sql_service：生成、校验、执行、重试（新服务 `services/multi_agent/nl2sql_service.py`）

### 4.1 generate_sql：一次 LLM 调用，结构化输出

```python
async def generate_sql(task: str, ctx: str, dialect: str, retries: int = 2
                       ) -> Optional[dict]:
    """返回 {"sql": str, "used_tables": [..], "explain": str}；失败返回 None。"""
```

- system：角色（只产单条 SELECT 的取数引擎）+ 方言声明 + §5 反模式红线；
- user：`ctx`（§3.3）+ few-shot（§5.4）+ 用户任务原文；
- 输出强制 JSON（`json.loads` 失败重试 1 次）；`used_tables` 必须被子图表包含。

### 4.2 validate_sql：确定性校验与归一化（永不信任 LLM）

| 规则 | 处置 |
|---|---|
| 仅一条语句：剥尾分号后禁止再次出现 `;` | 拒绝 |
| 黑名单词（不区分大小写、含子串扫描）：INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/REPLACE/MERGE/GRANT/REVOKE/PRAGMA/ATTACH/DETACH/VACUUM/load_extension | 拒绝 |
| 表名白名单：`used_tables` ∪ 图谱表 code，SQL 中出现的标识符必须 ⊆ 白名单 | 拒绝（提示「只能使用给定表」） |
| 无 LIMIT | 自动追加 `LIMIT 500` |
| LIMIT 超过 `MAX_ROWS=500` | 改写为 500 |
| `SELECT *` | 改写为 `SELECT t.*`（仅当子图 ≤3 表时放行，否则拒绝要求显式列） |
| RIGHT JOIN（仅 sqlite 分支） | sqlite 不支持 → 交换两表与 ON 条件，归一化为 LEFT JOIN；postgres/mysql 原生放行 |
| FULL OUTER JOIN | postgres 原生放行；sqlite/mysql 归一化为 `LEFT JOIN ... UNION ... 反向 LEFT JOIN（排除已匹配）`，超 3 表则拒绝 |
| 隐式 join（FROM a, b + WHERE 连接） | 拒绝，要求显式 JOIN ON（防笛卡尔积漏写） |

sqlglot 可用则先 parse 再校验（AST 级表名提取）；不可用则上述正则降级实现
（与项目「依赖缺失自动降级」惯例一致）。

### 4.3 run_readonly：只读执行

```python
async def run_readonly(dsn: str, dialect: str, sql: str,
                       timeout_s: float = 8.0) -> dict
# 返回 {"ok", "columns", "rows", "rowcount", "error", "elapsed_ms"}
```

- PostgreSQL（默认，独立 PG 业务库）：DSN 来自本体类别配置；播种时与平台主库
  同服务器自动新建 `biz_aviation` 库（或 `--dsn` 显式指定独立实例），与平台
  `DATABASE_URL` **不同库**；只读角色 + 连接级 `default_transaction_read_only=on`
  + `statement_timeout` 三道保险；
- SQLite（CI 兜底文件库，`--target sqlite`）：`aiosqlite.connect(f"file:{path}?mode=ro", uri=True)`；
- 执行包 `asyncio.wait_for(timeout_s)`；行数截断 `MAX_ROWS`，超限在结果中标注；
- MySQL（P2）接入：只读账号 + 同样的超时/行上限。

### 4.4 报错回灌重试

```
第 1 次执行报错（如 no such column / ambiguous）
  → 把 {"上次SQL", "数据库错误", 校验器拒绝原因} 追加进对话再生成（≤2 轮）
  → 连续失败 → 返回 None → 调用方回落 NL2Filter 老路
```

空结果不算失败（可能确实无数据），但会触发一次「放宽条件自查」：提示 LLM
检查是否有过度过滤（如把 LIKE 写成等值），自查后仍空则按空结果出卡。

**为什么不是 ReAct**：形态上二者同构（都是 Action→Observation→再生成），但
ReAct 的定义性特征是 **LLM 驱动循环**——每步显式 Thought、从开放动作空间自选
工具、自己决定何时终止；本节是固定流程的**自修正循环**（bounded self-correction /
generate→validate→execute→repair，同 Self-Debug / execution-guided decoding 一族），
与 ToolAgent 的 ReAct 式自主工具循环的本质区别在**决策权**——动作空间封闭（生成→校验→执行，
无「下一步调什么工具」可规划）、循环决策者是代码（预算封顶 ≤2，LLM 无权续跑）、
失败出口固定（回落 NL2Filter/词频老路，LLM 无权放弃或改道）。SQL 正确性检查
分两层且都不依赖 LLM 自评：静态层=确定性校验器（§4.2），动态层=真实执行报错
回灌（本节）。取数场景要求确定性、可审计、封顶预算，ReAct 的开放循环只增延迟
与不可控性。（P2 可选增强：执行前 `EXPLAIN` 预检 + 语义自查卡，仍走代码裁决。）

### 4.5 事实卡产出（沿用 `grade=data_fact`，合成官零适配）

| 卡 | id | 内容 |
|---|---|---|
| SQL 解释卡 | `fact-data-sql` | title=「SQL 查询 · N 表 M 关联」，detail=检索到的表 + join 链 + SQL 截断 200 字 + （重试次数） |
| 统计卡 | `fact-data-agg` | 行集概览：行数、列清单、数值列 min/max/avg、首列 TopN 分布 |
| 明细卡 | `fact-data-N` | 每行一张，`k=v` 串（列名用中文属性名），上限 `DATA_SAMPLE=8` 复用 |

结果行集同时挂 `rows` 原始数据在 fact 卡 `data` 字段（供 P2 图表智能体直接取数成图，
复用 v6.1 chart_result 链路）。

### 4.6 双模式：Pipeline（默认）× ReAct（可选档，同一循环骨架）

§4.4 的自修正循环骨架保持不变，**只切换回灌轮的提示策略**——这正是
「agent loop 是机制、ReAct 是策略」的活演示（同一 loop，两种走法）：

| 模式 | Thought | 触发 | 取舍 |
|---|---|---|---|
| pipeline（默认） | 无，直接按报错重新生成 | 线上默认 | 快、token 省、完全确定 |
| react（可选档） | LLM 显式输出思考链再生成 | 复杂问题 / 回灌轮自动升级 | 每轮多 ~100 token；可解释、成功率高 |

**react 档输出契约**：`{"thought", "sql", "used_tables", "explain"}`，thought 固定
四问——① 问题拆解；② 选表理由（须引用子图关系边）；③ JOIN 三态裁决依据
（INNER/LEFT 信号词）；④ 1:N 防翻倍自检。回灌轮 thought = 错误归因 + 修正策略。
thought 轨迹并入 SQL 解释卡 detail（P2 独立 SSE `thought` 事件 + 过程区
「思考链」时间线）。

**切换规则（预算封顶不变，总重试仍 ≤2 轮）**：含分析意图词（对比/排名/为什么）
或 join 链 ≥2 跳 → 首轮即 react 档；首轮失败 → 第 2 轮回灌自动切 react 模板；
react 档不增加调用次数，只让最后一轮「想清楚再改」。

---

## 5. SQL 查询情况覆盖矩阵（核心章节）

每类查询情况四要素：**问题信号**（什么样的自然语言触发）→ **图依据**（本体图上
什么信息支撑）→ **SQL 形态** → **防错要点**（校验器/prompt 红线）。

### 5.1 关联查询（JOIN 三态 + 链式 + 自连接）

| 情况 | 问题信号 | 图依据 | SQL 形态 | 防错要点 |
|---|---|---|---|---|
| **INNER JOIN** | 两表词都被提到且存在关系边（「CA1501 航班的旅客」） | 关系边 join_condition | `FROM flights f JOIN tickets t ON f.flight_no = t.flight_no JOIN passengers p ON t.passenger_id = p.id` | ON 条件必须逐字抄 join_condition，禁止 LLM 自拟字段 |
| **LEFT JOIN** | 「所有航班**及其**客票（没有客票也要列出）」「**没有**客票的航班」 | 基数 1:N + 主表词序（问题主语在前） | `FROM flights f LEFT JOIN tickets t ON f.flight_no = t.flight_no` | 主表 = 问题主语命中的种子表；「没有X」形态 → LEFT JOIN + `WHERE x.id IS NULL`（反连接） |
| **RIGHT JOIN** | 主宾语倒装（「每个旅客所属的航班」以旅客为主体） | 同一边，反向读 | sqlite 不支持 → **校验器归一化**为反向 LEFT JOIN | 归一化后日志标注；postgres/mysql 原生放行 |
| **多表链式** | 三表词共现（「旅客的会员等级」passengers→members） | BFS 2 跳路径 | 逐边串 JOIN，别名 f/p/m | 路径不连通 → 拒绝生成并如实回答「两表无关联路径」，禁止 LLM 臆造字段连接 |
| **自连接** | 同表比较（「同一到达机场的航班对」） | 同一表两条边/同表复用 | `FROM flights a JOIN flights b ON a.arr=b.arr AND a.flight_no<b.flight_no` | 强制别名 + 去重谓词，few-shot 覆盖 |

**INNER vs LEFT 的裁决**：默认 INNER；出现「所有/每个（主语侧）」「没有/未/缺少」
信号词时切 LEFT（或反连接）。该规则写进 system prompt + few-shot 各一例。

### 5.2 谓词与集合（IN / LIKE / EXISTS / BETWEEN）

| 情况 | 问题信号 | 图依据 | SQL 形态 | 防错要点 |
|---|---|---|---|---|
| **IN（枚举）** | 多值并列（「头等舱/公务舱/高端经济舱三种舱位」） | 属性 enum_values 白名单 | `cabin IN ('头等舱','公务舱','高端经济舱')` | 值必须来自 enum_values ∪ 问题原词；禁止 LLM 编造枚举值 |
| **IN（子查询）** | 「有头等舱旅客的航班」 | 关系边 | `flight_no IN (SELECT flight_no FROM ...)` | 单列子查询；优先 EXISTS（见下）；子查询同样过白名单 |
| **NOT IN 陷阱** | 「不属于重点通航机场的航班」 | — | **禁止**，强制改写 `NOT EXISTS` 或 `LEFT JOIN ... IS NULL` | 红线：NOT IN 子查询含 NULL 会导致全空，system prompt 明示 |
| **LIKE** | 模糊匹配（「名称含『延误』的告警」） | 属性 data_type=string | `name LIKE '%延误%'` | 前导 `%` 全表扫描——小库放行；LIKE 简单值自动 `ESCAPE '\'`；正则类需求（regex）拒绝改 LIKE |
| **EXISTS / NOT EXISTS** | 存在性（「有已值机旅客的航班」「从未延误的航线」） | 关系边 + 基数 | `WHERE EXISTS (SELECT 1 FROM tickets t WHERE t.flight_no=f.flight_no AND t.status='已值机')` | 半连接首选；`SELECT 1` 红线；关联条件必须用边 join_condition |
| **BETWEEN / 时间窗** | 「今天/本周/上月/两个日期之间」 | 属性 format（YYYY-MM-DD）+ 方言 | PG：`dep_datetime BETWEEN '2026-09-01' AND ...`（sqlite 档 `date()` 包裹后字典序比较） | 相对时间词由 LLM 结合当前日期换算（当前日期注入 prompt） |

### 5.3 聚合、分组与派生

| 情况 | 问题信号 | 图依据 | SQL 形态 | 防错要点 |
|---|---|---|---|---|
| **GROUP BY + 聚合** | 「按起飞机场统计航班数」 | 关系边 | `SELECT dep_airport, COUNT(*) ... GROUP BY dep_airport ORDER BY 2 DESC` | ORDER BY 聚合列必须显式；默认 LIMIT 500 |
| **HAVING** | 「平均延误超过 30 分钟的起飞机场」 | 属性 unit（分钟） | `... GROUP BY dep_airport HAVING AVG(delay_min) > 30` | 聚合条件进 HAVING 而非 WHERE（few-shot 区分一例） |
| **1:N 翻倍防护** | join 场景的任何 SUM/COUNT(A) | 边 cardinality | 先子查询聚合再 join：`JOIN (SELECT flight_no, COUNT(*) n FROM passengers GROUP BY flight_no) x ON ...` | **红线**：cardinality=1:N 的边被 join 后对主表聚合必须去重/先聚合，prompt+校验提示双保险 |
| **CASE WHEN 分桶** | 「按延误时长分档统计」 | 属性 description 口径 | `CASE WHEN delay_min>60 THEN '重度' ... END AS 档位` | 分档阈值来自问题原词，问题未给则反问/取默认并注明 |
| **TopN** | 「延误最久的 5 个航班」 | — | `ORDER BY delay_min DESC LIMIT 5` | LIMIT 豁免 500 上限改写（取问题中的 N，≤50） |
| **UNION** | 「A 类和 B 类告警合并去重」 | 多表/多条件 | `SELECT ... UNION SELECT ...` | 列数一致校验；去重语义默认 UNION（不 UNION ALL），问题明说「全部」才 ALL |
| **派生表/CTE** | 复杂两步分析（「先算各航班旅客数，再取旅客数最多的 5 个航班」） | 边链 | `WITH x AS (...) SELECT ...` | SQLite 支持 CTE；嵌套 ≤2 层红线，超了引导拆两轮 |

### 5.4 few-shot 清单（共 9 例，system+user 间按子图表相关性选注 4~6 例）

| # | 示例问题 | 覆盖情况 |
|---|---|---|
| 1 | 「CA1501 航班的所有旅客及会员等级」 | 3 表 INNER 链 flights→tickets→passengers→members + 中文列名 |
| 2 | 「统计每个航班的旅客数，没有旅客的航班也要列出」 | LEFT JOIN（经 tickets 桥）+ COUNT 防翻倍 + 0 填充 |
| 3 | 「哪些航班没有旅客托运行李」 | LEFT JOIN 反连接（flights→tickets→baggage IS NULL） |
| 4 | 「平均票价超 1000 元的舱位及其客票量」 | GROUP BY + HAVING + 子查询防翻倍 |
| 5 | 「金卡会员乘坐过的航班」 | EXISTS 半连接（members→passengers→tickets，EXISTS 优于 IN） |
| 6 | 「按延误分档（≤15/15-60/>60）统计各档航班数」 | CASE WHEN + GROUP BY |
| 7 | 「CA1501 的机长是谁」 | 第二个 M:N 桥 crew_assignments + role 枚举筛选（扩充域） |
| 8 | 「本月净增里程为正的金卡会员」 | 正负流水 SUM + HAVING + level 枚举（扩充域） |
| 9 | 「投诉未办结且满意度 ≤2 分的旅客名单」 | 可空 FK 边 + 区间筛选，验证 LEFT JOIN 不丢行（扩充域） |

### 5.5 反模式红线（system prompt 逐条明示，校验器拦截）

1. 禁 `NOT IN (子查询)`（NULL 陷阱）→ NOT EXISTS；
2. 禁隐式 join（`FROM a, b WHERE`）→ 显式 JOIN ON；
3. 禁 `SELECT *`（明确列清单，≤3 表整表放行）；
4. 1:N join 后禁对主表直接聚合（先子查询/去重）；
5. 禁臆造 schema 外的表/字段/枚举值；
6. 禁非 SELECT 语句与多语句；
7. 禁一次性回答「需要多步才能出结果」的问题——拆成单条 SQL 分轮执行。

### 5.6 方言适配

| 方言 | RIGHT JOIN | FULL OUTER | 日期函数 | 备注 |
|---|---|---|---|---|
| **postgres（P1，默认档·独立业务库）** | 原生放行 | 原生放行 | `CURRENT_DATE` / `now()` | DSN 必填，独立 PG 业务库（自动建 biz_aviation） |
| sqlite（P1，CI 兜底） | 归一化 LEFT JOIN | UNION 改写或拒绝 | `date()` 可用 | `--target sqlite` 文件库 |
| mysql（P2） | 原生放行 | 拒绝（引导 UNION） | `DATE()`/`CURDATE()` | — |

方言由 `datasource_dialect` 决定，连接串落在本体类别的 `datasource_dsn`
（与平台 `DATABASE_URL` 解耦、同款写法），方言声明注入 system prompt；SQL
归一化规则只在 sqlite 分支生效，PG/MySQL 原生能力直接放行。

---

## 6. 引擎与路由接线（universal.py 改动点，克制）

### 6.1 `_data_facts` 升级为三级链

```python
async def _data_facts(task: str, nl_filter: Optional[dict] = None) -> list[dict]:
    # ① NL2SQL 路径（新增）：存在「数据源本体」类别且 datasource_dialect + dsn 齐备才启用；
    #    schema_store.load → match/expand/render → generate → validate → run
    #    产出 fact-data-sql + agg + 明细卡；失败/禁用 ↓
    # ② NL2Filter 路径（现有 _data_facts_by_filter，零改动）
    # ③ 词频老路（现有主体，零改动）
```

- 节点 goal 文案：命中 NL2SQL 时「本体检索 N 表 → 生成 SQL 取数」，否则沿用原文案；
- `node_done` summary 附「SQL 重试 x 次后成功 / SQL 路径失败回落台账老路」；
- **P1 不新增 SSE 事件类型**（前端零改动，过程区照常走 node_start/node_done/fact）；
  P2 再评估独立 `sql` 事件 + 过程区「SQL 检索」tab。

### 6.2 路由与组队（router_service 零改动）

- data 意图精简组合不变（`SLIM_ROSTERS["data"]`）；
- NL2SQL 是 data_agent 节点内部的**第一优先取数手段**，门控在节点内
  （无数据源本体类别 = 静默跳过，与 OTel/图表 MCP 的「缺失自动降级」同风格）；
- ToolAgent 的内置工具 `data_query`（tool_registry）P2 同源升级：同一
  schema_store + nl2sql_service 换个入口，多智能体两条取数车道口径一致。

## 7. 国航旅客运输域：数据与播种（`backend/scripts/seed_schema_ontology.py`，幂等）

**场景设定**：国航（CA）旅客运输全域——航司固定为国航（不设航司维度表，
字段中无 airline_code），覆盖六条业务线：**机场/航班 → 客票 → 旅客 / 行李 /
凤凰知音会员 → 机组排班 / 服务请求（投诉工单）/ 里程流水 / 延误补偿**，
支撑真实跨表业务问题（旅客托运了哪些行李、CA1501 的机长是谁、金卡会员
本月净增里程、延误补偿发放统计、投诉未办结旅客名单…）。

**落库位置（业务数据与平台实体库分离）**：业务数据落**独立 PG 业务库**，不入平台
实体库——播种时与平台主库同一 PG 服务器自动新建 `biz_aviation` 库（`--dsn`
省略时按 `DATABASE_URL` 自动推导同账号新库，需 CREATEDB 权限；`--dsn` 显式指定
其他实例亦可，指向平台主库会被直接拒绝；`--target sqlite` 降为 CI 兜底文件库）。
平台主库**只存本体映射**：「数据源本体·国航旅客运输」类别 + 11 表/字段/15 关系
（关系带类别内唯一编码），类别上 `datasource_dialect` + `datasource_dsn` 指向
独立实例，NL2SQL 执行器按 DSN 只读连接（asyncpg 只读会话 / sqlite ro URI）；
DSN 未配置 = 数据源位置不明 → 直接回落老链，绝不默认连平台库。

| 表 | 关键字段 | 说明 |
|---|---|---|
| airports | airport_code PK, name, city, is_international | 机场（~25 行，PEK/PKX 主基地 + 主要通航点） |
| flights | flight_no PK, dep/arr_airport FK, dep/arr_datetime, aircraft_type, dep_gate, status(计划/起飞/到达/取消), delay_min | 航班（~200 行，近 7 天，全 CA 班表） |
| members | member_id PK, name, level(普通卡/银卡/金卡/白金卡/终身白金卡), miles, join_date | 凤凰知音会员（~300 行） |
| passengers | id PK, name, gender, phone(脱敏), member_id FK 可空 | 旅客（~600 行，约四成注册会员） |
| tickets | ticket_no PK(999-开头票号), passenger_id FK, flight_no FK, cabin(头等舱/公务舱/高端经济舱/经济舱), fare, seat_no, status(已出票/已值机/已登机/已成行/退票) | 客票航段 = 旅客×航班 多对多桥（~1800 行） |
| baggage | bag_tag PK(999-开头牌号), ticket_no FK, weight_kg, status(已托运/已装机/运输中/已到达/已提取/异常滞留), special 可空(超规/易碎) | 行李（~1300 行，挂航段） |
| crew_members | crew_id PK, name, gender, role(机长/副驾驶/乘务长/乘务员), base_airport FK, hire_date | 机组（~120 行，覆盖全部航班排班） |
| crew_assignments | id PK, flight_no FK, crew_id FK, duty_role(机长/副驾驶/乘务长/乘务员) | 排班 = 航班×机组 第二个多对多桥（~800 行，每班 1 机长 1 副驾 2 乘务起） |
| service_requests | request_no PK, passenger_id FK **可空**(匿名/代理), flight_no FK **可空**(非航班类), type(餐食/住宿/接送机/补偿/票务/投诉), channel(App/小程序/柜台/电话), status(待受理/处理中/已办结), satisfaction 可空(1-5), created_at, closed_at | 服务请求/投诉工单（~450 行，含未关联航班与未办结） |
| mileage_records | id PK, member_id FK, ticket_no FK **可空**(活动赠送等非乘机累积), type(乘机累积/活动赠送/兑换/过期调整), miles **正负**(累积+/兑换-), created_at | 里程流水（~2500 行，正负相抵可对账 members.miles） |
| compensations | id PK, flight_no FK, passenger_id FK, reason(延误4小时以上/航班取消/超售), amount, status(应发/已发/已冲正), created_at | 延误补偿（~350 行，金额+状态机） |

关系边（15 条，join_condition 落库；每张事实表至少 2 条入边——星型）：

| 关系 | 基数 | join_condition |
|---|---|---|
| 航班售出客票 flights→tickets | 1:N | flight_no = flight_no |
| 旅客购买客票 passengers→tickets | 1:N | id = passenger_id |
| 会员关联旅客 members→passengers | 1:N | member_id = member_id |
| 客票托运行李 tickets→baggage | 1:N | ticket_no = ticket_no |
| 起飞机场 airports→flights | 1:N | airport_code = dep_airport |
| 到达机场 airports→flights | 1:N | airport_code = arr_airport（同表双边，§5.1 自连接同款机制） |
| 航班排班机组 flights→crew_assignments | 1:N | flight_no = flight_no |
| 机组执飞航段 crew_members→crew_assignments | 1:N | crew_id = crew_id |
| 机组驻地机场 airports→crew_members | 1:N | airport_code = base_airport |
| 旅客发起服务请求 passengers→service_requests | 1:N | id = passenger_id |
| 航班产生服务请求 flights→service_requests | 1:N | flight_no = flight_no（**可空 FK 边**） |
| 会员产生里程流水 members→mileage_records | 1:N | member_id = member_id |
| 客票关联里程流水 tickets→mileage_records | 1:N | ticket_no = ticket_no（**可空 FK 边**） |
| 航班产生补偿 flights→compensations | 1:N | flight_no = flight_no |
| 旅客获得补偿 passengers→compensations | 1:N | id = passenger_id |

新机制覆盖（§5 矩阵教学点逐项落地）：

- **第二个 M:N 桥**（crew_assignments）+ 岗位枚举筛选——「机长」是枚举值不是表；
- **双可空外键**（service_requests：匿名无旅客 / 非航班类无航班）——取全量必须
  LEFT JOIN，INNER JOIN 会静默丢行（§5.1 教学点强化）；
- **正负流水**（mileage_records.miles ±）——SUM 聚合对账 + HAVING 净额筛选；
- **金额状态机**（compensations：应发/已发/已冲正）——状态筛选 + 金额聚合；
- **多事实星型**——passengers 周围挂 tickets / service_requests / compensations /
  mileage_records（经 members）四张事实表，考验 BFS 选边与主语表判定。

典型问题与 join 链（§11 验收基线）：

- 「CA1501 航班的旅客托运了哪些行李」：flights→tickets→baggage ∪ tickets→passengers（3 表链）；
- 「金卡会员本周乘坐过哪些航班」：members→passengers→tickets→flights（4 表链）；
- 「CA1501 的机长是谁」：flights→crew_assignments→crew_members + `role='机长'`（桥 + 枚举）；
- 「执飞过成都出港航班的乘务长及其执飞次数」：crew_members→crew_assignments→flights→airports(dep)
  + `city='成都'` + COUNT（4 表链 + 城市过滤 + 聚合排序）；
- 「延误超 2 小时航班的补偿发放总额与件数」：flights→compensations + `status='已发'`
  （金额 SUM/COUNT + 状态筛选）；
- 「本月净增里程为正的金卡会员」：mileage_records GROUP BY member `SUM(miles)>0`
  → members `level='金卡'`（正负流水 + HAVING）；
- 「投诉未办结且满意度 ≤2 分的旅客名单」：service_requests→passengers
  （status + satisfaction 区间 + 可空 FK 反向关联）；
- 「各舱位平均票价对比 / 延误超 30 分钟航班的机场分布」：聚合 + airports 链。

播种内容：① 建表灌数（独立 PG 业务库，`CREATE TABLE IF NOT EXISTS` + 空表才灌，
确定性伪随机固定 seed，可复现；里程流水与 members.miles 严格对账）；② 建「数据源
本体·国航旅客运输」类别（写平台库；dialect+dsn 指向独立业务实例）+ 11 个表本体 +
全量字段属性（中文别名、枚举值、单位、格式）+ 15 条关系（类别内唯一编码 `rel_*`
+ join_condition 落库）；③ `--update` 刷新 alias/枚举/关系编码（对标
seed_chart_agent --update）；④ `--drop` 整体清理独立实例本域十一张业务表与
平台库类别后重建（只碰本域表，不动平台表）。

播种后 §0.2 差距全部可验证：「金卡会员本周乘坐过哪些延误超 30 分钟的航班」
一步出带 join 链的真实数据卡。

## 8. 降级矩阵

| 故障 | 行为 |
|---|---|
| 无数据源本体类别 / dialect 为空 | 静默跳过 NL2SQL，走老两级链（现状不变） |
| 子图检索 0 命中 | 回落 NL2Filter / 词频，节点 summary 说明「schema 未命中」 |
| LLM 未配置 / 生成失败 | 回落老链；不阻塞流水线 |
| 校验器拒绝（越权表/多语句） | 错误回灌重试 ≤2 → 仍败回落老链，绝不放行可疑 SQL |
| 执行超时 / 超行 | 超时视为失败走重试；超行截断出卡并标注「已截断至 500 行」 |
| 首轮生成/校验失败 | 第 2 轮回灌自动升 react 档（显式思考链，§4.6）；仍败回落老链 |
| 只读连接失败 | 记 error 事件，节点降级产出空事实卡 |

## 9. 安全边界

- **最小权限**：平台库连接走只读角色或会话级 `SET default_transaction_read_only=on`（PG）/ `?mode=ro` URI（sqlite 兜底库）；任何数据源有写权限直接拒连；
- **永不信任 LLM**：校验器是唯一放行人；黑名单+白名单+单语句+LIMIT 四道闸；
- **资源上限**：timeout 8s / 行数 500 / 重试 2 轮 / 单节点 30s 引擎超时不变；
- **可解释可审计**：每张数据卡带 SQL 与表关系链，OTel 埋点沿用 data 节点
  span（`agent.multi.node[data_agent]`），P1 补 `nl2sql.generate` 子 span。

## 10. 分期计划

| 期 | 内容 | 验收口径 |
|---|---|---|
| **P1**（单数据源闭环） | migration_042 + schema_store + nl2sql_service（方言适配：postgres 平台现役 + sqlite 测试兜底）+ 三级链接线 + 播种脚本 + 校验器 | §11 全过；老链行为不回归 |
| **P2** | mysql 方言、ToolAgent `data_query` 同源升级、独立 SSE `sql` 事件 + 过程区 tab、结果行集喂图表智能体成图 | 跨方言用例 + 图表联动 |
| **P3** | embedding 双路种子（黑话命中）、值采样入库（DISTINCT 采样写 enum_values）、指标节点（口径公式入图）、多数据源多类别 | 黑话问题命中率提升可量化 |

## 11. 验收清单（P1）

**schema 建模与检索**

- [ ] 播种脚本幂等：重复执行不重复建类别/表本体/关系；`--update` 可刷新别名
- [ ] 本体管理页可见「数据源本体·国航旅客运输」类别及 11 表/字段/关系；类别编辑含方言/DSN 字段（DSN 指向独立业务实例）
- [ ] 「旅客的会员等级」→ 检索出 passengers→members 2 跳链路；「两表无关联」的问题如实拒答
- [ ] 「CA1501 的机长是谁」→ 走 flights→crew_assignments→crew_members 桥 + `role='机长'` 出正确姓名（不误连 passengers 同名人员）

**SQL 生成与校验**

- [ ] §5.4 九个 few-shot 问题全部出正确数据卡（join/左连/反连接/聚合/EXISTS/CASE/桥+枚举/流水 HAVING/可空 FK）
- [ ] 可空 FK 全量问题：「统计服务请求总数」结果 = 表行数（验证可空 passenger_id 边未因 INNER JOIN 静默丢行）
- [ ] 「本月净增里程为正的金卡会员」与播种对账脚本输出一致（mileage_records SUM 对 members.miles 可对账）
- [ ] 构造攻击用例：`DROP TABLE`、双语句、越权表、`NOT IN (子查询)`、隐式 join——全部被校验器拦截且回灌重试后回落老链
- [ ] 无 LIMIT / LIMIT 99999 自动纠偏为 500；RIGHT JOIN：sqlite 档归一化为反向 LEFT JOIN，PG 档原生放行
- [ ] 默认播种（独立 PG 业务库 biz_aviation，自动建库）九问 + 攻击用例全过；`--target sqlite` CI 兜底同跑通过（方言矩阵 P1 项）

**三级链与回归**

- [ ] 无数据源类别环境：DataAgent 行为与 v6.3 完全一致（NL2Filter→词频，零回归）
- [ ] 故意改错业务表列名 → 重试 ≤2 后自动回落 NL2Filter 老路，SSE 过程说明可见
- [ ] 任务库「数据 · 台账统计」预置任务在新链下正常出卡；合成官引用 [事实] 无适配改动
- [ ] conda 环境 ontology 实测通过（无 pytest，用内联脚本验证校验器矩阵与三级链）
