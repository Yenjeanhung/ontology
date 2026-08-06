# 动态本体生成与审核功能设计文档

## 1. 背景与动机

### 现状问题
- 用户上传文档后，必须先绑定一个已创建的本体类别才能进行结构化抽取
- 本体类别需要预先手工定义（本体、属性、关系、三元组约束），门槛高
- 面对新领域/新文档时，用户不知道该创建什么本体，也不知道文档里会出现哪些实体类型
- 已有本体类别覆盖不全，导致抽取时大量实体/关系因"不在白名单"被过滤掉

### 目标
让系统具备"自动理解文档"的能力：
1. **零配置抽取**：上传文档时可选"自由模式"，不绑定任何本体，让 LLM 自由抽取出实体和关系
2. **智能建议本体**：抽取完成后，系统分析抽取结果，**自动聚类生成候选本体类别**（含本体、属性、关系、三元组）
3. **人工审核入库**：用户在审核界面确认/修改后一键入库，新本体类别可立即被其他文档复用
4. **持续学习**：每次审核通过的本体类别都会进入本体库，后续文档可直接绑定

---

## 2. 核心流程

```
┌─────────────┐    ┌──────────────────┐    ┌──────────────────────┐    ┌──────────────┐
│ 上传文档     │───▶│ 自由抽取（无约束） │───▶│ 自动聚类生成候选本体  │───▶│ 审核 → 入库  │
│ 不绑定本体   │    │ 实体/关系/属性值   │    │ 类别/本体/关系/约束   │    │ 绑定到知识库 │
└─────────────┘    └──────────────────┘    └──────────────────────┘    └──────────────┘
```

### 两种抽取模式并存

| 模式 | 触发条件 | 行为 |
|------|---------|------|
| **本体约束模式** | 知识库绑定了本体类别 | 按本体约束过滤实体/关系，Prompt 含约束块 |
| **自由抽取模式** | 知识库未绑定本体，或用户主动选择 | LLM 自由输出实体类型和关系，不过滤 |

两种模式的抽取结果都产出到 `graph_entities` / `graph_relations` 表。区别在于：
- 约束模式：实体的 `ontology_id` 指向已有本体
- 自由模式：实体的 `ontology_id` 为空，`entity_type` 为 LLM 自由输出的类型名

---

## 3. 详细设计

### 3.1 数据模型

#### 新增表：`ontology_suggestions`（本体建议任务）
```sql
CREATE TABLE ontology_suggestions (
    id              TEXT PRIMARY KEY,
    kb_id           TEXT NOT NULL,
    file_id         TEXT,
    status          TEXT NOT NULL,        -- pending / generating / ready / approved / rejected
    source_mode     TEXT NOT NULL,        -- free_extraction / auto_cluster / manual
    suggestion_data TEXT NOT NULL,        -- JSON: 生成的完整本体类别数据
    score           REAL DEFAULT 0,       -- 置信度评分 (0-1)
    review_notes    TEXT DEFAULT '',      -- 审核备注
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT,
    reviewer        TEXT
);
```

#### `suggestion_data` JSON 结构
```json
{
  "category": {
    "name": "建议的类别名称",
    "description": "根据文档内容生成的类别描述",
    "type": "auto_generated"
  },
  "ontologies": [
    {
      "name": "人物",
      "description": "文档中出现的人物实体",
      "attributes": [
        {"name": "姓名", "code": "name", "data_type": "string", "is_required": true},
        {"name": "年龄", "code": "age", "data_type": "number", "is_required": false}
      ],
      "sample_entities": ["张三", "李四"]
    }
  ],
  "relations": [
    {"name": "任职于", "code": "works_at", "description": "任职关系"},
    {"name": "导致", "code": "causes", "description": "因果关系"}
  ],
  "constraints": [
    {"source": "人物", "relation": "任职于", "target": "组织"},
    {"source": "事件", "relation": "导致", "target": "事件"}
  ],
  "stats": {
    "total_entities": 45,
    "total_relations": 23,
    "coverage_ratio": 0.92,
    "confidence": 0.85
  }
}
```

