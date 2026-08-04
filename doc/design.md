# KnowSource 本体管理功能设计文档

## 一、背景与目标

### 1.1 现状

KnowSource 是一个基于 RAG + 知识图谱的知识库系统，前端使用 Vue 3，后端使用 Python (FastAPI)。当前系统在抽取实体和关系时，LLM 的 System Prompt 中仅列出了固定的实体类型和关系类型作为参考，没有硬性约束，导致每次抽取结果不一致。

### 1.2 目标

在当前系统上增加本体（Ontology）管理功能，用户可以：

- 维护**本体类别**：一个领域本体方案（如"金融领域本体"），作为顶层容器，知识库绑定到本体类别
- 维护**本体**：在某个本体类别下定义实体类型（如"人物"、"组织"），每个本体可携带**多个属性**（姓名、年龄、成立时间等），用于约束实体抽取的字段
- 维护**本体关系定义**：一份**关系字典**（如"任职于"、"持有"），只定义关系名称与含义，不绑定具体起终点
- 维护**本体关系设置**：从已有本体和关系中，选择**起点本体 + 关系 + 终点本体**组成**三元组约束**（如 `(人物)-任职于→(组织)`），作为关系抽取的硬性约束
- 将本体类别绑定到知识库
- 文件处理时根据绑定的本体约束（本体+属性+三元组）抽取实体和关系，使结果符合用户预期

### 1.3 术语约定

为避免"本体"与"实体"混淆，本文档严格区分两层概念：**本体是类型定义（ontology_*，存 SQLite），实体是抽取后生成的实例数据（entity，存 SQLite `entities`/`relations` 表并同步 Kùzu 图数据库）**。统一以下术语：

| 术语         | 对应概念                | 所在层 | 说明                                            |
| ---------- | ------------------- | ------ | --------------------------------------------- |
| **本体类别**   | Ontology Category   | 定义层 | 顶层容器，一个领域本体方案，如"金融领域本体"。知识库绑定到本体类别            |
| **本体**     | Ontology（类型定义）      | 定义层 | 归属到本体类别的类型定义，如"人物"、"组织"。每个本体可有多个属性            |
| **本体属性**   | Ontology Attribute  | 定义层 | 本体的字段定义，如"姓名(string,必填)"、"年龄(number)"         |
| **属性模板**   | Attribute Template  | 定义层 | 可复用的属性组（**全局**，不归属任何本体类别），如"自然人基础属性"。本体可引用一个或多个模板 + 补充自有属性，抽取时合并为完整属性 |
| **本体关系定义** | Ontology Relation   | 定义层 | 关系字典中的一项，如"任职于"。仅含名称与描述，不绑定起终点                |
| **本体关系设置** | Relation Constraint | 定义层 | 由"起点本体 + 关系 + 终点本体"组成的三元组约束，如 `(人物)-任职于→(组织)` |
| **实体**     | Entity              | 实例层 | 抽取后生成的实例数据，存 SQLite `entities`/`relations` 表（权威）并同步 Kùzu，通过 `ontology_id`/`entity_type` 归属到某个本体（类型） |

> 前六个术语（定义层）对应本体管理界面上的功能模块（其中"属性模板"为全局模块，不归属某个本体类别）；实体/关系（实例层）由文件处理抽取自动生成，并可在"实体管理"菜单浏览与编辑（详见第六章）。

### 1.4 设计原则

- 本体管理功能直接在现有 Python (FastAPI) 后端中实现
- 复用现有的 SQLite 数据库，新增 11 张表（6 张本体定义层 + 3 张属性模板 + 2 张实体实例层），**不使用数据库外键**，关联由 service 层维护
- 抽取后的实体/关系实例以 SQLite 为权威存储（供实体管理菜单），并同步写入 Kùzu 图数据库（供图谱可视化与图遍历）
- 图谱抽取服务根据本体约束动态构建 Prompt（含本体属性与三元组约束），并增加后处理校验
- 无本体绑定时保持现有行为不变，完全向后兼容

***

## 二、整体架构

### 2.1 架构概览（纯 Python）

```
┌──────────────────────────────────────────────────────────────┐
│                    前端 (Vue 3 / Vite)                       │
│                       Port: 3000                             │
└──────────────────────────┬───────────────────────────────────┘
                           │ /api/* (Vite proxy)
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                  Python 后端 (FastAPI)                        │
│                     Port: 8000                                │
│                                                              │
│  ┌────────────────────┐  ┌────────────────────────────────┐ │
│  │  本体管理模块（新增）│  │  图谱抽取服务（修改）           │ │
│  │                    │  │                                │ │
│  │  routers/          │  │  - 接收 ontology_constraint    │ │
│  │   ontology.py      │  │  - 动态构建 Prompt              │ │
│  │                    │  │  - 后处理校验过滤               │ │
│  │  services/         │  │                                │ │
│  │   ontology_        │  └────────────────────────────────┘ │
│  │   service.py       │                                      │
│  └────────┬───────────┘                                      │
│           │                                                  │
│           ▼                                                  │
│  ┌────────────────────┐                                      │
│  │   SQLite 数据库     │  ← 新增 11 张表(6 本体+3 模板+2 实体)  │
│  │  (models.py)       │                                      │
│  └────────────────────┘                                      │
│                                                              │
│  现有功能保持不变（文件处理、RAG 问答、图谱查询、向量管理等）    │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 本体约束注入流程

```
前端发起 POST /api/files/{fileId}/process
    │
    ▼
FastAPI router (files.py)
    │
    ├─ 查询 file 所属的 kb_id（已有逻辑）
    │
    ├─ 查询 kb_id 绑定的本体类别（调用 OntologyService）
