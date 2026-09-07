# KnowSource 本体能力对标 Palantir Foundry：差距分析与补强设计

> 对标来源：Palantir Foundry Ontology 官方文档（[本体概述](https://www.palantir.com/docs/zh/foundry/ontologies/ontologies-overview/)、对象类型、链接类型、共享属性、值类型、动作类型、函数、接口、对象视图、对象监视器、Object Explorer、Ontology 提案、对象权限管理、对象后端）
> 现状基线：`doc/本体管理/design.md`、`本体管理功能实现计划.md`、`动态本体_功能设计.md`、`本体服务/本体服务_功能设计.md`
> 说明：项目当前权威库为 **PostgreSQL**（早期文档写作 SQLite）、图库为 **Neo4j**（早期文档写作 Kùzu），本文档按现状描述，新增表沿用现有约定：**`VARCHAR/TEXT/INTEGER` 类型、不使用数据库外键、关联由 service 层维护、建表语句追加到 `sql/schema.sql` 末尾（语句内不得含 `;`）**。

---

## 0. 结论摘要

### 0.1 一句话结论

本项目的本体目前是一套**「静态 schema + 初版动作」**：能定义类型与关系约束、能约束 LLM 抽取、能给本体/实体挂 Python 动作；但 Palantir 之所以把 Ontology 称为"操作系统的数字孪生"，靠的是另外三层能力——

1. **多态契约层（Interface / Shared property / Value type）**：让不同对象类型"长得一样"，从而被同一份代码、同一个视图、同一个动作统一处理；
2. **可编程能力层（Function + Action 的完整语义）**：Function 负责只读计算与派生属性，Action 负责带规则、校验、副作用、日志、可撤销的事务性写；
3. **运行时与治理层（Object Set / Object View / Monitor / 权限 / 提案与版本 / 影响分析）**：让本体从"抽取约束"升级为"可直接驱动应用的运行时"。

这三层正是本项目当前**最缺**的部分。

### 0.2 现有能力盘点（基线）

| 层 | 已有 | 对应实现 |
|---|---|---|
| 类型定义 | 本体类别、本体（类型）、属性、属性模板（含合并）、关系字典、三元组约束、KB 绑定 | `models.py` OntologyCategory/Ontology/OntologyAttribute/OntologyRelation/OntologyRelationConstraint/KbOntologyBinding/OntologyAttributeTemplate/OntologyTemplateAttribute/OntologyTemplateBinding |
| 抽取约束 | 约束模式 Prompt + 后处理过滤 + 属性规整 | `graph_extraction_service.py:929` / `:737` |
| 智能生成 | 自由抽取 → 本体建议 → 审核入库 | `ontology_suggestions`、`OntologySuggestionService` |
| 动作 | 本体服务/实体服务（`ontology_services`），本体→实体按 code 继承与覆盖，Python 子进程沙箱、AST import 白名单、参数校验、测试运行 | `ontology_action_service.py`、`services/service_runtime.py`、`routers/ontology_service.py` |
| 导入导出 | Excel 模板下载 / 导出 / 导入（含 dry_run） | `ontology_import_service.py` |
| 周边 | 图迁入、图计算分析（PageRank/介数/Louvain/相似度/度）、图推理补全建议、实体管理、智能体、工作流、定时调度 | `graph_sync_service.py`、`graph_analysis_service.py`、`graph_inference_service.py`、`entity_service.py`、`agent_service.py`、`workflow_engine.py`、`scheduler_service.py` |

### 0.3 建议优先级

| 优先级 | 主题 | 解决的痛点 | 预估工作量 |
|---|---|---|---|
| **P0-1** | 对象类型元数据 + 共享属性 + 值类型 | 属性各自为政、无法跨本体统一口径；本体无状态/无主键/无标题键 | 3~4 天 |
| **P0-2** | **接口（Interface）** | 缺多态：同一份视图/动作/函数无法跨本体复用 | 4~5 天 |
| **P0-3** | **函数（Function）+ 派生属性** | 只有"动作"没有"只读计算"；图计算分值（pagerank 等）无处定义、无法回写为属性 | 4~5 天 |
| P1-1 | 动作补强：规则/提交条件/副作用/执行日志/撤销/批量 | 动作只能"跑一段代码"，没有校验、通知、webhook、审计、回滚 | 5~6 天 |
| P1-2 | 链接类型升级：基数/反向/必需/链接属性/语义 | 关系只有名字，没有基数与反向语义，关系上不能带属性 | 3~4 天 |
| P1-3 | 对象视图（Object View）配置 | 实体详情页布局写死，不同领域需要不同展示 | 3 天 |
| P1-4 | 本体版本 + 提案 + 影响分析 + 回滚 | 改一个本体不知道会影响什么，改错了没有退路 | 4~5 天 |
| P2-1 | 权限与受限视图（对象/实例/属性级） | 无任何本体级权限 | 4 天 |
| P2-2 | 对象集（Object Set）与聚合查询 | 只能列表筛选，没有"可保存的对象集"与聚合 | 3~4 天 |
| P2-3 | 对象监视器（Object Monitor） | 本体数据变化无法主动告警/触发动作 | 3 天 |
| P2-4 | 本体包（JSON 导入导出）/ OSDK 与智能体工具清单 | 已有 Excel 导入导出，缺机器可读包与给 LLM 的工具清单 | 2~3 天 |

---

## 1. Palantir 本体能力全景

```
┌─ 类型层（What it is）─────────────────────────────────────────────┐
│  Object type（主键/标题键/显示名/图标/状态/类型类/渲染提示/类型组）   │
│  Property（数据类型、格式化、条件格式化、必填、仅编辑属性、渲染提示）  │
│  Shared property（跨对象类型共享定义、值各自独立）                  │
│  Value type / Struct type（受控值域与版本、结构体属性）             │
│  Link type（一对多/多对多、必需性、反向链接、链接可带属性）          │
│  Interface（共享属性 + 接口链接 + 实现/扩展 → 多态）                │
├─ 行为层（What it can do）──────────────────────────────────────────┤
│  Action type（参数、规则、提交条件、Function-backed、副作用         │
│              [通知/Webhook]、权限、撤销、操作日志、操作指标、行内编辑）│
│  Function（TS/Python、只读计算、绑定对象集、派生属性、语义搜索、     │
│            Ontology 编辑、模型与图、单元测试、API 网关）            │
├─ 运行时层（How it is used）────────────────────────────────────────┤
│  Object Set（搜索/筛选/聚合/保存）/ Object View（可配置详情页）      │
│  Object Explorer / Vertex（图谱）/ Map / 时间序列与事件              │
│  Object Monitor（条件 → 评估 → 通知/动作）                          │
│  Foundry Rules / Dynamic Scheduling / AIP Logic                     │
├─ 治理层（How it is governed）──────────────────────────────────────┤
│  Ontology 提案（分支 + 审查 + 合并）/ 变更记录与回滚                 │
│  Ontology 角色与权限 / 共享 Ontology / 受限视图                      │
│  Ontology 指标与使用分析 / Marketplace 打包与安装                    │
├─ 存储层（How it is backed）────────────────────────────────────────┤
│  Object Storage v2 / Object Set Service / 对象索引 / 编辑历史 / 物化 │
└────────────────────────────────────────────────────────────────────┘
```

> 取舍提示：Foundry 的强项里，**数据源绑定与物化、多组织共享本体、Marketplace、地图/动态调度/流程挖掘**依赖其数据平台底座，本项目（单机 RAG+KG 系统）不必照抄；真正值得搬的是**接口、函数、动作的完整语义、对象视图、提案/版本、影响分析**这几项。

---

## 2. 术语映射（Palantir ↔ 本项目）

| Palantir | 本项目对应 | 现状 |
|---|---|---|
| Ontology | 本体类别 `ontology_categories` | ✅ |
| Object type | 本体 `ontologies` | ✅（缺元数据字段） |
| Object | 实体 `entities` | ✅ |
| Property | 本体属性 `ontology_attributes` | ✅（缺共享属性/值类型/仅编辑） |
| Shared property | — | ❌ 由属性模板近似替代（语义不同） |
| Value type / Struct type | `data_type` + `enum_values` | ⚠️ 只有基础类型与内联枚举 |
| Link type | 关系定义 + 三元组 `ontology_relations` + `ontology_relation_constraints` | ⚠️ 缺基数/反向/链接属性 |
| **Interface** | — | ❌ 完全缺失 |
| **Function** | — | ❌ 完全缺失 |
| Action type | 本体服务 `ontology_services` | ⚠️ 有执行器，缺规则/副作用/日志/撤销 |
| Object View | 实体详情页（写死布局） | ⚠️ 不可配置 |
| Object Set | 实体列表筛选 | ⚠️ 无对象集与聚合 |
| Object Explorer | 实体管理页 | ⚠️ 无保存的探索/对比 |
| Object Monitor | 系统监控（非本体级） | ❌ 本体级监视缺失 |
| Ontology Proposals | 本体建议 `ontology_suggestions` | ⚠️ 只有"生成建议"，无分支/审查/合并/回滚 |
| 对象权限 / 受限视图 | — | ❌ |
| Ontology SDK / Marketplace | Excel 导入导出 | ⚠️ 缺机器可读包与 SDK |

---

## 3. 逐项对标（差距明细）

| # | 能力 | Palantir 做法 | 本项目现状 | 差距 | 优先级 | 建议动作 |
|---|---|---|---|---|---|---|
| 1 | 对象类型元数据 | 主键、标题键、显示名/复数名、图标、状态（草稿/活跃/弃用）、类型类、渲染提示、类型组 | 仅 name/description/color/sort_order | 无主键与标题语义，无生命周期状态 | P0 | 扩 `ontologies` 字段 |
| 2 | 属性能力 | 数据类型、格式化、条件格式化、必填、**仅编辑属性**、渲染提示 | name/code/data_type/is_required/default/enum_values | 缺"仅编辑"（不参与抽取、只人工填）、渲染与格式化 | P0 | 扩 `ontology_attributes` |
| 3 | 共享属性 | 一处定义、多类型实现，值各自独立，改元数据全局生效 | 用属性模板"拷贝"代替 | 模板是复制，改模板不改已引用本体的历史值；接口所需的"契约"语义缺失 | P0 | 新增 `ontology_shared_properties` |
| 4 | 值类型 / 结构类型 | 受控值域、版本、权限、约束；结构体属性 | 无 | 无单位/格式/受控字典与版本 | P2 | 新增 `ontology_value_types`（二期） |
| 5 | **接口** | 共享属性 + 接口链接 + 实现/扩展 → 多态查询与统一交互 | 无 | 完全缺失 | P0 | 新增接口四表（见 §4.2） |
| 6 | **函数** | 只读计算、派生属性、语义搜索、模型/图、单元测试 | 无（只有动作） | 无只读计算层，图算法分值无定义位 | P0 | 新增 `ontology_functions` + 派生属性 |
| 7 | 动作-规则/提交条件 | 参数校验规则、提交条件、提交前/后动作 | 只有参数类型校验 | 无业务规则与提交条件 | P1 | 新增 `ontology_service_rules` |
| 8 | 动作-副作用 | 通知、Webhook、Ontology 编辑、创建链接 | 无（通知渠道仅 v1 骨架） | 动作无法驱动后续流程 | P1 | 新增 `ontology_service_effects` |
| 9 | 动作-日志/撤销/指标 | 操作日志、撤销、操作指标 | 仅返回耗时，无持久化 | 无审计、无回滚 | P1 | 新增 `ontology_service_invocations` |
| 10 | 动作-权限/行内编辑 | 动作级权限、列表行内触发 | 无 | 无权限 | P2 | 与 P2-1 权限体系合并做 |
| 11 | 链接基数与反向 | 1:1 / 1:N / N:M、必需性、反向链接名 | 三元仅有方向 | 无基数、无反向语义，图谱交互与推理易歧义 | P1 | 扩 `ontology_relations` + `constraints` |
| 12 | 链接属性 | 链接本身可带属性（时间区间、权重、置信度） | 关系仅 description | 无法表达"何时/多强" | P1 | 新增关系属性定义 + `relations.properties` |
| 13 | 对象视图 | 可配置详情页：标签页/分区/微件/版本 | 实体详情页布局写死 | 不同领域无法定制 | P1 | 新增 `ontology_object_views` |
| 14 | 对象集与聚合 | 对象集 DSL、筛选/聚合/排序/保存 | 列表分页筛选 | 无聚合与保存视图 | P2 | 新增对象集查询服务 |
| 15 | 对象浏览器/图谱 | 搜索语法、透视关联、对比对象集、保存探索 | 图谱可视化 + 图计算榜单 | 无保存探索/对比 | P2 | 复用图分析 + 保存视图 |
| 16 | 对象监视器 | 条件 → 评估 → 通知/触发动作 | 无本体级监视（有系统与调度监控） | 数据变化无主动响应 | P2 | 新增 `ontology_monitors` |
| 17 | 本体提案/版本 | 分支、审查、合并、变更记录、回滚 | 只有建议审核，无版本与回滚 | 改错无退路 | P1 | 新增 `ontology_versions` + 提案化 `ontology_suggestions` |
| 18 | 影响分析 | Usages：改一个资源影响哪些下游 | 删除时仅做级联 | 无影响面预览 | P1 | 新增 usages 查询接口 |
| 19 | 权限 | 对象类型/实例级权限、受限视图（属性级脱敏） | 无 | 无 | P2 | 新增权限表 + 受限视图 |
| 20 | 导入导出/市场 | 导出/编辑/导入 Ontology、Marketplace 打包 | Excel 导入导出 | 缺机器可读包与"模板市场" | P2 | 新增本体包 JSON + 预置包 |
| 21 | 编辑历史/物化 | 用户编辑历史、物化到数据集、模式变更管理 | 实体有 updated_at，无变更史 | 无法追溯"谁改了什么" | P2 | 新增 `entity_change_logs` |
| 22 | 本体指标 | Ontology 指标与使用分析 | 无 | 无法看出哪些本体/关系在用、空转 | P2 | 统计接口（可复用 `EntityService.stats`） |

---

## 4. 补强设计

### 4.1 P0-1 对象类型元数据升级 + 共享属性

#### 目标
让"本体"从"一个名字 + 一组属性"变成有身份、有生命周期、属性可跨类型统一口径的一等公民。

#### 数据模型

```sql
-- 1) ontologies 扩列（ALTER 追加到 sql/migrations.sql）
ALTER TABLE ontologies ADD COLUMN code            VARCHAR(64)  DEFAULT '';   -- 类型 API 名，如 person_org
ALTER TABLE ontologies ADD COLUMN title_key       VARCHAR(64)  DEFAULT '';   -- 标题属性名（列表/图谱默认显示）
ALTER TABLE ontologies ADD COLUMN primary_key     VARCHAR(64)  DEFAULT 'name'; -- 主键属性名，默认 name
ALTER TABLE ontologies ADD COLUMN display_name    VARCHAR(100) DEFAULT '';   -- 显示名（可与 name 不同）
ALTER TABLE ontologies ADD COLUMN plural_name     VARCHAR(100) DEFAULT '';
ALTER TABLE ontologies ADD COLUMN icon            VARCHAR(64)  DEFAULT '';
ALTER TABLE ontologies ADD COLUMN status          VARCHAR(20)  DEFAULT 'active'; -- draft/active/deprecated
ALTER TABLE ontologies ADD COLUMN visibility      VARCHAR(20)  DEFAULT 'public'; -- public/restricted(预留 P2)
ALTER TABLE ontologies ADD COLUMN group_name      VARCHAR(100) DEFAULT '';   -- 对象类型组（前端分组展示）

-- 2) ontology_attributes 扩列
ALTER TABLE ontology_attributes ADD COLUMN is_edit_only INTEGER NOT NULL DEFAULT 0;   -- 仅人工编辑，不进抽取 Prompt
ALTER TABLE ontology_attributes ADD COLUMN render_hint  VARCHAR(50)  DEFAULT '';      -- text/textarea/tag/link/image/badge
ALTER TABLE ontology_attributes ADD COLUMN format       VARCHAR(100) DEFAULT '';      -- 值格式化：#,##0.00 / YYYY-MM-DD
ALTER TABLE ontology_attributes ADD COLUMN unit         VARCHAR(32)  DEFAULT '';
ALTER TABLE ontology_attributes ADD COLUMN shared_property_id VARCHAR DEFAULT '';     -- 关联的共享属性（可空）

-- 3) 共享属性（跨本体统一定义，值各自独立）
CREATE TABLE IF NOT EXISTS ontology_shared_properties (
    id            VARCHAR PRIMARY KEY,
    name          VARCHAR(50)  NOT NULL,
    code          VARCHAR(64)  NOT NULL,
    data_type     VARCHAR(20)  NOT NULL,
    description   VARCHAR(500) DEFAULT '',
    is_required   INTEGER      NOT NULL DEFAULT 0,
    default_value VARCHAR(200) DEFAULT NULL,
    enum_values   TEXT         DEFAULT NULL,
    unit          VARCHAR(32)  DEFAULT '',
    format        VARCHAR(100) DEFAULT '',
    is_system     INTEGER      NOT NULL DEFAULT 0,
    created_at    VARCHAR,
    updated_at    VARCHAR,
    UNIQUE(code)
);
```

#### 语义要点
- **共享属性 ≠ 属性模板**：模板是"定义拷贝"（改模板只影响新引用与合并结果），共享属性是"契约引用"（改共享属性的元数据/枚举，所有引用它的本体属性立即生效，值仍各自存储）。
- **title_key**：抽取与展示的默认标题字段——图谱节点标签、实体列表主标题、LLM 消歧都用它；缺省回落 `name`。
- **status**：`deprecated` 的本体不再进入抽取 Prompt 白名单，但历史实体可查（避免"删了就没了"）。

#### API
```
GET|POST       /api/shared-properties
PUT|DELETE     /api/shared-properties/{id}
POST           /api/shared-properties/{id}/apply      {ontology_ids: [], overwrite: false}  # 批量挂到本体
GET            /api/ontology-categories/{cid}/ontologies/{oid}         # 返回含 title_key/status/icon 等
```

#### 前端
- 本体编辑卡片新增「基本信息」区：code / 显示名 / 复数名 / 图标 / 状态 / 分组 / 标题属性下拉。
- 属性行新增：仅编辑（开关）、渲染提示、单位、格式化、"绑定共享属性"下拉（绑定后名称与类型被锁定，标灰色）。
- 独立菜单「共享属性」（与「属性模板」并列，需说明两者差异）。

#### 验收
1. 建共享属性 `risk_level(enum:低/中/高)` 并挂到两个本体 → 改枚举值 → 两个本体的抽取 Prompt 与校验同步变化；
2. 本体置为 `deprecated` → 新文件抽取不再产出该类型，历史实体仍可查。

---

### 4.2 P0-2 接口（Interface）

#### 目标
提供**多态**：不同本体只要实现同一接口，就能被同一份对象视图、同一个动作、同一个函数、同一段查询统一处理——这是 Palantir Interface 的核心价值，也是本项目从"schema 工具"走向"可编程对象模型"的关键一步。

#### 概念（对照 Palantir）
- 接口是**抽象**的：只有属性/链接契约，没有数据、不能实例化（本项目：不产生实体）。
- 接口分两类：
  - **功能接口**：表达一种能力（如「可监控设备」「可调度资源」），属性少而专；
  - **抽象对象接口**：多个本体的超类型（如「参与方」← 人物/组织/机构）。
- 接口可 `extends` 接口（组合），本体可 `implements` 多个接口。
- 引用方式：**本体属性名 + 接口属性名都可用**（`e.name` 与 `party.name` 指向同一值）。

#### 数据模型

```sql
-- 接口定义
CREATE TABLE IF NOT EXISTS ontology_interfaces (
    id            VARCHAR PRIMARY KEY,
    category_id   VARCHAR NOT NULL,           -- 归属本体类别
    name          VARCHAR(100) NOT NULL,
    code          VARCHAR(64)  NOT NULL,      -- party / monitorable / schedulable
    description   VARCHAR(500) DEFAULT '',
    icon          VARCHAR(64)  DEFAULT '',
    extends       TEXT  DEFAULT NULL,         -- JSON 数组：[interface_id...]
    interface_kind VARCHAR(20) DEFAULT 'functional',  -- functional / abstract_object
    is_system     INTEGER NOT NULL DEFAULT 0,
    created_at    VARCHAR,
    updated_at    VARCHAR,
    UNIQUE(category_id, code)
);

-- 接口属性契约（共享属性在接口上的投影）
CREATE TABLE IF NOT EXISTS ontology_interface_properties (
    id                  VARCHAR PRIMARY KEY,
    interface_id        VARCHAR NOT NULL,
    name                VARCHAR(50) NOT NULL,
    code                VARCHAR(64) NOT NULL,
    data_type           VARCHAR(20) NOT NULL,
    description         VARCHAR(500) DEFAULT '',
    is_required         INTEGER NOT NULL DEFAULT 1,
    default_value       VARCHAR(200) DEFAULT NULL,
    enum_values         TEXT DEFAULT NULL,
    shared_property_id  VARCHAR DEFAULT '',   -- 可选：直接复用共享属性定义
    sort_order          INTEGER NOT NULL DEFAULT 0,
    UNIQUE(interface_id, code)
);

-- 接口链接契约（P1，接口间的链接）
CREATE TABLE IF NOT EXISTS ontology_interface_links (
    id                    VARCHAR PRIMARY KEY,
    interface_id          VARCHAR NOT NULL,
    name                  VARCHAR(50) NOT NULL,
    code                  VARCHAR(64) NOT NULL,
    target_interface_id   VARCHAR,            -- 可空：指向具体本体时留空并用 target_ontology_id
    target_ontology_id    VARCHAR DEFAULT '',
    cardinality           VARCHAR(10) DEFAULT 'ONE_TO_MANY',  -- ONE_TO_ONE/ONE_TO_MANY/MANY_TO_MANY
    is_required           INTEGER NOT NULL DEFAULT 0,
    UNIQUE(interface_id, code)
);

-- 本体实现接口
CREATE TABLE IF NOT EXISTS ontology_interface_implementations (
    id              VARCHAR PRIMARY KEY,
    interface_id    VARCHAR NOT NULL,
    ontology_id     VARCHAR NOT NULL,
    property_mapping TEXT NOT NULL,           -- JSON: {接口属性code: 本体属性id 或 本体属性名}
    link_mapping     TEXT DEFAULT NULL,       -- JSON: {接口链接code: relation_id}
    status          VARCHAR(20) DEFAULT 'active',   -- active / partial（缺必填映射）
    created_at      VARCHAR,
    UNIQUE(interface_id, ontology_id)
);
```

#### 核心服务逻辑
```python
class OntologyInterfaceService:
    async def validate_implementation(db, interface_id, ontology_id, mapping) -> dict:
        """校验：接口必填属性是否都已映射、类型是否兼容 → 返回 {ok, missing[], type_mismatch[]}"""

    async def resolve_by_interface(db, category_id, interface_code) -> dict:
        """接口视图解析：返回实现该接口的全部本体 + 统一的属性/链接别名表"""

    async def query_interface_objects(db, category_id, interface_code, filters, paging) -> dict:
        """按接口查询对象：把各本体的本地属性按别名投影成接口属性，返回统一结构"""
```

#### 与属性模板的区别（务必在 UI 上写清楚）

|  | 属性模板 | 接口 |
|---|---|---|
| 本质 | 定义**拷贝**（编译期） | 定义**契约**（运行期） |
| 改模板 | 影响合并结果，历史本体属性已是副本 | 改接口元数据，所有实现本体立即生效 |
| 能否被查询寻址 | 否 | 是（`按接口查对象`） |
| 能否驱动视图/动作/函数 | 否 | 是（一份视图面向接口编写） |
| 关系 | 只管属性 | 管属性 + 链接 |

#### API
```
GET|POST      /api/ontology-categories/{cid}/interfaces
GET|PUT|DELETE /api/interfaces/{iid}
GET|PUT       /api/interfaces/{iid}/properties
GET|PUT       /api/interfaces/{iid}/links
POST          /api/interfaces/{iid}/implementations            {ontology_id, property_mapping}
DELETE        /api/interfaces/{iid}/implementations/{ontology_id}
POST          /api/interfaces/{iid}/implementations/validate   # 校验映射完整性
GET           /api/ontology-categories/{cid}/interfaces/{code}/objects   # 按接口查询对象（多态）
GET           /api/ontology-categories/{cid}/ontologies/{oid}/interfaces # 某本体实现的接口
```

#### 前端
- 本体类别详情页新增 **Tab「接口」**：接口列表 → 接口编辑（属性契约 + 链接契约 + 继承 extends）→「实现情况」面板（哪些本体实现了、映射是否完整）。
- 本体编辑卡片新增「实现的接口」区：勾选接口 → 逐属性下拉映射到本体属性 → 校验提示。
- 实体列表/图谱增加"按接口筛选"。

#### 验收
1. 定义接口 `参与方`（属性：名称/别名）+ `可监控设备`（属性：设备编号/状态）；
2. 人物与组织实现 `参与方`，设备实现 `可监控设备`；
3. `GET /interfaces/party/objects` 能一次性返回两类实体，字段统一投影为 `name`；
4. 给 `参与方` 配一个动作/视图 → 人物与组织都能直接用，无需各配一份。

---

### 4.3 P0-3 函数（Function）+ 派生属性

#### 目标
补上"只读计算"这一层：**函数不写数据、可缓存、可被动作/视图/派生属性/智能体/提示词复用**；并把已有的图计算分值（PageRank/介数/社区/度）纳入统一的"派生属性"体系，成为可声明、可物化、可查询的本体属性。

#### 与动作的分工（对照 Palantir）
|  | Function | Action |
|---|---|---|
| 是否写数据 | 否（只读；写走显式 Ontology Edit） | 是（事务） |
| 触发方式 | 被动作/视图/派生属性/智能体/API 网关调用 | 用户点击或自动化触发 |
| 是否有副作用 | 无 | 有（通知、Webhook、写回） |
| 是否可缓存 | 是（确定性 + TTL） | 否 |
| 日志/撤销 | 轻量 | 完整（P1-1） |
| 典型例子 | 计算风险分、聚合子图指标、调用外部 API 查询 | 修改实体属性、创建关系、发起审批 |

#### 数据模型

```sql
-- 函数（只读计算）
CREATE TABLE IF NOT EXISTS ontology_functions (
    id                VARCHAR PRIMARY KEY,
    category_id       VARCHAR DEFAULT '',     -- 空 = 全局函数
    ontology_id       VARCHAR DEFAULT '',     -- 空 = 类别级/全局；有值 = 挂在本体下（可被实体继承）
    name              VARCHAR(100) NOT NULL,
    code              VARCHAR(64)  NOT NULL,  -- risk.score / graph.neighbor_count
    description       VARCHAR(500) DEFAULT '',
    params_schema     TEXT DEFAULT NULL,      -- JSON 数组
    return_schema     TEXT DEFAULT NULL,      -- JSON：返回结构说明（供前端与 LLM 理解）
    code_text         TEXT NOT NULL,          -- Python 源码
    language          VARCHAR(20) DEFAULT 'python',
    timeout_seconds   INTEGER NOT NULL DEFAULT 30,
    is_deterministic  INTEGER NOT NULL DEFAULT 1,   -- 确定性 → 允许缓存
    cache_seconds     INTEGER NOT NULL DEFAULT 0,   -- 0 = 不缓存
    is_enabled        INTEGER NOT NULL DEFAULT 1,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        VARCHAR,
    updated_at        VARCHAR
);

-- 派生属性（属性来源：函数 或 图计算指标）
CREATE TABLE IF NOT EXISTS ontology_derived_properties (
    id                   VARCHAR PRIMARY KEY,
    ontology_id          VARCHAR NOT NULL,
    name                 VARCHAR(50) NOT NULL,
    code                 VARCHAR(64) NOT NULL,
    data_type            VARCHAR(20) NOT NULL,     -- number/string/enum...
    source_kind          VARCHAR(20) NOT NULL,     -- function / graph_metric
    function_id          VARCHAR DEFAULT '',       -- source_kind=function
    graph_metric         VARCHAR(32) DEFAULT '',   -- pagerank/betweenness/community/degree
    params               TEXT DEFAULT NULL,        -- JSON：函数入参或图算法参数
    materialize_mode     VARCHAR(20) DEFAULT 'virtual',  -- virtual（实时算）/ materialized（物化写入）
    last_materialized_at VARCHAR DEFAULT '',
    is_enabled           INTEGER NOT NULL DEFAULT 1,
    sort_order           INTEGER NOT NULL DEFAULT 0,
    created_at           VARCHAR,
    UNIQUE(ontology_id, code)
);

-- 函数/动作调用记录（取代"一期不落调用历史"的取舍，函数与动作共用）
CREATE TABLE IF NOT EXISTS ontology_runtime_invocations (
    id            VARCHAR PRIMARY KEY,
    kind          VARCHAR(10) NOT NULL,      -- function / action
    ref_id        VARCHAR NOT NULL,          -- function_id 或 service_id
    entity_id     VARCHAR DEFAULT '',
    params        TEXT DEFAULT NULL,
    result        TEXT DEFAULT NULL,         -- JSON（截断存储）
    status        VARCHAR(20) NOT NULL,      -- success / error / timeout
    error         TEXT DEFAULT NULL,
    duration_ms   INTEGER DEFAULT 0,
    triggered_by  VARCHAR(64) DEFAULT '',    -- user / agent / schedule / action
    created_at    VARCHAR
);
```

#### 执行契约（复用现有沙箱 `services/service_runtime.py`）
```python
def run(params: dict, entity: dict, context: dict) -> dict:
    """与动作同一套 runner：AST import 白名单 + 子进程 + 超时 + 输出截断。
    context: {kb_id, category_id, function_code, triggered_by}
    """
```
- 图计算类派生属性**不写代码**：`source_kind='graph_metric'` 时由 `graph_analysis_service` 的既有结果回填（Neo4j 上的 `pagerank_score`/`betweenness_score`/`community_id` 已是写回属性），派生属性只是把它**声明为本体属性**，从而可在实体详情页/对象视图/接口投影中统一呈现。

#### API
```
GET|POST       /api/ontology-categories/{cid}/functions
GET|PUT|DELETE /api/functions/{fid}
POST           /api/functions/{fid}/test                  {params, mock_entity?}
POST           /api/entities/{eid}/functions/{fid}/invoke  {params}
POST           /api/functions/{fid}/resolve                # 批量解析（对象集/视图用）

GET|POST       /api/ontology-categories/{cid}/ontologies/{oid}/derived-properties
PUT|DELETE     /api/derived-properties/{id}
POST           /api/derived-properties/{id}/materialize    # 手动/调度物化（写入 entities.properties）
```

#### 前端
- 本体编辑卡片新增「函数」与「派生属性」两个区块；
- 实体详情页「派生属性」分组：实时值 + 最近计算时间 + 手动刷新；
- 函数编辑器复用 `ServiceEditorDialog.vue`（改称 `CodeEditorDialog.vue`），增加返回结构说明与缓存设置。

#### 验收
1. 定义函数 `risk.score`（读实体属性 + 调外部 API）→ 实体详情页一键计算并展示；
2. 定义派生属性 `核心度`（图指标 pagerank）→ 实体列表可按它排序、可加入对象视图；
3. 一个动作内部调用同一个函数（函数被动作与视图复用）。

---

### 4.4 P1-1 动作（Action）补强

现有动作（`ontology_services`）已有：CRUD、继承覆盖、参数校验、沙箱执行、测试运行、AI 辅助写代码。缺的是 Palantir 动作类型的"工程化四件套"。

```sql
-- 1) 规则：提交条件（precondition）/ 参数校验（validation）/ 提交后动作（post）
CREATE TABLE IF NOT EXISTS ontology_service_rules (
    id            VARCHAR PRIMARY KEY,
    service_id    VARCHAR NOT NULL,
    rule_type     VARCHAR(20) NOT NULL,   -- precondition / validation / post
    expression    TEXT NOT NULL,          -- JSON：{field, op, value} 树 或 {kind:'python', code}
    error_message VARCHAR(300) DEFAULT '',
    sort_order    INTEGER NOT NULL DEFAULT 0,
    is_enabled    INTEGER NOT NULL DEFAULT 1
);

-- 2) 副作用：通知 / Webhook / 写回本体（改属性/建关系）
CREATE TABLE IF NOT EXISTS ontology_service_effects (
    id          VARCHAR PRIMARY KEY,
    service_id  VARCHAR NOT NULL,
    effect_type VARCHAR(20) NOT NULL,     -- notify / webhook / update_property / create_relation
    config      TEXT NOT NULL,            -- JSON：渠道与模板，或 {property_code, value_expr}，或 {relation, target_expr}
    is_enabled  INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- 3) 动作执行记录（撤销数据 + 审计，见 4.3 的 ontology_runtime_invocations 亦可，此处独立便于撤销）
CREATE TABLE IF NOT EXISTS ontology_service_invocations (
    id            VARCHAR PRIMARY KEY,
    service_id    VARCHAR NOT NULL,
    entity_id     VARCHAR DEFAULT '',
    params        TEXT DEFAULT NULL,
    result        TEXT DEFAULT NULL,
    status        VARCHAR(20) NOT NULL,   -- success / error / timeout / undone
    error         TEXT DEFAULT NULL,
    duration_ms   INTEGER DEFAULT 0,
    undo_payload  TEXT DEFAULT NULL,      -- JSON：撤销所需的前像（属性旧值/新建的关系 id）
    triggered_by  VARCHAR(64) DEFAULT '',
    undone_at     VARCHAR DEFAULT '',
    undone_by     VARCHAR(64) DEFAULT '',
    created_at    VARCHAR
);
```

要点：
- **规则表达式**建议直接复用 `workflow_engine.py` 已有的规则树求值（`:331 _eval_rule_tree`），避免第二套语法；
- **撤销（Undo）**：仅对 `effect_type='update_property'/'create_relation'` 的写回型动作支持，执行前存前像到 `undo_payload`，`POST /invocations/{id}/undo` 回滚；
- **批量**：`POST /api/ontology-services/{id}/batch-invoke {entity_ids: []}`，逐条执行并汇总成功/失败；
- **写回本体（Ontology edits）**：动作返回结构支持声明式写回 `{edits: [{op:'set_property'|'add_relation', ...}]}`，由后端统一落库 + 同步 Neo4j（复用 `entity_service` 双写），避免每个动作自己拼 SQL。

API 增量：
```
GET|POST   /api/ontology-services/{id}/rules
PUT|DELETE /api/ontology-services/{id}/rules/{rid}
GET|POST   /api/ontology-services/{id}/effects
PUT|DELETE /api/ontology-services/{id}/effects/{eid}
GET        /api/ontology-services/{id}/invocations
POST       /api/ontology-services/invocations/{iid}/undo
POST       /api/ontology-services/{id}/batch-invoke
```

---

### 4.5 P1-2 链接类型升级

```sql
-- 关系定义扩列（方向语义 + 反关系 + 基数）
ALTER TABLE ontology_relations ADD COLUMN cardinality   VARCHAR(10) DEFAULT 'MANY_TO_MANY';
ALTER TABLE ontology_relations ADD COLUMN inverse_name  VARCHAR(50) DEFAULT '';   -- 反向展示名：任职于 ↔ 雇佣
ALTER TABLE ontology_relations ADD COLUMN is_symmetric  INTEGER NOT NULL DEFAULT 0;   -- 如"合作"
ALTER TABLE ontology_relations ADD COLUMN is_transitive INTEGER NOT NULL DEFAULT 0;   -- 如"位于/属于"
ALTER TABLE ontology_relations ADD COLUMN status        VARCHAR(20) DEFAULT 'active';

-- 三元组扩列（端点约束）
ALTER TABLE ontology_relation_constraints ADD COLUMN source_min INTEGER DEFAULT 0;
ALTER TABLE ontology_relation_constraints ADD COLUMN source_max INTEGER DEFAULT 0;  -- 0 = 不限
ALTER TABLE ontology_relation_constraints ADD COLUMN target_min INTEGER DEFAULT 0;
ALTER TABLE ontology_relation_constraints ADD COLUMN target_max INTEGER DEFAULT 0;
ALTER TABLE ontology_relation_constraints ADD COLUMN is_required INTEGER NOT NULL DEFAULT 0;  -- 该起点必须存在此关系

-- 关系属性定义（链接本身可带属性：时间区间、权重、置信度、来源）
CREATE TABLE IF NOT EXISTS ontology_relation_properties (
    id            VARCHAR PRIMARY KEY,
    relation_id   VARCHAR NOT NULL,        -- 逻辑关联 ontology_relations.id
    name          VARCHAR(50) NOT NULL,
    code          VARCHAR(64) NOT NULL,
    data_type     VARCHAR(20) NOT NULL,
    description   VARCHAR(500) DEFAULT '',
    is_required   INTEGER NOT NULL DEFAULT 0,
    enum_values   TEXT DEFAULT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(relation_id, code)
);

-- 关系实例属性存储
ALTER TABLE relations ADD COLUMN properties TEXT DEFAULT NULL;   -- JSON
```

收益：
- 图谱可视化的边可带"生效时间/权重"，支撑时间序列与加权图算法；
- 推理规则可利用 `is_transitive` / `is_symmetric` 做一阶推理（如"属于"传递 → 补全间接归属）；
- 抽取校验可检查 `source_max`（如"一个人只能有一个主键组织"）。

API 增量：`/api/ontology-categories/{cid}/relations/{rid}/properties` CRUD；三元组接口返回基数与反向名。

---

### 4.6 P1-3 对象视图（Object View）

```sql
CREATE TABLE IF NOT EXISTS ontology_object_views (
    id           VARCHAR PRIMARY KEY,
    category_id  VARCHAR NOT NULL,
    ontology_id  VARCHAR DEFAULT '',    -- 空 = 该类别的缺省视图
    name         VARCHAR(100) NOT NULL,
    layout       TEXT NOT NULL,         -- JSON: {tabs:[{name, sections:[{title, widgets:[...]}]}]}
    is_default   INTEGER NOT NULL DEFAULT 0,
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   VARCHAR,
    updated_at   VARCHAR
);
```

`layout` 支持的 widget 类型（一期）：`properties`（属性分组）、`relations`（关系表，按关系类型/方向）、`actions`（动作按钮区）、`derived`（派生属性/图指标）、`chart`（简单柱状/饼图，基于对象集聚合）、`timeline`（基于关系的时间属性，P2）。

- 实体详情页 `EntityDetailPage.vue` 改为**按 layout 渲染**，无 layout 时回落到当前固定布局（向后兼容）；
- 提供「视图编辑器」：左侧组件面板 + 中间预览 + 右侧属性配置；
- 接口级视图：`ontology_id` 为空且绑定 `interface_code` 时，作为该接口所有实现本体的统一视图（与 P0-2 联动）。

---

### 4.7 P1-4 本体版本 + 提案 + 影响分析

```sql
-- 版本快照（每次发布一个不可变快照）
CREATE TABLE IF NOT EXISTS ontology_versions (
    id           VARCHAR PRIMARY KEY,
    category_id  VARCHAR NOT NULL,
    version_no   INTEGER NOT NULL,
    snapshot     TEXT NOT NULL,          -- JSON：类别下全部本体/属性/关系/三元组/接口/视图
    source       VARCHAR(20) DEFAULT 'manual',   -- manual / auto_before_change / proposal
    note         VARCHAR(1000) DEFAULT '',
    created_by   VARCHAR(64) DEFAULT '',
    created_at   VARCHAR,
    UNIQUE(category_id, version_no)
);

-- 提案（把 ontology_suggestions 升级为通用变更提案）
ALTER TABLE ontology_suggestions ADD COLUMN proposal_type VARCHAR(20) DEFAULT 'suggestion'; -- suggestion / change
ALTER TABLE ontology_suggestions ADD COLUMN base_version  INTEGER DEFAULT 0;
ALTER TABLE ontology_suggestions ADD COLUMN diff          TEXT DEFAULT NULL;  -- JSON：变更项列表
ALTER TABLE ontology_suggestions ADD COLUMN reviewers     TEXT DEFAULT '';
ALTER TABLE ontology_suggestions ADD COLUMN merged_version_id VARCHAR DEFAULT '';
```

能力：
1. **影响分析（Usages）**：`GET /api/ontology-categories/{cid}/ontologies/{oid}/usages` 返回——引用它的三元组数、实现的接口、挂载的动作/函数/派生属性、使用的对象视图、已抽取实体数、绑定该类别的知识库。**删除/改名前置展示**。
2. **发布版本**：手动或变更自动打快照（改前自动存一份 `auto_before_change`），支持"回滚到 v3"。
3. **提案流**：草稿 → 请求审查 → 逐任务批准/拒绝 → 合并（合并时自动打新版本）；关闭不合并。与现有"本体建议审核"共用一张表、两套 `proposal_type`。

---

### 4.8 P2 项（概要）

| 项 | 设计要点 |
|---|---|
| 权限与受限视图 | 三张表：`ontology_permissions(subject_type, subject_id, resource_type, resource_id, role)`、`attribute_visibility(ontology_id, attribute_id, level: full/masked/hidden)`；读路径在 service 层做属性级脱敏；动作/函数级 `can_execute`。一期可只做"受限属性标记 + 列表脱敏"，不做完整 RBAC。 |
| 对象集与聚合 | `POST /api/object-set` 收 `{filters:[{field,op,value}], group_by, aggs:[{op:count/sum/avg, field}], sort, paging}`；`ontology_saved_sets` 保存命名对象集；前端「探索」页支持保存/对比/导出 CSV。 |
| 对象监视器 | `ontology_monitors(id, category_id, ontology_id, condition JSON, actions JSON, schedule, last_evaluated_at, status)`；复用 `scheduler_engine` 定时评估，命中则通知（`notification_hub`）或触发动作/函数。 |
| 本体包与 SDK | `GET /api/ontology-categories/{cid}/export?format=json` 导出类别 + 本体 + 属性 + 关系 + 三元组 + 接口 + 动作 + 函数 + 视图的完整包；`POST /import` 支持 `dry_run`。另提供 `GET /api/ontology-categories/{cid}/agent-tools` 输出给 LLM 的工具清单（动作/函数 code + 描述 + 参数 schema），接 `oag_service` 的 system prompt，实现"智能体直接用本体能力"。 |
| 编辑历史 | `entity_change_logs(id, entity_id, field, old_value, new_value, source: user/action/inference, operator, created_at)`，实体详情页显示变更时间线。 |

---

## 5. 新增/改造表总览

| 表 | 优先级 | 用途 |
|---|---|---|
| `ontology_shared_properties` | P0 | 跨本体共享属性定义（契约式复用） |
| `ontology_interfaces` | P0 | 接口定义 |
| `ontology_interface_properties` | P0 | 接口属性契约 |
| `ontology_interface_links` | P0/P1 | 接口链接契约 |
| `ontology_interface_implementations` | P0 | 本体实现接口的映射 |
| `ontology_functions` | P0 | 函数（只读计算） |
| `ontology_derived_properties` | P0 | 派生属性（函数 / 图指标） |
| `ontology_runtime_invocations` | P0 | 函数与动作调用记录 |
| `ontologies`（扩列） | P0 | code/title_key/primary_key/display_name/plural_name/icon/status/visibility/group |
| `ontology_attributes`（扩列） | P0 | is_edit_only/render_hint/format/unit/shared_property_id |
| `ontology_service_rules` | P1 | 动作规则（提交条件/校验/提交后） |
| `ontology_service_effects` | P1 | 动作副作用（通知/Webhook/写回） |
| `ontology_service_invocations` | P1 | 动作执行记录与撤销 |
| `ontology_relations`（扩列） | P1 | cardinality/inverse_name/is_symmetric/is_transitive/status |
| `ontology_relation_constraints`（扩列） | P1 | source/target min-max、is_required |
| `ontology_relation_properties` | P1 | 关系属性定义 |
| `relations`（扩列） | P1 | properties JSON |
| `ontology_object_views` | P1 | 对象视图布局配置 |
| `ontology_versions` | P1 | 本体版本快照 |
| `ontology_suggestions`（扩列） | P1 | 提案化：proposal_type/base_version/diff/reviewers |
| `ontology_permissions`、`attribute_visibility` | P2 | 权限与受限视图 |
| `ontology_saved_sets` | P2 | 保存的对象集 |
| `ontology_monitors` | P2 | 对象监视器 |
| `entity_change_logs` | P2 | 实体编辑历史 |

---

## 6. API 增量一览（前缀 `/api`）

```
# 共享属性
GET|POST   /shared-properties
PUT|DELETE /shared-properties/{id}
POST       /shared-properties/{id}/apply

# 接口
GET|POST    /ontology-categories/{cid}/interfaces
GET|PUT|DELETE /interfaces/{iid}
GET|PUT     /interfaces/{iid}/properties
GET|PUT     /interfaces/{iid}/links
POST|DELETE /interfaces/{iid}/implementations[/{ontology_id}]
POST        /interfaces/{iid}/implementations/validate
GET         /ontology-categories/{cid}/interfaces/{code}/objects
GET         /ontology-categories/{cid}/ontologies/{oid}/interfaces

# 函数与派生属性
GET|POST    /ontology-categories/{cid}/functions
GET|PUT|DELETE /functions/{fid}
POST        /functions/{fid}/test
POST        /entities/{eid}/functions/{fid}/invoke
GET|POST    /ontology-categories/{cid}/ontologies/{oid}/derived-properties
PUT|DELETE  /derived-properties/{id}
POST        /derived-properties/{id}/materialize

# 动作补强
GET|POST    /ontology-services/{id}/rules
PUT|DELETE  /ontology-services/{id}/rules/{rid}
GET|POST    /ontology-services/{id}/effects
PUT|DELETE  /ontology-services/{id}/effects/{eid}
GET         /ontology-services/{id}/invocations
POST        /ontology-services/invocations/{iid}/undo
POST        /ontology-services/{id}/batch-invoke

# 链接
GET|POST    /ontology-categories/{cid}/relations/{rid}/properties
PUT|DELETE  /ontology-categories/{cid}/relations/{rid}/properties/{pid}

# 视图 / 版本 / 提案 / 影响分析
GET|POST    /ontology-categories/{cid}/object-views
PUT|DELETE  /object-views/{id}
GET|POST    /ontology-categories/{cid}/versions
POST        /ontology-categories/{cid}/versions/{vid}/rollback
GET         /ontology-categories/{cid}/ontologies/{oid}/usages

# P2
POST        /object-set
GET|POST    /saved-sets
GET|POST    /ontology-categories/{cid}/monitors
GET         /ontology-categories/{cid}/export?format=json
POST        /ontology-categories/import
GET         /ontology-categories/{cid}/agent-tools
```

---

## 7. 分期路线图

| 阶段 | 内容 | 依赖 | 工期 |
|---|---|---|---|
| **S1（P0-1）** | 本体元数据扩列 + 共享属性 | — | 3~4d |
| **S2（P0-2）** | 接口（四表 + 校验 + 多态查询 + UI） | S1（接口属性依赖共享属性） | 4~5d |
| **S3（P0-3）** | 函数 + 派生属性 + 调用记录 | 复用 `service_runtime` | 4~5d |
| **S4（P1-1）** | 动作规则/副作用/日志/撤销/批量 | S3（调用记录共用） | 5~6d |
| **S5（P1-2）** | 链接基数/反向/关系属性 | 与图迁入、图推理联动 | 3~4d |
| **S6（P1-3）** | 对象视图与视图编辑器 | S2（接口级视图） | 3d |
| **S7（P1-4）** | 版本 + 提案 + 影响分析 + 回滚 | S2/S6（快照含接口与视图） | 4~5d |
| **S8（P2）** | 权限、对象集与聚合、监视器、本体包/agent-tools、编辑历史 | 前述稳定后 | 12~15d |

> 建议顺序理由：先把"类型语义"（S1）和"多态契约"（S2）打牢，再补"可编程能力"（S3/S4），此时函数/动作/接口三者可互相复用（接口上的动作、动作里的函数），最后做运行时与治理（S6/S7）。S5 可并行。

---

## 8. 风险与注意事项

1. **接口 ≠ 模板，避免概念打架**：两者并存容易让用户困惑，UI 必须给出对照说明（见 4.2 表）；实现上接口属性优先复用共享属性，让"共享属性"成为两者的共同底座。
2. **接口实现校验要前置**：本体改名/删属性时若正被接口引用，需在 `usages` 中提示并在删除时给出"将解除 N 个接口映射"的二次确认。
3. **函数/动作的安全边界**：沿用现有 AST import 白名单与子进程隔离；函数声明为只读是**约定**而非强制（沙箱已禁 `os/subprocess`），若将来放开网络写操作需引入"只读/读写"双模式并在代码里区分入口。
4. **派生属性物化的成本**：`materialized` 模式会写入 `entities.properties` 并需同步 Neo4j，大图要限流；默认用 `virtual` 实时算，只对高频字段物化。
5. **链接属性对既有数据的影响**：`relations.properties` 为空是合法的，抽取与图迁入需兼容 NULL；图迁入时按 `ontology_relation_properties` 定义写入 Neo4j 边属性。
6. **版本快照体积**：一个类别完整快照（含本体、属性、接口、视图、动作、函数）可能达数百 KB，`snapshot` 建议只存定义层（不含实体），并限制保留最近 N 个版本。
7. **提案与建议共用一张表**：`ontology_suggestions` 已有生成/审核/回填逻辑，扩展为通用提案时须保证 `proposal_type='suggestion'` 的既有行为完全不变（向后兼容）。
8. **工作量控制**：P0 三项（约 12~14 天）即可让本体从"抽取约束"升级为"可编程对象模型"；P2 可按需点菜，不必一次做完。

---

## 9. 附录：Palantir 文档索引

- 本体（Ontology）概述：https://www.palantir.com/docs/zh/foundry/ontologies/ontologies-overview/
- Ontology 提案（分支/审查/合并）：https://www.palantir.com/docs/zh/foundry/ontologies/ontologies-proposals/
- 对象类型：https://www.palantir.com/docs/zh/foundry/object-link-types/object-types-overview/
- 共享属性：https://www.palantir.com/docs/zh/foundry/object-link-types/shared-property-overview/
- 链接类型：https://www.palantir.com/docs/zh/foundry/object-link-types/link-types-overview/
- 动作类型：https://www.palantir.com/docs/zh/foundry/action-types/overview/
- 函数：https://www.palantir.com/docs/foundry/functions/overview-functions/
- 接口：https://www.palantir.com/docs/zh/foundry/interfaces/interface-overview/
- 对象视图 / 对象浏览器 / 对象监视器 / 对象权限管理：见侧边栏同名条目（`/docs/zh/foundry/object-views/`、`object-explorer/`、`object-monitors/`、`object-permissions/`）
