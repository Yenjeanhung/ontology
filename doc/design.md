# KnowSource 本体管理功能设计文档

## 一、背景与目标

### 1.1 现状

KnowSource 是一个基于 RAG + 知识图谱的知识库系统，前端使用 Vue 3，后端使用 Python (FastAPI)。当前系统在抽取实体和关系时，LLM 的 System Prompt 中仅列出了固定的实体类型和关系类型作为参考，没有硬性约束，导致每次抽取结果不一致。

### 1.2 目标

在当前系统上增加本体（Ontology）管理功能，用户可以：
- 自定义本体，包含实体类型（EntityType）和关系类型（RelationType）及其约束
- 将本体绑定到知识库
- 文件处理时根据绑定的本体约束抽取实体和关系，使结果符合用户预期

### 1.3 设计原则

- 本体管理功能直接在现有 Python (FastAPI) 后端中实现
- 复用现有的 SQLite 数据库，新增 4 张本体相关表
- 图谱抽取服务根据本体约束动态构建 Prompt，并增加后处理校验
- 无本体绑定时保持现有行为不变，完全向后兼容

---

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
│  │   SQLite 数据库     │  ← 新增 4 张本体表                   │
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
    ├─ 查询 kb_id 绑定的 ontology（调用 OntologyService）
    │
    ├─ 如果有绑定本体：
    │    ├─ 加载 entity_types 和 relation_types
    │    ├─ 构建 ontology_constraint 对象
    │    └─ 传递给 _process_file_bg -> GraphExtractionService.extract
    │
    └─ 如果没有绑定本体：直接处理（保持原有行为）
```

---

## 三、数据库设计（Python SQLite 新增表）

在现有 SQLite 数据库（`backend/data/knowsource.db`）中新增 4 张表。

### 3.1 本体表 `ontologies`

```sql
CREATE TABLE IF NOT EXISTS ontologies (
    id              VARCHAR PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    description     TEXT DEFAULT '',
    is_system       INTEGER NOT NULL DEFAULT 0,
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(name)
);
```

### 3.2 实体类型表 `ontology_entity_types`

```sql
CREATE TABLE IF NOT EXISTS ontology_entity_types (
    id              VARCHAR PRIMARY KEY,
    ontology_id     VARCHAR NOT NULL,
    name            VARCHAR(50) NOT NULL,
    description     VARCHAR(500) DEFAULT '',
    color           VARCHAR(20) DEFAULT NULL,
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(ontology_id, name),
    FOREIGN KEY (ontology_id) REFERENCES ontologies(id) ON DELETE CASCADE
);
```

### 3.3 关系类型表 `ontology_relation_types`

```sql
CREATE TABLE IF NOT EXISTS ontology_relation_types (
    id              VARCHAR PRIMARY KEY,
    ontology_id     VARCHAR NOT NULL,
    name            VARCHAR(50) NOT NULL,
    description     VARCHAR(500) DEFAULT '',
    source_types    TEXT DEFAULT NULL,
    target_types    TEXT DEFAULT NULL,
    created_at      VARCHAR,
    updated_at      VARCHAR,
    UNIQUE(ontology_id, name),
    FOREIGN KEY (ontology_id) REFERENCES ontologies(id) ON DELETE CASCADE
);
```

> **`source_types` / `target_types`**：JSON 格式存储，约束关系的两端实体类型。如"任职于"可指定 `source_types=["人物"]`、`target_types=["组织"]`。NULL 表示不限制。约束在 Prompt 中提示 LLM，并在抽取后校验过滤。

### 3.4 知识库-本体绑定表 `kb_ontology_bindings`

```sql
CREATE TABLE IF NOT EXISTS kb_ontology_bindings (
    id              VARCHAR PRIMARY KEY,
    kb_id           VARCHAR NOT NULL,
    ontology_id     VARCHAR NOT NULL,
    created_at      VARCHAR,
    UNIQUE(kb_id),
    FOREIGN KEY (ontology_id) REFERENCES ontologies(id) ON DELETE CASCADE
);
```

### 3.5 ER 关系

```
ontologies 1 ──N ontology_entity_types
ontologies 1 ──N ontology_relation_types
ontologies 1 ──N kb_ontology_bindings (通过 kb_id 关联 knowledge_bases)
```

---

## 四、API 设计

### 4.1 本体管理 API（Python 新增 Router）

#### 本体 CRUD

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/ontologies` | 获取本体列表（支持 `?q=` 搜索） |
| `GET` | `/api/ontologies/{id}` | 获取本体详情（含实体类型和关系类型） |
| `POST` | `/api/ontologies` | 创建本体 |
| `PUT` | `/api/ontologies/{id}` | 更新本体（名称、描述） |
| `DELETE` | `/api/ontologies/{id}` | 删除本体（级联删除实体类型、关系类型、绑定） |