│
├─ 如果有绑定本体类别：
│    ├─ 加载本体(含属性)、关系定义、三元组约束
│    ├─ 构建 ontology_constraint 对象
│    └─ 传递给 _process_file_bg -> GraphExtractionService.extract
│
└─ 如果没有绑定本体类别：直接处理（保持原有行为）
```

***

## 三、数据库设计（Python SQLite 新增表）

在现有 SQLite 数据库（`backend/data/knowsource.db`）中新增 11 张表：6 张**本体定义层**表（本体类别、本体、本体属性、本体关系定义、本体关系设置、知识库绑定）+ 3 张**属性模板**表（跨领域属性复用，全局）+ 2 张**实体实例层**表（实体、关系）。

> **不使用数据库外键约束**：所有表间关联通过 `*_id` 字段做**逻辑关联**，由 `services/ontology_service.py` 在应用层维护引用一致性与级联删除（如删除本体类别时，由 service 层主动删除其下全部本体、属性、关系、三元组、绑定）。SQLite 仅保留 `UNIQUE` 约束用于防重。

### 3.1 本体类别表 `ontology_categories`

顶层容器，一个领域本体方案（如"金融领域本体"）。知识库绑定到本表。

```sql
CREATE TABLE IF NOT EXISTS ontology_categories (
    id              VARCHAR PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    description     TEXT DEFAULT '',
    is_system       INTEGER NOT NULL DEFAULT 0,
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(name)
);
```

### 3.2 本体表 `ontologies`

本体（类型定义），归属到某个本体类别（如"人物"、"组织"）。

> 注意：本表存的是**类型定义**，不是抽取后的实体实例。实体实例在知识抽取后生成，存入 SQLite 的 `entities`/`relations` 表并同步至 Kùzu 图数据库（见 3.7–3.9）。

```sql
CREATE TABLE IF NOT EXISTS ontologies (
    id              VARCHAR PRIMARY KEY,
    category_id     VARCHAR NOT NULL,          -- 归属本体类别（逻辑关联 ontology_categories.id）
    name            VARCHAR(50) NOT NULL,
    description     VARCHAR(500) DEFAULT '',
    color           VARCHAR(20) DEFAULT NULL,  -- 前端图谱展示颜色
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(category_id, name)
);
```

### 3.3 本体属性表 `ontology_attributes`

一个本体可拥有多个属性（字段定义），用于约束实体抽取时的结构化字段。

```sql
CREATE TABLE IF NOT EXISTS ontology_attributes (
    id              VARCHAR PRIMARY KEY,
    ontology_id     VARCHAR NOT NULL,          -- 归属本体（逻辑关联 ontologies.id）
    name            VARCHAR(50) NOT NULL,      -- 属性名，如"姓名""年龄"
    data_type       VARCHAR(20) NOT NULL,      -- string/number/boolean/date/datetime/text/enum
    description     VARCHAR(500) DEFAULT '',
    is_required     INTEGER NOT NULL DEFAULT 0,
    default_value   VARCHAR(200) DEFAULT NULL,
    enum_values     TEXT DEFAULT NULL,         -- JSON 数组，仅 data_type=enum 时使用，如 ["男","女"]
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(ontology_id, name)
);
```

> **`data_type`** **取值**：`string`（字符串）、`number`（数字）、`boolean`（布尔）、`date`（日期）、`datetime`（日期时间）、`text`（长文本）、`enum`（枚举，配合 `enum_values`）。

### 3.4 属性模板表（跨领域属性复用，全局）

属性模板用于解决跨领域共性属性重复定义的问题（如"人物"在军工、银行领域都有姓名、性别、住址，但各自又有军衔 / 信用评分等特有属性）。模板是**全局**的，不归属任何本体类别；一个本体可引用一个或多个模板，本体最终属性 = 模板属性（合并）+ 本体自有属性。

#### 3.4.1 属性模板表 `ontology_attribute_templates`

```sql
CREATE TABLE IF NOT EXISTS ontology_attribute_templates (
    id              VARCHAR PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,       -- 模板名，如"自然人基础属性"
    description     VARCHAR(500) DEFAULT '',
    is_system       INTEGER NOT NULL DEFAULT 0,  -- 是否系统内置（内置模板不可删）
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(name)
);
```

#### 3.4.2 模板属性表 `ontology_template_attributes`

模板下的属性定义，结构与 `ontology_attributes` 一致。

```sql
CREATE TABLE IF NOT EXISTS ontology_template_attributes (
    id              VARCHAR PRIMARY KEY,
    template_id     VARCHAR NOT NULL,          -- 归属模板（逻辑关联 ontology_attribute_templates.id）
    name            VARCHAR(50) NOT NULL,
    data_type       VARCHAR(20) NOT NULL,      -- string/number/boolean/date/datetime/text/enum
    description     VARCHAR(500) DEFAULT '',
    is_required     INTEGER NOT NULL DEFAULT 0,
    default_value   VARCHAR(200) DEFAULT NULL,
    enum_values     TEXT DEFAULT NULL,         -- JSON 数组，仅 data_type=enum 时使用
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(template_id, name)
);
```

#### 3.4.3 本体-模板关联表 `ontology_template_bindings`（多对多）

```sql
CREATE TABLE IF NOT EXISTS ontology_template_bindings (
    id              VARCHAR PRIMARY KEY,
    ontology_id     VARCHAR NOT NULL,          -- 引用模板的本体（逻辑关联 ontologies.id）
    template_id     VARCHAR NOT NULL,          -- 被引用的模板（逻辑关联 ontology_attribute_templates.id）
    sort_order      INTEGER NOT NULL DEFAULT 0, -- 模板属性合并顺序
    created_at      VARCHAR,
    UNIQUE(ontology_id, template_id)
);
```

> **属性合并规则**：本体最终属性 = 按 `sort_order` 合并各绑定模板的属性 + 本体自有属性（`ontology_attributes`）。同名冲突时，**本体自有属性优先**（覆盖模板同名属性），service 层合并时检测并提示冲突。抽取时以合并后的完整属性列表注入 Prompt 与后处理校验。
>
> **删除规则**：删除模板仅解除所有本体的引用（删除 `ontology_template_bindings` 中相关记录），不影响本体自有属性与已抽取的实体数据；系统内置模板（`is_system=1`）不可删除。
>
> **使用示例**：全局建一个"自然人基础属性"模板（姓名、性别、住址、身份证号），军工领域的"人物"本体与银行领域的"人物"本体都引用它，再各自补充军衔 / 信用评分等自有属性，避免共性属性重复维护。

### 3.5 本体关系定义表 `ontology_relations`

关系字典，只定义关系名称与含义，**不绑定**具体起终点。同一关系可被多个三元组引用。

```sql
CREATE TABLE IF NOT EXISTS ontology_relations (
    id              VARCHAR PRIMARY KEY,
    category_id     VARCHAR NOT NULL,          -- 归属本体类别（逻辑关联 ontology_categories.id）
    name            VARCHAR(50) NOT NULL,
    description     VARCHAR(500) DEFAULT '',
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(category_id, name)
);
```

### 3.6 本体关系设置表 `ontology_relation_constraints`（三元组）

由"起点本体 + 关系 + 终点本体"组成的三元组约束，是关系抽取的硬性约束。两端指向的是**本体（类型定义）**，不是实体实例。

```sql
CREATE TABLE IF NOT EXISTS ontology_relation_constraints (
    id                  VARCHAR PRIMARY KEY,
    category_id         VARCHAR NOT NULL,          -- 归属本体类别（逻辑关联 ontology_categories.id）
    source_ontology_id  VARCHAR NOT NULL,          -- 起点本体（逻辑关联 ontologies.id）
    relation_id         VARCHAR NOT NULL,          -- 关系（逻辑关联 ontology_relations.id）
    target_ontology_id  VARCHAR NOT NULL,          -- 终点本体（逻辑关联 ontologies.id）
    description         VARCHAR(500) DEFAULT '',
    created_at          VARCHAR,
    UNIQUE(category_id, source_ontology_id, relation_id, target_ontology_id)
);
```

> 三元组是约束的最小单位。例如 `(人物)-任职于→(组织)`、`(人物)-持有→(金融产品)`、`(组织)-持有→(金融产品)`。同一关系可出现在多条三元组中（如"持有"既可人物→产品，也可组织→产品）。

### 3.7 知识库-本体类别绑定表 `kb_ontology_bindings`

```sql
CREATE TABLE IF NOT EXISTS kb_ontology_bindings (
    id              VARCHAR PRIMARY KEY,
    kb_id           VARCHAR NOT NULL,
    category_id     VARCHAR NOT NULL,          -- 绑定本体类别（逻辑关联 ontology_categories.id）
    created_at      VARCHAR,
    UNIQUE(kb_id)
);
```

### 3.8 实体表 `entities`（实例层）

抽取后生成的实体实例。供"实体管理"菜单浏览/编辑，是实体实例的权威存储。

```sql
CREATE TABLE IF NOT EXISTS entities (
    id              VARCHAR PRIMARY KEY,
    kb_id           VARCHAR NOT NULL,          -- 所属知识库（逻辑关联 knowledge_bases）
    ontology_id     VARCHAR NOT NULL,          -- 归属本体（逻辑关联 ontologies.id）
    entity_type     VARCHAR(50) NOT NULL,      -- 类型名（冗余，= ontologies.name，便于查询/展示）
    name            VARCHAR(200) NOT NULL,     -- 实体名
    description     VARCHAR(1000) DEFAULT '',
    properties      TEXT DEFAULT NULL,         -- JSON，属性值，键对齐 ontology_attributes.name
    source_file_id  VARCHAR DEFAULT NULL,      -- 来源文件
    source_chunk_id VARCHAR DEFAULT NULL,      -- 来源分块
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(kb_id, entity_type, name)
);
```

### 3.9 关系表 `relations`（实例层）

抽取后生成的实体间关系实例。

```sql
CREATE TABLE IF NOT EXISTS relations (
    id                VARCHAR PRIMARY KEY,
    kb_id             VARCHAR NOT NULL,        -- 所属知识库（逻辑关联 knowledge_bases）
    relation_def_id   VARCHAR NOT NULL,        -- 归属关系定义（逻辑关联 ontology_relations.id）
    relation_type     VARCHAR(50) NOT NULL,    -- 关系名（冗余，= ontology_relations.name）
    source_entity_id  VARCHAR NOT NULL,        -- 起点实体（逻辑关联 entities.id）
    target_entity_id  VARCHAR NOT NULL,        -- 终点实体（逻辑关联 entities.id）
    description       VARCHAR(1000) DEFAULT '',
    source_file_id    VARCHAR DEFAULT NULL,
    source_chunk_id   VARCHAR DEFAULT NULL,
    created_at        VARCHAR,
    updated_at        VARCHAR,
    UNIQUE(kb_id, source_entity_id, relation_type, target_entity_id)
);
```

### 3.10 实体/关系与 Kùzu 图数据库的关系

实体实例采用**双库存储**，职责分工：

| 存储 | 表/节点 | 职责 | 操作入口 |
|------|---------|------|---------|
| SQLite | `entities` / `relations` | 实体/关系实例的**权威存储**，支持列表、分页、搜索、按本体类型过滤、属性编辑 | 实体管理菜单 |
| Kùzu | `Entity` / `Relation` 节点 + `RELATES` 边 | **图谱存储**，供图谱可视化与图遍历查询 | 图谱页面、图查询 |

抽取流程：抽取校验通过后，先写入 SQLite（`entities`/`relations`），再同步写入 Kùzu（`Entity`/`Relation` 节点与 `RELATES` 边）。在"实体管理"菜单中编辑/删除实体时，由 service 层同步操作 Kùzu，保持双库一致。

> **命名约定**：本体定义层全部 `ontology_*` 命名；实体实例层 `entities`/`relations` 命名。实体通过 `ontology_id`（及冗余的 `entity_type = ontologies.name`）归属本体。

### 3.11 ER 关系

```
[SQLite - 本体定义层]
ontology_categories 1 ──N ontologies
ontology_categories 1 ──N ontology_relations
ontology_categories 1 ──N ontology_relation_constraints
ontology_categories 1 ──N kb_ontology_bindings (通过 kb_id 关联 knowledge_bases)