### 3.2 后端 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ontology-suggestions/generate` | 触发生成（基于 file_id 或 kb_id） |
| GET  | `/api/ontology-suggestions?kb_id=xxx` | 列出某知识库的建议 |
| GET  | `/api/ontology-suggestions/{id}` | 获取建议详情 |
| PUT  | `/api/ontology-suggestions/{id}` | 更新建议（修改名称/描述/属性等） |
| POST | `/api/ontology-suggestions/{id}/approve` | 审核通过 → 正式入库为本体类别 |
| POST | `/api/ontology-suggestions/{id}/reject` | 审核拒绝 |
| DELETE | `/api/ontology-suggestions/{id}` | 删除建议 |

#### 审核通过后的处理
1. 将 `suggestion_data` 中的 `category`、`ontologies`、`relations`、`constraints` 写入对应正式表
2. 将建议中涉及的实体的 `ontology_id` 回填到新创建的本体
3. 自动将新类别绑定到知识库
4. 建议状态置为 `approved`

### 3.3 自动聚类算法

**Step 1：收集自由抽取的实体类型**
- 从 `graph_entities` 中收集所有 `ontology_id IS NULL` 的实体
- 按 `entity_type` 分组，统计每个类型的实体数量和属性值分布

**Step 2：LLM 智能归纳**
调用 LLM 将散落的实体类型归纳为本体类别：

```
Prompt 示例：
给定以下从文档中抽取的实体类型及其属性：
- "公司" (15个): {name, industry, scale}
- "人物" (23个): {name, age, role, organization}
- "产品" (8个): {name, model, category}
- "事件" (5个): {date, location, description}

请归纳为最合适的本体类别结构：
1. 建议的类别名称
2. 每个实体类型是否应作为独立本体或合并
3. 每个本体的核心属性（含编码建议和数据类型）
4. 关系类型建议（基于实体共现和语义）
5. 三元组约束建议
```

**Step 3：聚类与评分**
- 相同或相似的实体类型合并（基于 LLM 判断 + 字符串相似度）
- 为每个建议打置信度分（基于实体数量、属性一致性、关系数量）

### 3.4 前端交互设计

#### 页面 1：知识库详情 - 本体绑定区（改造）
```
┌─ 本体设置 ─────────────────────────────────────┐
│  当前绑定: [通用 ▾]                              │
│  [选择已有本体]  [自动生成本体]  [查看建议]      │
│                                                  │
│  💡 未绑定本体时，使用"自由抽取模式"，抽取后可    │
│     点击"自动生成本体"让系统为你建议合适的本体    │
└──────────────────────────────────────────────────┘
```

#### 页面 2：本体建议列表（新增页面）
```
┌─ 本体建议 ──────────────────────────────────────┐
│  知识库: [XXX ▾]  状态: [全部 ▾]  [刷新]         │
│                                                  │
│  ┌ 建议 #1 ─────────────────────────────────┐   │
│  │ 📋 华为竹知了事件分析  置信度: 85%         │   │
│  │ 4个本体 · 6个属性 · 3个关系 · 2个三元组    │   │
│  │ 来源: 自由抽取 · 涉及实体: 45个           │   │
│  │                              [审核] [拒绝] │   │
│  └────────────────────────────────────────────┘   │
│                                                  │
│  ┌ 建议 #2 ─────────────────────────────────┐   │
│  │ ...                                       │   │
│  └────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────┘
```

#### 页面 3：审核编辑器（核心）
```
┌─ 审核本体建议 ──────────────────────────────────┐
│                                                  │
│  类别名称: [华为事件分析        ]  置信度: 85%   │
│  类别描述: [分析华为相关事件的本体体系       ]   │
│                                                  │
│  ── 本体列表 ─────────────────────────────────  │
│  ☑ 人物  (23个实体)                              │
│    属性: 姓名(name)✓ 年龄(age) 角色(role)        │
│          [+添加属性]                             │
│  ☑ 组织  (15个实体)                              │
│    属性: 名称(name) 行业(industry) 规模(scale)   │
│  ☐ 产品  (8个实体)  [x 移除]                     │
│                                                  │
│  ── 关系字典 ─────────────────────────────────  │
│  ☑ 任职于(works_at)  ☑ 属于(belongs_to)         │
│  ☑ 导致(causes)  [+添加关系]                    │
│                                                  │
│  ── 三元组约束 ─────────────────────────────────  │
│  ☑ 人物 ─任职于→ 组织                           │
│  ☑ 事件 ─导致→ 事件                             │
│                                                  │
│  预览: [实体样本] [关系样本]                     │
│                                                  │
│                    [拒绝]  [审核通过并入库]       │
└──────────────────────────────────────────────────┘
```