#### 实体类型管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/ontologies/{ontologyId}/entity-types` | 获取实体类型列表 |
| `POST` | `/api/ontologies/{ontologyId}/entity-types` | 添加实体类型 |
| `PUT` | `/api/ontologies/{ontologyId}/entity-types/{id}` | 更新实体类型 |
| `DELETE` | `/api/ontologies/{ontologyId}/entity-types/{id}` | 删除实体类型 |
| `POST` | `/api/ontologies/{ontologyId}/entity-types/batch` | 批量添加实体类型 |

#### 关系类型管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/ontologies/{ontologyId}/relation-types` | 获取关系类型列表 |
| `POST` | `/api/ontologies/{ontologyId}/relation-types` | 添加关系类型 |
| `PUT` | `/api/ontologies/{ontologyId}/relation-types/{id}` | 更新关系类型 |
| `DELETE` | `/api/ontologies/{ontologyId}/relation-types/{id}` | 删除关系类型 |
| `POST` | `/api/ontologies/{ontologyId}/relation-types/batch` | 批量添加关系类型 |

#### 知识库绑定本体

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/kb/{kbId}/ontology` | 获取知识库绑定的本体 |
| `PUT` | `/api/kb/{kbId}/ontology` | 设置/更新绑定的本体 |
| `DELETE` | `/api/kb/{kbId}/ontology` | 解绑本体 |

### 4.2 关键请求/响应格式

#### 创建本体

```json
POST /api/ontologies
{
    "name": "金融领域本体",
    "description": "用于金融文档的实体和关系抽取"
}
```

#### 批量添加实体类型

```json
POST /api/ontologies/{ontologyId}/entity-types/batch
{
    "items": [
        { "name": "人物", "description": "人名，包括真实人物和虚拟人物", "color": "#e74c3c" },
        { "name": "组织", "description": "公司、机构、政府部门等组织实体", "color": "#3498db" },
        { "name": "金融产品", "description": "基金、股票、债券、保险等金融产品", "color": "#2ecc71" },
        { "name": "指标", "description": "经济指标、财务指标等", "color": "#f39c12" }
    ]
}
```

#### 批量添加关系类型

```json
POST /api/ontologies/{ontologyId}/relation-types/batch
{
    "items": [
        {
            "name": "任职于",
            "description": "人物在某组织担任职务",
            "source_types": ["人物"],
            "target_types": ["组织"]
        },
        {
            "name": "持有",
            "description": "人物或组织持有某金融产品",
            "source_types": ["人物", "组织"],
            "target_types": ["金融产品"]
        },
        {
            "name": "影响",
            "description": "某指标影响另一指标或实体",
            "source_types": ["指标"],
            "target_types": ["指标", "金融产品"]
        }
    ]
}
```

#### 绑定知识库

```json
PUT /api/kb/{kbId}/ontology
{
    "ontology_id": "ontology_abc123"
}
```

---

## 五、Python 后端改动

### 5.1 新增文件

| 文件 | 说明 |
|------|------|
| `routers/ontology.py` | 本体管理 API 路由 |
| `services/ontology_service.py` | 本体业务逻辑 |

### 5.2 修改文件

| 文件 | 改动 |
|------|------|
| `models.py` | 新增 4 个 ORM 模型：`Ontology`、`OntologyEntityType`、`OntologyRelationType`、`KbOntologyBinding` |
| `schemas.py` | 新增本体相关 Pydantic 请求/响应模型 + `OntologyConstraint` 模型 |
| `database.py` | 创建表时包含新的 4 张本体表 |
| `server.py` | 注册 `ontology_router` |
| `sql/schema.sql` | 新增 4 张本体表的建表语句 |
| `sql/migrations.sql` | 新增迁移脚本 |
| `services/file_service.py` | `_process_file_bg` 查绑定的本体并传递给图谱抽取 |
| `services/graph_extraction_service.py` | 接收 `ontology_constraint` 参数，动态构建 Prompt，后处理校验 |

### 5.3 Schema 新增

```python
# backend/schemas.py 新增
class OntologyEntityTypeConstraint(BaseModel):
    name: str
    description: str = ""

class OntologyRelationTypeConstraint(BaseModel):
    name: str
    description: str = ""
    source_types: list[str] | None = None
    target_types: list[str] | None = None

class OntologyConstraint(BaseModel):
    entity_types: list[OntologyEntityTypeConstraint] = []
    relation_types: list[OntologyRelationTypeConstraint] = []
```

### 5.4 图谱抽取改动（核心）

`services/graph_extraction_service.py` 的 `extract` 方法新增可选参数 `ontology_constraint`。

**有约束时的 System Prompt 动态构建**：

```
你是知识图谱抽取助手。