ontologies           1 ──N ontology_attributes
ontologies           1 ──N ontology_relation_constraints (作为 source / target)
ontology_relations   1 ──N ontology_relation_constraints

[SQLite - 属性模板（全局，跨本体类别复用）]
ontology_attribute_templates 1 ──N ontology_template_attributes
ontologies N ──M ontology_attribute_templates  (通过 ontology_template_bindings；本体最终属性 = 模板属性合并 + 自有属性)

[SQLite - 实体实例层]
knowledge_bases     1 ──N entities      (通过 kb_id)
knowledge_bases     1 ──N relations     (通过 kb_id)
ontologies          1 ──N entities      (通过 ontology_id；entity_type = ontologies.name)
ontology_relations  1 ──N relations     (通过 relation_def_id；relation_type = ontology_relations.name)
entities            1 ──N relations     (作为 source / target，通过 source_entity_id / target_entity_id)

[跨库 - SQLite 实例层 ↔ Kùzu 图谱]
entities  <──>  Kùzu Entity 节点                  (抽取/编辑时双写同步)
relations <──>  Kùzu Relation 节点 + RELATES 边   (抽取/编辑时双写同步)
```

### 3.12 数据示例（金融领域本体）

```
属性模板（ontology_attribute_templates，全局，不归属本体类别）：
├─ 自然人基础属性 ─ 属性(ontology_template_attributes): 姓名(string,必填), 性别(enum:男,女), 住址(string), 身份证号(string)
└─ （可被任意领域本体引用，金融/军工的"人物"均引用它，共性属性只维护一份）

本体类别（ontology_categories）：金融领域本体
├─ 本体（ontologies）
│   ├─ 人物  ─ 引用模板: [自然人基础属性]；自有属性(ontology_attributes): 年龄(number), 出生日期(date)
│   │        （最终属性 = 姓名,性别,住址,身份证号 + 年龄,出生日期）
│   ├─ 组织  ─ 属性: 名称(string,必填), 成立时间(date), 行业(string)
│   └─ 金融产品 ─ 属性: 名称(string,必填), 类型(string), 风险等级(enum:低,中,高)
├─ 本体关系定义（ontology_relations）
│   ├─ 任职于  ─ 人物在某组织担任职务
│   ├─ 持有    ─ 持有某金融产品
│   └─ 影响    ─ 某对象对另一对象产生影响
└─ 本体关系设置（ontology_relation_constraints，三元组）
    ├─ (人物) ─任职于→ (组织)
    ├─ (人物) ─持有→   (金融产品)
    ├─ (组织) ─持有→   (金融产品)
    └─ (组织) ─影响→   (金融产品)