### 3.5 文件处理流程改造

#### 当前流程
```
上传 → 解析 → 分块 → 抽取(约束模式) → 入库
```

#### 改造后流程
```
上传 → 解析 → 分块 → 判断模式:
                         ├─ 已绑定本体 → 约束模式抽取 → 入库
                         └─ 未绑定本体 → 自由模式抽取 → 入库
                                                        ↓
                                              自动生成建议本体
                                                        ↓
                                              展示建议供审核
                                                        ↓
                                              审核通过 → 回填 ontology_id
```

#### `_process_file_bg` 改造
```python
async def _process_file_bg(...):
    # 1. 判断抽取模式
    binding = await get_kb_binding(kb_id)
    if binding:
        extraction_mode = "constrained"
        constraint = build_constraint(binding)
    else:
        extraction_mode = "free"
        constraint = None

    # 2. 执行抽取
    graph_chunks = await GraphExtractionService.extract(
        chunks, extraction_mode=extraction_mode, constraint=constraint
    )

    # 3. 存储图谱
    await GraphStore.upsert_document_graph(...)

    # 4. 自由模式下自动生成本体建议
    if extraction_mode == "free":
        try:
            await OntologySuggestionService.generate(
                kb_id=kb_id, file_id=file_id,
                extraction_result=graph_chunks
            )
        except Exception:
            logger.exception("Auto ontology suggestion failed (non-critical)")
```

### 3.6 置信度评分算法

```python
score = (
    entity_coverage * 0.4 +      # 抽取实体数 / 文档分块数
    attribute_consistency * 0.3 + # 同类型实体属性字段的一致性
    relation_richness * 0.2 +     # 关系类型数 / 实体类型数
    llm_confidence * 0.1          # LLM 自评分数
)
```

评分 >= 0.7 标记为"高置信度"，可一键入库；0.4-0.7 标记为"需审核"；< 0.4 标记为"建议参考"。

---

## 4. 实施计划

### Phase 1：基础设施（建议 1-2 天）
- [ ] 创建 `ontology_suggestions` 表及迁移脚本
- [ ] 实现 `OntologySuggestionService`（CRUD + 生成逻辑）
- [ ] 实现路由层 API
- [ ] 改造 `_process_file_bg` 支持自由抽取模式
- [ ] 实现自动聚类和 LLM 归纳逻辑

### Phase 2：前端界面（建议 2 天）
- [ ] 本体建议列表页面
- [ ] 审核编辑器组件
- [ ] 知识库详情页"自动生成本体"入口
- [ ] 置信度可视化和预览功能

### Phase 3：审核通过与回填（建议 1 天）
- [ ] 审核通过 → 创建正式本体类别
- [ ] 回填实体的 `ontology_id`
- [ ] 自动绑定到知识库
- [ ] 重新受约束抽取（可选）

### Phase 4：优化与打磨（建议 1-2 天）
- [ ] 建议的去重与合并（同一知识库多次生成）
- [ ] 历史建议对比
- [ ] 已审核本体的迭代更新
- [ ] Prompt 优化和评分校准

---

## 5. 风险与注意事项

1. **LLM 不确定性**：自动生成的本体结构可能不稳定，需要通过多次抽样 + 人工审核兜底
2. **性能影响**：自动生成建议需要额外一次 LLM 调用，在后台异步执行，不阻塞抽取主流程
3. **实体回填**：审核通过后需要批量更新 `graph_entities.ontology_id`，对已有图谱数据做增量更新
4. **版本管理**：同一知识库多次生成建议时需要版本管理，避免混淆
5. **向后兼容**：自由抽取模式是新功能，不影响已有的约束抽取模式

---

## 6. 与现有功能的关系

| 现有功能 | 影响 |
|---------|------|
| 本体管理 | 建议通过审核后自动创建本体类别，与手工创建的本体完全一致 |
| 属性模板 | 建议中的属性自动复用属性模板，减少重复定义 |
| 三元组约束 | 建议中的约束自动注册，供图谱渲染使用 |
| 图谱可视化 | 审核通过后，图谱中的实体自动关联到正确的本体类型 |
| 知识抽取 | 自由模式为约束模式的补充，两种模式可随时切换 |
