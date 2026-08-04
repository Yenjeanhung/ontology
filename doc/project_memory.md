# 项目记忆（Project Memory）

> 本文档记录 KnowSource 本体管理功能的设计约束、工程约定与关键决策，供团队参考并保持开发一致性。完整设计详情见 [design.md](./design.md)。

## 硬性约束（Hard Constraints）

- **不使用数据库外键约束**；通过 `*_id` 字段在应用层（service 层）维护逻辑关联与级联删除
- **实体实例必须存入 SQLite 表**（`entities` 和 `relations`），供"实体管理"菜单访问
- 严格区分**本体定义层**（所有 `ontology_*` 表）与**实体实例层**（`entities`/`relations` 表）
- **本体类别代表领域/业务场景**（如金融、军工、医疗），而非知识库；多个知识库可共享同一个本体类别
- **跨领域共性属性通过"属性模板"复用**（全局，不归属任何本体类别）

## 工程约定（Engineering Conventions）

- 共新增 **11 张 SQLite 表**（无外键）：6 张本体定义层 + 3 张属性模板 + 2 张实体实例层
- **本体定义层表**：`ontology_categories`、`ontologies`、`ontology_attributes`、`ontology_relations`、`ontology_relation_constraints`、`kb_ontology_bindings`
- **属性模板表**（全局，跨领域复用）：`ontology_attribute_templates`、`ontology_template_attributes`、`ontology_template_bindings`
- **实体实例层表**：`entities`（存抽取后的实体实例及属性）、`relations`（存关系实例）
- 实体实例同时写入 **Kùzu 图数据库**，供图谱可视化与图遍历（SQLite 为权威存储，Kùzu 同步）
- **属性合并规则**：本体最终属性 = 模板属性（按 `sort_order` 合并）+ 本体自有属性；同名冲突时**本体自有属性优先**（service 层合并并提示冲突）；抽取 Prompt 与后处理校验使用合并后的完整属性列表
- 属性模板通过**独立全局菜单**与 `/api/attribute-templates` 路由管理；本体通过多对多关联（`ontology_template_bindings`）引用模板；系统内置模板（`is_system=1`）不可删除
- API 资源组织在 `/api/ontology-categories` 下，本体与属性作为子资源；实体/关系实例在 `/api/entities`、`/api/relations`

## 关键决策记录

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 表间关联 | 无外键，service 层维护 | 灵活，避免级联约束带来的迁移与删除困难 |
| 实体存储位置 | SQLite（权威）+ Kùzu（图谱）双库 | 实体管理菜单需列表/分页/编辑，Kùzu 需图遍历 |
| 本体分类粒度 | 按领域/业务场景，非知识库 | 避免类别爆炸，支持领域本体复用 |
| 跨领域属性复用 | 属性模板（多对多引用） | 共性属性只维护一份，比继承更松耦合、比复制更不易失同步 |
| 属性同名冲突 | 本体自有属性优先 | 本体可覆盖模板默认，保持领域特化能力 |