跨领域复用对比：军工领域本体的"人物"同样引用 [自然人基础属性]，自有属性补充 军衔(string)、保密等级(enum:公开,内部,机密)，
   共性属性（姓名/性别/住址/身份证号）无需重复定义，仅维护一份模板即可。

抽取后（SQLite 实体实例层 entities / relations，并同步至 Kùzu）：
    entities:  { name:"张三",  entity_type:"人物", ontology_id:..., properties:{姓名:..., 性别:..., 住址:..., 年龄:..., 出生日期:...} }
    entities:  { name:"A公司", entity_type:"组织", ontology_id:..., properties:{名称:..., 行业:...} }
    relations: { source_entity:"张三"(人物) ─任职于→ target_entity:"A公司"(组织), relation_type:"任职于" }
```

***

## 四、API 设计

### 4.1 本体管理 API（Python 新增 Router）

API 按四个功能模块组织，外加知识库绑定。顶层资源 `ontology-categories` 对应本体类别（`ontology_categories` 表）；其下的 `ontologies` 子资源对应本体（`ontologies` 表，类型定义）。路径中的 `{categoryId}` 指本体类别 ID，`{ontologyId}` 指本体（类型）ID。

#### 模块一：本体类别 CRUD

| 方法       | 路径                                  | 说明                              |
| -------- | ----------------------------------- | ------------------------------- |
| `GET`    | `/api/ontology-categories`          | 获取本体类别列表（支持 `?q=` 搜索）           |
| `GET`    | `/api/ontology-categories/{categoryId}` | 获取本体类别详情（含本体/属性/关系/三元组的完整结构）    |
| `POST`   | `/api/ontology-categories`          | 创建本体类别                          |
| `PUT`    | `/api/ontology-categories/{categoryId}` | 更新本体类别（名称、描述）                   |
| `DELETE` | `/api/ontology-categories/{categoryId}` | 删除本体类别（级联删除其下全部本体、属性、关系、三元组、绑定） |

#### 模块二：本体管理（本体 + 属性）

| 方法       | 路径                                                                              | 说明                        |
| -------- | ------------------------------------------------------------------------------- | ------------------------- |
| `GET`    | `/api/ontology-categories/{categoryId}/ontologies`                              | 获取本体列表（含属性）               |
| `POST`   | `/api/ontology-categories/{categoryId}/ontologies`                              | 添加本体                      |
| `PUT`    | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}`                 | 更新本体（名称、描述、颜色）            |
| `DELETE` | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}`                 | 删除本体（级联删除其属性与引用它的三元组）     |
| `POST`   | `/api/ontology-categories/{categoryId}/ontologies/batch`                        | 批量添加本体                    |
| `GET`    | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}/attributes`      | 获取某本体的属性列表                |
| `POST`   | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}/attributes`      | 给本体添加一个属性                 |
| `PUT`    | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}/attributes/{id}` | 更新属性                      |
| `DELETE` | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}/attributes/{id}` | 删除属性                      |
| `PUT`    | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}/attributes`      | 整体替换某本体的属性列表（便于前端表单一次性保存） |

#### 模块三：本体关系定义（关系字典）

| 方法       | 路径                                                  | 说明                |
| -------- | --------------------------------------------------- | ----------------- |
| `GET`    | `/api/ontology-categories/{categoryId}/relations`   | 获取关系字典列表          |
| `POST`   | `/api/ontology-categories/{categoryId}/relations`   | 添加关系              |
| `PUT`    | `/api/ontology-categories/{categoryId}/relations/{id}`  | 更新关系（名称、描述）       |
| `DELETE` | `/api/ontology-categories/{categoryId}/relations/{id}`  | 删除关系（级联删除引用它的三元组） |
| `POST`   | `/api/ontology-categories/{categoryId}/relations/batch` | 批量添加关系            |

#### 模块四：本体关系设置（三元组约束）

| 方法       | 路径                                                    | 说明                        |
| -------- | ----------------------------------------------------- | ------------------------- |
| `GET`    | `/api/ontology-categories/{categoryId}/constraints`   | 获取三元组约束列表（含起终点本体名与关系名）    |
| `POST`   | `/api/ontology-categories/{categoryId}/constraints`   | 新增一条三元组（起点本体 + 关系 + 终点本体） |
| `PUT`    | `/api/ontology-categories/{categoryId}/constraints/{id}`  | 更新三元组（描述或起终点/关系）          |
| `DELETE` | `/api/ontology-categories/{categoryId}/constraints/{id}`  | 删除一条三元组                   |
| `POST`   | `/api/ontology-categories/{categoryId}/constraints/batch` | 批量新增三元组                   |

#### 模块五：属性模板管理（全局，跨本体类别复用）

属性模板不归属任何本体类别，路径独立为 `/api/attribute-templates`。一个本体可通过本体管理接口引用一个或多个模板，最终属性 = 模板属性合并 + 本体自有属性。

| 方法       | 路径                                                  | 说明                              |
| -------- | --------------------------------------------------- | ------------------------------- |
| `GET`    | `/api/attribute-templates`                          | 获取属性模板列表（支持 `?q=` 搜索）           |
| `GET`    | `/api/attribute-templates/{templateId}`             | 获取模板详情（含属性列表）                   |
| `POST`   | `/api/attribute-templates`                          | 创建属性模板                          |
| `PUT`    | `/api/attribute-templates/{templateId}`             | 更新模板（名称、描述）                     |
| `DELETE` | `/api/attribute-templates/{templateId}`             | 删除模板（解除所有本体引用；`is_system=1` 不可删） |
| `POST`   | `/api/attribute-templates/{templateId}/attributes`  | 给模板添加一个属性                       |
| `PUT`    | `/api/attribute-templates/{templateId}/attributes/{id}` | 更新模板属性                      |
| `DELETE` | `/api/attribute-templates/{templateId}/attributes/{id}` | 删除模板属性                      |
| `PUT`    | `/api/attribute-templates/{templateId}/attributes`  | 整体替换某模板的属性列表（便于前端一次性保存）         |

#### 本体引用属性模板（多对多绑定，嵌套在本体下）

| 方法       | 路径                                                                              | 说明                                       |
| -------- | ------------------------------------------------------------------------------- | ---------------------------------------- |
| `GET`    | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}/templates`        | 获取本体已引用的模板列表                             |
| `PUT`    | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}/templates`        | 设置本体引用的模板（整体替换，传 `template_ids` 数组）      |
| `GET`    | `/api/ontology-categories/{categoryId}/ontologies/{ontologyId}/merged-attributes` | 获取本体合并后的完整属性（模板属性 + 自有属性，含冲突提示，前端预览用） |

#### 知识库绑定本体类别