你的任务是从文档分块中抽取实体和实体间关系，并严格输出 JSON。

【本体约束 - 必须严格遵守】
允许的实体类型：
  - 人物：人名，包括真实人物和虚拟人物
  - 组织：公司、机构、政府部门等组织实体

允许的关系类型：
  - 任职于：人物在某组织担任职务（源: 人物 → 目标: 组织）

要求：
1. 仅基于提供文本抽取，不要编造。
2. 实体类型必须从上述允许的类型中选择，不要使用其他类型。
3. 关系类型必须从上述允许的类型中选择，不要使用其他类型。
4. 关系的源实体和目标实体类型必须符合上述约束。
...（其余现有要求不变）
```

**无本体约束时**：保持现有 Prompt 不变，向后兼容。

**抽取后校验**（在 `_merge_payload` 中）：过滤不在约束中的实体类型和关系类型，校验关系两端实体类型是否匹配约束规则。

---

## 六、前端改动

### 6.1 新增页面/组件

| 组件 | 说明 |
|------|------|
| `OntologyList.vue` | 本体列表页，展示所有本体，支持搜索、创建、删除 |
| `OntologyDetail.vue` | 本体详情页，展示和编辑实体类型、关系类型 |
| `CreateOntologyModal.vue` | 创建本体弹窗 |
| `EntityTypeEditor.vue` | 实体类型编辑器（支持添加、删除、批量导入） |
| `RelationTypeEditor.vue` | 关系类型编辑器（支持添加、删除、批量导入、设置源/目标约束） |
| `OntologySelect.vue` | 知识库详情页中，绑定的本体下拉框 |

### 6.2 路由新增

```javascript
// router/index.js 新增路由
{
  path: '/ontologies',
  name: 'OntologyList',
  component: OntologyList
},
{
  path: '/ontologies/:ontologyId',
  name: 'OntologyDetail',
  component: OntologyDetail
}
```

### 6.3 菜单新增

在现有菜单中新增"本体管理"菜单项，放在"知识库"菜单旁边或下方。

### 6.4 API 层新增

```javascript
// api/index.js 新增
fetchOntologies({ q = '' } = {})
createOntology({ name, description })
updateOntology(id, { name, description })
deleteOntology(id)
getOntologyDetail(id)                // 含 entity_types 和 relation_types
fetchEntityTypes(ontologyId)
addEntityType(ontologyId, { name, description, color })
batchAddEntityTypes(ontologyId, items)
updateEntityType(ontologyId, typeId, data)
deleteEntityType(ontologyId, typeId)
fetchRelationTypes(ontologyId)
addRelationType(ontologyId, data)
batchAddRelationTypes(ontologyId, items)
updateRelationType(ontologyId, typeId, data)
deleteRelationType(ontologyId, typeId)
getKbOntology(kbId)
setKbOntology(kbId, ontologyId)
removeKbOntology(kbId)
```

### 6.5 知识库详情页改动

- 在 `KbDetail.vue` 中增加"本体设置"区域
- 显示当前绑定的本体名称
- 提供下拉选择本体/解绑操作
- 已绑定时，文件处理按钮旁显示提示

---

## 七、开发任务

### 阶段一：后端本体管理

1. `models.py` 新增 4 个 ORM 模型
2. `schemas.py` 新增请求/响应 DTO
3. `database.py` 建表逻辑包含新表
4. `sql/schema.sql` + `sql/migrations.sql` 新增建表语句
5. 新增 `services/ontology_service.py` 业务逻辑
6. 新增 `routers/ontology.py` API 路由
7. `server.py` 注册路由

### 阶段二：图谱抽取集成本体约束

1. `schemas.py` 新增 `OntologyConstraint` 模型
2. `graph_extraction_service.py` 支持动态 Prompt + 后处理校验
3. `file_service.py` 处理文件时自动查询 KB 绑定的本体并传递约束

### 阶段三：前端本体管理界面

1. 本体列表页 `OntologyList.vue`
2. 本体详情页 `OntologyDetail.vue`（实体类型/关系类型编辑）
3. 知识库详情页增加本体绑定区域
4. `api/index.js` 新增所有本体相关 API
5. 路由和菜单新增"本体管理"入口

---

## 八、风险与注意事项

1. **本体修改后的已有数据**：修改或删除本体后，已抽取的图谱数据不会自动更新，需用户手动重新处理文件。
2. **LLM 约束遵循的可靠性**：即使在 Prompt 中明确约束了实体和关系类型，LLM 也可能偶尔输出不符合的类型。Python 端的后处理校验作为兜底过滤。
3. **向后兼容**：无本体绑定时的行为完全不变，已有知识库和文件不受影响。