| 方法       | 路径                        | 说明           |
| -------- | ------------------------- | ------------ |
| `GET`    | `/api/kb/{kbId}/ontology` | 获取知识库绑定的本体类别 |
| `PUT`    | `/api/kb/{kbId}/ontology` | 设置/更新绑定的本体类别 |
| `DELETE` | `/api/kb/{kbId}/ontology` | 解绑本体类别       |

#### 实体/关系实例管理（实体管理菜单数据源，操作同步 Kùzu）

| 方法       | 路径                            | 说明                                  |
| -------- | ----------------------------- | ----------------------------------- |
| `GET`    | `/api/entities`               | 实体列表（`?kb_id=&ontology_id=&q=&page=&page_size=`） |
| `GET`    | `/api/entities/{entityId}`    | 实体详情（含属性值与关联关系）                     |
| `PUT`    | `/api/entities/{entityId}`    | 更新实体（名称、描述、属性值），同步 Kùzu             |
| `DELETE` | `/api/entities/{entityId}`    | 删除实体，同步删除 Kùzu 节点                   |
| `GET`    | `/api/relations`              | 关系实例列表（`?kb_id=&relation_type=&q=&page=&page_size=`） |
| `GET`    | `/api/relations/{relationId}` | 关系实例详情                              |
| `DELETE` | `/api/relations/{relationId}` | 删除关系实例，同步删除 Kùzu `RELATES` 边        |

> 说明：`/api/entities`、`/api/relations` 是**实例层**资源（抽取后的实体/关系数据）；`/api/ontology-categories/{categoryId}/ontologies`、`.../relations` 是**定义层**资源（本体类型与关系字典），二者路径不同、职责不同。

### 4.2 关键请求/响应格式

#### 创建本体类别

```json
POST /api/ontology-categories
{
    "name": "金融领域本体",
    "description": "用于金融文档的实体和关系抽取"
}
```

#### 添加本体

```json
POST /api/ontology-categories/{categoryId}/ontologies
{
    "name": "人物",
    "description": "人名，包括真实人物和虚拟人物",
    "color": "#e74c3c"
}
```

#### 给本体添加属性

```json
POST /api/ontology-categories/{categoryId}/ontologies/{ontologyId}/attributes
{
    "name": "性别",
    "data_type": "enum",
    "description": "人物性别",
    "is_required": false,
    "enum_values": ["男", "女"]
}
```

```json
POST /api/ontology-categories/{categoryId}/ontologies/{ontologyId}/attributes
{
    "name": "出生日期",
    "data_type": "date",
    "is_required": false
}
```

#### 一次性保存某本体的全部属性（整体替换）

```json
PUT /api/ontology-categories/{categoryId}/ontologies/{ontologyId}/attributes
{
    "attributes": [
        { "name": "姓名", "data_type": "string", "is_required": true },
        { "name": "年龄", "data_type": "number", "is_required": false },
        { "name": "出生日期", "data_type": "date", "is_required": false },
        { "name": "性别", "data_type": "enum", "enum_values": ["男", "女"] }
    ]
}
```

#### 添加关系定义（关系字典）

```json
POST /api/ontology-categories/{categoryId}/relations
{
    "name": "任职于",
    "description": "人物在某组织担任职务"
}
```

#### 添加三元组约束（本体关系设置）

```json
POST /api/ontology-categories/{categoryId}/constraints
{
    "source_ontology_id": "ont_person",
    "relation_id": "rel_works_at",
    "target_ontology_id": "ont_org",
    "description": "人物在组织中任职"
}
```

#### 创建属性模板

```json
POST /api/attribute-templates
{
    "name": "自然人基础属性",
    "description": "人物通用基础属性，跨领域复用"
}
```

#### 给属性模板添加属性

```json
POST /api/attribute-templates/{templateId}/attributes
{
    "name": "住址",
    "data_type": "string",
    "description": "常住地址",
    "is_required": false
}
```

#### 设置本体引用的属性模板（整体替换）

```json
PUT /api/ontology-categories/{categoryId}/ontologies/{ontologyId}/templates
{
    "template_ids": ["tpl_person_base"]
}
```

#### 获取本体合并后的完整属性（响应示例）

```json
GET /api/ontology-categories/{categoryId}/ontologies/{ontologyId}/merged-attributes
{
    "ontology_id": "ont_person",
    "attributes": [
        { "name": "姓名", "data_type": "string", "is_required": true, "source": "template:tpl_person_base" },
        { "name": "性别", "data_type": "enum", "enum_values": ["男","女"], "source": "template:tpl_person_base" },
        { "name": "住址", "data_type": "string", "source": "template:tpl_person_base" },
        { "name": "年龄", "data_type": "number", "source": "self" },
        { "name": "出生日期", "data_type": "date", "source": "self" }
    ],
    "conflicts": []
}
```

> `source` 标识属性来源（`template:xxx` 或 `self`）；`conflicts` 列出同名冲突项（自有属性已覆盖模板同名属性时记录）。

#### 获取本体类别详情（响应示例）

```json
GET /api/ontology-categories/{categoryId}
{
    "id": "cat_finance",
    "name": "金融领域本体",
    "description": "用于金融文档的实体和关系抽取",
    "ontologies": [
        {
            "id": "ont_person", "name": "人物", "description": "...", "color": "#e74c3c",
            "attributes": [
                { "id": "attr_1", "name": "姓名", "data_type": "string", "is_required": true },
                { "id": "attr_2", "name": "年龄", "data_type": "number", "is_required": false }
            ]
        }
    ],
    "relations": [
        { "id": "rel_works_at", "name": "任职于", "description": "..." }
    ],
    "constraints": [
        {
            "id": "rc_1",
            "source_ontology_id": "ont_person", "source_ontology_name": "人物",
            "relation_id": "rel_works_at",     "relation_name": "任职于",
            "target_ontology_id": "ont_org",   "target_ontology_name": "组织"
        }
    ]
}
```

#### 绑定知识库

```json
PUT /api/kb/{kbId}/ontology
{
    "category_id": "cat_finance"
}
```

#### 更新实体（实体管理菜单编辑属性值，同步 Kùzu）

```json
PUT /api/entities/{entityId}
{
    "name": "张三",
    "description": "项目负责人",
    "properties": {
        "姓名": "张三",
        "年龄": 35,
        "性别": "男"
    }
}
```

***

## 五、Python 后端改动

### 5.1 新增文件

| 文件                             | 说明                            |
| ------------------------------ | ----------------------------- |
| `routers/ontology.py`          | 本体管理 API 路由                   |
| `routers/entity.py`            | 实体/关系实例管理 API 路由（实体管理菜单数据源）   |
| `services/ontology_service.py` | 本体定义层业务逻辑（含逻辑关联、级联删除、属性模板合并与跨领域复用） |
| `services/entity_service.py`   | 实体/关系实例 CRUD + Kùzu 双写同步      |

### 5.2 修改文件

| 文件                                     | 改动                                                                                                                                      |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `models.py`                            | 新增 11 个 ORM 模型：`OntologyCategory`、`Ontology`、`OntologyAttribute`、`OntologyRelation`、`OntologyRelationConstraint`、`KbOntologyBinding`、`OntologyAttributeTemplate`、`OntologyTemplateAttribute`、`OntologyTemplateBinding`、`Entity`、`Relation` |
| `schemas.py`                           | 新增本体 + 实体/关系相关 Pydantic 请求/响应模型 + `OntologyConstraint` 模型                                                                                |
| `database.py`                          | 创建表时包含新的 11 张表（不建外键）                                                                                                                     |
| `server.py`                            | 注册 `ontology_router`、`entity_router`                                                                                                    |
| `sql/schema.sql`                       | 新增 11 张表的建表语句                                                                                                                           |
| `sql/migrations.sql`                   | 新增迁移脚本                                                                                                                                  |
| `services/file_service.py`             | `_process_file_bg` 查绑定的本体类别并传递给图谱抽取                                                                                                     |
| `services/graph_extraction_service.py` | 接收 `ontology_constraint` 参数，动态构建 Prompt（属性已由 service 层合并模板属性 + 本体自有属性，含三元组），后处理校验；校验通过后写入 SQLite `entities`/`relations` 并同步 Kùzu                          |
| `providers/graph_store/__init__.py`    | `KuzuGraphAdapter` 扩展 `Entity`/`Relation` 节点 schema 与写入逻辑，承载本体定义的动态属性；提供实体/关系实例的同步增删改接口                                                  |

### 5.3 Schema 新增

```python
# backend/schemas.py 新增
class OntologyAttributeConstraint(BaseModel):
    name: str
    data_type: str            # string/number/boolean/date/datetime/text/enum
    description: str = ""
    is_required: bool = False
    default_value: str | None = None
    enum_values: list[str] | None = None

class OntologyTypeDefConstraint(BaseModel):
    """单个本体（类型定义）及其属性（service 层已合并模板属性 + 本体自有属性）"""
    name: str
    description: str = ""
    attributes: list[OntologyAttributeConstraint] = []

class OntologyRelationConstraintItem(BaseModel):
    """三元组约束：起点本体 + 关系 + 终点本体（均为类型名）"""
    source_ontology: str      # 起点本体（类型）名
    relation: str             # 关系名
    target_ontology: str      # 终点本体（类型）名

class OntologyConstraint(BaseModel):
    """传递给图谱抽取服务的完整本体约束"""
    ontologies: list[OntologyTypeDefConstraint] = []              # 本体（类型）及其属性
    relations: list[str] = []                                     # 关系字典中的关系名
    relation_constraints: list[OntologyRelationConstraintItem] = []  # 三元组约束
```

### 5.4 图谱抽取改动（核心）

`services/graph_extraction_service.py` 的 `extract` 方法新增可选参数 `ontology_constraint`。

**有约束时的 System Prompt 动态构建**（属性已由 `ontology_service` 合并模板属性 + 本体自有属性后传入）：

```
你是知识图谱抽取助手。

你的任务是从文档分块中抽取实体和实体间关系，并严格输出 JSON。

【本体约束 - 必须严格遵守】
允许的实体类型及其属性：
  - 人物：人名，包括真实人物和虚拟人物
      属性：姓名(string,必填)、年龄(number)、出生日期(date)、性别(enum:男,女)
  - 组织：公司、机构、政府部门等组织实体
      属性：名称(string,必填)、成立时间(date)、行业(string)
  - 金融产品：基金、股票、债券、保险等金融产品
      属性：名称(string,必填)、类型(string)、风险等级(enum:低,中,高)

允许的关系（仅以下三元组成立，不得越界）：
  - (人物) ─任职于→ (组织)
  - (人物) ─持有→   (金融产品)
  - (组织) ─持有→   (金融产品)
  - (组织) ─影响→   (金融产品)

要求：
1. 仅基于提供文本抽取，不要编造。
2. 实体类型必须从上述允许的类型中选择，不要使用其他类型。
3. 每个实体尽量抽取上述列出的属性，缺失则留空；必填属性尽量给出。
4. 关系必须是上述三元组之一（起点类型 + 关系 + 终点类型 都要匹配），不得使用其他关系或组合。
5. 实体输出时附带 properties 字段，按属性定义输出键值。
...（其余现有要求不变）
```

**无本体约束时**：保持现有 Prompt 不变，向后兼容。

**抽取后校验**（在 `_merge_payload` 中）：

- 过滤不在 `ontologies` 列表中的实体类型（实体的 `entity_type` 必须命中某个本体定义的 `name`）。
- 过滤不匹配任何 `relation_constraints` 三元组的关系（起点类型、关系名、终点类型三者都要匹配）。
- 对实体属性做轻量校验：剔除不在合并后属性列表（模板属性 + 自有属性）中的属性键；必填属性缺失时记录告警（不阻断）。
- 对属性值做类型规整（如 `number` 转浮点失败则置空、`enum` 值不在候选内则置空）。
- 校验通过的实体/关系**先写入 SQLite**（`entities`/`relations`，`entity_type`/`ontology_id` 对应本体定义），**再同步写入 Kùzu**（`Entity`/`Relation` 节点 + `RELATES` 边），由 `entity_service` 保证双库一致。

> 后处理校验作为 LLM 不遵守约束时的兜底，保证最终入库（SQLite + Kùzu）的实体符合本体定义。

***

## 六、前端改动

### 6.1 页面结构与新增组件

本体管理采用"**列表页 → 详情页（四 Tab）**"的两层结构：

- **本体类别列表页** `OntologyCategoryList.vue`：列出所有本体类别（如"金融领域本体"），支持搜索、创建、删除，点击进入详情。
- **本体类别详情页** `OntologyCategoryDetail.vue`：进入某个本体类别后，用四个 Tab 承载四个功能模块，所有操作都在同一本体类别作用域内：
  - **Tab 1 基本信息**：本体类别名称、描述、所属知识库绑定情况。
  - **Tab 2 本体管理**：管理本体（类型定义），每个本体可展开编辑其多个属性。
  - **Tab 3 本体关系定义**：维护关系字典（仅名称 + 描述）。
  - **Tab 4 本体关系设置**：通过三个下拉（起点本体 / 关系 / 终点本体）组合三元组约束，列表展示已有三元组。

| 组件                            | 说明                                          |
| ----------------------------- | ------------------------------------------- |
| `OntologyCategoryList.vue`    | 本体类别列表页（入口）                                 |
| `OntologyCategoryDetail.vue`  | 本体类别详情页，承载四个 Tab                            |
| `CreateOntologyCategoryModal.vue` | 创建本体类别弹窗                                    |
| `OntologyEditor.vue`          | Tab 2：本体列表，新增/删除/重命名本体，可展开进入属性编辑            |
| `OntologyAttributeEditor.vue` | Tab 2 内：某本体的自有属性编辑器（属性名、类型、必填、枚举值），支持整体保存；配合 `OntologyTemplatePicker` 展示合并后完整属性 |
| `RelationDictEditor.vue`      | Tab 3：关系字典编辑器（新增/删除/重命名关系，仅名称与描述）           |
| `ConstraintEditor.vue`        | Tab 4：三元组组合器，三个可搜索下拉选择起点本体/关系/终点本体，新增与删除三元组 |
| `OntologyCategorySelect.vue`  | 知识库详情页中，绑定的本体类别下拉框                          |

**属性模板管理**（全局，独立菜单，跨本体类别复用）：

| 组件                            | 说明                                          |
| ----------------------------- | ------------------------------------------- |
| `AttributeTemplateList.vue`    | 属性模板列表页：CRUD 模板，每个模板可展开编辑其属性（结构同本体属性编辑器）      |
| `OntologyTemplatePicker.vue`   | Tab 2 内：本体引用属性模板的多选选择器，展示合并后的完整属性预览与同名冲突提示    |

> 属性模板是全局资源，不归属任何本体类别，单独菜单管理。本体编辑时通过 `OntologyTemplatePicker` 引用一个或多个模板，最终属性 = 模板属性 + 本体自有属性（合并预览见 `merged-attributes` 接口）。

**实体管理**（实例层，独立菜单，数据源为 SQLite `entities`/`relations`）：

| 组件                    | 说明                                                          |
| --------------------- | ----------------------------------------------------------- |
| `EntityListPage.vue`    | 实体列表页：分页、按名称搜索、按知识库/本体（类型）过滤；支持编辑、删除（同步 Kùzu） |
| `EntityDetailPage.vue`  | 实体详情页：展示并编辑实体属性值（对齐本体属性定义）、查看来源文件与关联关系        |
| `RelationListPage.vue`  | 关系实例列表页：浏览/搜索/删除实体间关系实例（同步 Kùzu）               |

> 三个下拉建议使用可搜索的下拉组件（符合用户偏好），其中"起点本体"和"关系"选定后，"终点本体"可联动过滤出该关系在已有三元组中允许的目标，减少误配。

### 6.2 路由新增

```javascript
// router/index.js 新增路由
{
  path: '/ontology-categories',
  name: 'OntologyCategoryList',
  component: OntologyCategoryList
},
{
  path: '/ontology-categories/:categoryId',
  name: 'OntologyCategoryDetail',
  component: OntologyCategoryDetail
},
{
  path: '/attribute-templates',
  name: 'AttributeTemplateList',
  component: AttributeTemplateList
},
{
  path: '/entities',
  name: 'EntityListPage',
  component: EntityListPage
},
{
  path: '/entities/:entityId',
  name: 'EntityDetailPage',
  component: EntityDetailPage
},
{
  path: '/relations',
  name: 'RelationListPage',
  component: RelationListPage
}
```

### 6.3 菜单新增

在现有菜单中新增"本体管理"、"属性模板"与"实体管理"三个菜单项，放在"知识库"菜单旁边或下方。"属性模板"为全局模板管理入口（不归属本体类别）；"实体管理"用于浏览/编辑抽取后的实体与关系实例（数据来自 SQLite `entities`/`relations`）。

### 6.4 API 层新增

```javascript
// api/index.js 新增 —— 按七个模块组织（本体类别/本体/关系定义/关系设置/属性模板/实体实例/关系实例）

// 模块一：本体类别
fetchOntologyCategories({ q = '' } = {})
createOntologyCategory({ name, description })
updateOntologyCategory(categoryId, { name, description })
deleteOntologyCategory(categoryId)
getOntologyCategoryDetail(categoryId)   // 含本体/属性/关系/三元组完整结构

// 模块二：本体（类型定义）+ 属性
fetchOntologies(categoryId)
addOntology(categoryId, { name, description, color })
batchAddOntologies(categoryId, items)
updateOntology(categoryId, ontologyId, data)
deleteOntology(categoryId, ontologyId)
getOntologyAttributes(categoryId, ontologyId)
addOntologyAttribute(categoryId, ontologyId, data)
updateOntologyAttribute(categoryId, ontologyId, attrId, data)
deleteOntologyAttribute(categoryId, ontologyId, attrId)
replaceOntologyAttributes(categoryId, ontologyId, { attributes })  // 整体保存

// 模块三：本体关系定义（关系字典）
fetchRelations(categoryId)
addRelation(categoryId, { name, description })
batchAddRelations(categoryId, items)
updateRelation(categoryId, relationId, data)
deleteRelation(categoryId, relationId)

// 模块四：本体关系设置（三元组）
fetchConstraints(categoryId)
addConstraint(categoryId, { source_ontology_id, relation_id, target_ontology_id, description })
batchAddConstraints(categoryId, items)
updateConstraint(categoryId, constraintId, data)
deleteConstraint(categoryId, constraintId)

// 模块五：属性模板管理（全局，跨本体类别复用）
fetchAttributeTemplates({ q = '' } = {})
getAttributeTemplate(templateId)
createAttributeTemplate({ name, description })
updateAttributeTemplate(templateId, { name, description })
deleteAttributeTemplate(templateId)
addTemplateAttribute(templateId, data)
updateTemplateAttribute(templateId, attrId, data)
deleteTemplateAttribute(templateId, attrId)
replaceTemplateAttributes(templateId, { attributes })  // 整体保存

// 本体引用属性模板（多对多）
getOntologyTemplates(categoryId, ontologyId)
setOntologyTemplates(categoryId, ontologyId, { template_ids })  // 整体替换
getMergedAttributes(categoryId, ontologyId)  // 合并后的完整属性预览

// 知识库绑定本体类别
getKbOntology(kbId)
setKbOntology(kbId, categoryId)
removeKbOntology(kbId)

// 模块六：实体实例管理（实体管理菜单数据源，增删改同步 Kùzu）
fetchEntities({ kb_id, ontology_id, q, page, page_size } = {})
getEntityDetail(entityId)
updateEntity(entityId, { name, description, properties })
deleteEntity(entityId)

// 模块七：关系实例管理
fetchRelationInstances({ kb_id, relation_type, q, page, page_size } = {})
getRelationInstance(relationId)
deleteRelationInstance(relationId)
```

### 6.5 知识库详情页改动

- 在 `KbDetail.vue` 中增加"本体设置"区域
- 显示当前绑定的本体类别名称
- 提供可搜索下拉选择本体类别/解绑操作
- 已绑定时，文件处理按钮旁显示提示，并展示该本体类别的本体/关系/三元组概要（只读），便于用户确认约束范围

### 6.6 实体管理界面

"实体管理"菜单用于浏览与维护**抽取后的实体/关系实例**（数据来自 SQLite `entities`/`relations`，编辑时同步 Kùzu）：

- **实体列表页** `EntityListPage.vue`：
  - 分页展示实体，支持按名称搜索、按知识库过滤、按本体（类型）过滤
  - 每行显示：实体名、本体类型、所属知识库、来源文件
  - 操作：进入详情编辑、删除（二次确认，同步删除 Kùzu 节点）
- **实体详情页** `EntityDetailPage.vue`：
  - 展示并编辑实体属性值（表单按本体属性 `ontology_attributes` 定义动态渲染：string/number/date/enum 等）
  - 展示该实体参与的关系实例（作为起点/终点）
  - 展示来源文件/分块
- **关系实例列表页** `RelationListPage.vue`：
  - 分页展示关系实例（起点实体 → 关系 → 终点实体）
  - 支持按知识库、关系类型过滤，按实体名搜索
  - 操作：删除关系实例（同步删除 Kùzu 的 `RELATES` 边）

> 实体管理界面的编辑/删除操作经 `entity_service` 同步到 Kùzu，保证图谱可视化与 SQLite 列表一致。新增实体/关系主要由文件处理抽取自动生成，界面以浏览与修正为主。

***

## 七、开发任务

### 阶段一：后端本体定义层

1. `models.py` 新增 9 个定义层 ORM 模型（`OntologyCategory`、`Ontology`、`OntologyAttribute`、`OntologyRelation`、`OntologyRelationConstraint`、`KbOntologyBinding`、`OntologyAttributeTemplate`、`OntologyTemplateAttribute`、`OntologyTemplateBinding`）
2. `schemas.py` 新增本体定义层请求/响应 DTO（含属性、属性模板与三元组）
3. `database.py` 建表逻辑包含 11 张表（**不建外键**）
4. `sql/schema.sql` + `sql/migrations.sql` 新增 11 张表建表语句
5. 新增 `services/ontology_service.py` 业务逻辑（逻辑关联维护、应用层级联删除、三元组唯一性校验、属性模板合并与跨领域复用、整树加载）
6. 新增 `routers/ontology.py` API 路由（资源名 `ontology-categories`，按五个模块（含属性模板）+ 绑定组织）
7. `server.py` 注册 `ontology_router`

### 阶段二：后端实体实例层 + 抽取集成

1. `models.py` 新增 2 个实例层 ORM 模型（`Entity`、`Relation`）
2. `schemas.py` 新增实体/关系实例 DTO + `OntologyConstraint` 模型（含 `ontologies`/`attributes`/`relation_constraints`）
3. 新增 `services/entity_service.py` 实体/关系实例 CRUD + Kùzu 双写同步
4. 新增 `routers/entity.py` 实体/关系实例 API 路由（实体管理菜单数据源）
5. `server.py` 注册 `entity_router`
6. `graph_extraction_service.py` 动态 Prompt（属性已合并模板属性 + 自有属性，含三元组）+ 后处理校验；校验通过后写 SQLite `entities`/`relations` 并同步 Kùzu
7. `file_service.py` 处理文件时自动查询 KB 绑定的本体类别并传递约束
8. 扩展 `KuzuGraphAdapter` 的 `Entity`/`Relation` 节点 schema 与写入逻辑，承载本体定义的动态属性；提供实例同步增删改接口

### 阶段三：前端本体管理界面

1. 本体类别列表页 `OntologyCategoryList.vue`
2. 本体类别详情页 `OntologyCategoryDetail.vue`（四 Tab 框架）
3. Tab 2 本体管理：`OntologyEditor.vue` + `OntologyAttributeEditor.vue` + `OntologyTemplatePicker.vue`（引用属性模板 + 合并预览）
4. Tab 3 关系定义：`RelationDictEditor.vue`
5. Tab 4 关系设置（三元组）：`ConstraintEditor.vue`
6. 属性模板列表页 `AttributeTemplateList.vue`（全局，CRUD 模板与模板属性）
7. 知识库详情页增加本体类别绑定区域
8. `api/index.js` 新增本体 + 属性模板相关 API
9. 路由和菜单新增"本体管理"与"属性模板"入口

### 阶段四：前端实体管理界面

1. 实体列表页 `EntityListPage.vue`（分页/搜索/按知识库与本体类型过滤）
2. 实体详情页 `EntityDetailPage.vue`（按本体属性定义动态渲染属性值表单）
3. 关系实例列表页 `RelationListPage.vue`
4. `api/index.js` 新增实体/关系实例 API
5. 路由和菜单新增"实体管理"入口

***

## 八、风险与注意事项

1. **无外键的引用一致性**：表间不使用数据库外键，所有级联删除/引用维护由 `ontology_service`/`entity_service` 在应用层完成。删除本体类别、本体、关系定义时，必须同时清理其下全部子记录（本体→属性、关系定义→三元组、本体→引用它的三元组与实体实例），否则产生孤儿数据。建议级联删除用事务包裹，并补充单元测试覆盖。
2. **双库一致性（SQLite ↔ Kùzu）**：实体/关系实例以 SQLite 为权威存储并同步 Kùzu。若同步 Kùzu 失败，会出现 SQLite 已写入而图谱缺失的不一致。`entity_service` 需保证"先 SQLite 后 Kùzu"，失败时回滚或记录补偿任务，并在实体管理界面提供"重新同步图谱"入口。
3. **实体管理与重新抽取的冲突**：用户在实体管理界面手动修正的实体/关系，再次处理同文件时会被抽取结果覆盖。需提示"已手动编辑的实体在重新抽取时是否保留"，或提供合并策略。
4. **本体修改后的已有数据**：修改或删除本体（含属性、关系、三元组）后，已抽取的实体/关系不会自动更新，需用户手动重新处理文件。
5. **LLM 约束遵循的可靠性**：即使在 Prompt 中明确约束了本体类型、属性与三元组，LLM 也可能偶尔输出不符合的结果。Python 端的后处理校验（类型过滤、三元组匹配、属性规整）作为兜底。
6. **三元组与关系字典的一致性**：删除关系定义或本体时会级联删除引用它的三元组，需在前端给出二次确认提示，避免误删导致约束缺失。
7. **属性抽取的稳定性**：LLM 对结构化属性（尤其是 `enum`/`date`/`number`）的输出质量不如类型稳定，后处理需做类型规整，并允许部分属性为空。
8. **向后兼容**：无本体绑定时行为完全不变，已有知识库和文件不受影响。
9. **属性模板与引用一致性**：属性模板被多个本体引用，修改模板属性（改名、改类型、删除属性）会级联影响所有引用它的本体的合并属性，进而影响后续抽取与已抽取实体的属性对齐。需在模板修改/删除时提示影响范围；同名冲突（模板属性与本体自有属性同名）由 service 层按"自有属性优先"合并并提示。系统内置模板（`is_system=1`）禁止删除以防误操作。

