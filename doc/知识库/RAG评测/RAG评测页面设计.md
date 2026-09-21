# RAGAS 评测页面设计

> 目标：把 `backend/scripts/rag_eval/eval_rag_ragas.py` 的脚本能力升级为 Web 页面，形成「评测集管理 → 在线评测 → 结果留存 → Badcase 标记回流」的完整闭环。

---

## 1. 背景与现状

### 1.1 已有能力（脚本）

`eval_rag_ragas.py` 三个子命令：

| 子命令 | 能力 |
|---|---|
| `run` | 读评测集 JSONL → 逐条跑 `RAGService.query` 采集 (question, contexts, answer) → 顺带 ragas 评分 |
| `score` | 对已保存的 results JSONL 重新算分（换指标/评估模型免重跑链路） |
| `gen-testset` | RAGAS TestsetGenerator 从知识库分片合成评测集 |

支持消融开关（`--no-bm25` / `--no-rewrite` / `--no-rerank`）、评估模型独立配置、结果 JSONL 落盘 + 配置快照。

### 1.2 痛点（做页面的理由）

1. 评测集是本地 JSONL 文件，无法多人协作编辑，上传/修改靠手工改文件
2. 评测结果散落在 `eval_out/results_*.jsonl`，无历史留存与横向对比
3. Badcase 回流是「人工看报告 → 手动改测试集」，无页面化标记与沉淀
4. 消融实验要记命令行参数，配置快照不直观

## 2. 总体设计

```
┌─ 前端 Vue3（评测中心页面）────────────────────────────┐
│  评测集管理 │ 评测任务 │ 结果详情（Badcase标记+回流） │
└──────────────┬───────────────────────────────────┘
               │ REST API
┌──────────────▼───────────────────────────────────┐
│  routers/eval.py  +  services/rag_eval_service.py │
│  （从脚本抽取核心逻辑，脚本改为调用 service）        │
└──────┬──────────────────────┬────────────────────┘
       │                      │
┌──────▼──────┐      ┌────────▼─────────┐
│ PostgreSQL   │      │ RAGService.query │
│ 4 张新表      │      │ ragas evaluate   │
└─────────────┘      └──────────────────┘
```

**核心原则**：评测执行逻辑不重写——把脚本里「跑链路采集」「ragas 评分」「消融开关」抽成 service 函数，CLI 脚本与页面 API 共用同一套实现。

## 3. 数据模型（PostgreSQL，4 张表）

### 3.1 `eval_testset` 评测集

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| name | str | 名称（唯一） |
| description | str | 描述 |
| default_kb_id | FK, nullable | 默认关联知识库（条目可覆盖） |
| source | enum | `manual` / `upload` / `synthesized`（gen-testset 合成） |
| created_at / updated_at | datetime | |

### 3.2 `eval_testset_item` 评测集条目

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| testset_id | FK | 所属评测集 |
| question | text | 问题 |
| reference | text, nullable | 标准答案（缺省时跳过需 reference 的指标） |
| kb_id | FK, nullable | 覆盖评测集默认 KB |
| enabled | bool | 是否参与评测（软禁用，不物理删） |
| origin | enum | `manual` / `upload` / `synthesized` / `badcase`（回流） |
| source_run_item_id | FK, nullable | 回流溯源：来自哪条评测结果 |
| created_at / updated_at | datetime | |

唯一约束：`(testset_id, question, kb_id)` 去重。

### 3.3 `eval_run` 评测任务

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| testset_id | FK | 使用的评测集 |
| kb_id | FK | 缺省知识库 |
| name | str | 任务名（缺省「评测集名+时间」） |
| config_json | JSON | 指标列表、消融开关、llm_config_id（评估所用模型配置）、temperature 等完整配置快照 |
| status | enum | `pending` / `running` / `done` / `failed` / `cancelled` |
| total / done / failed_count | int | 进度计数 |
| metrics_summary_json | JSON, nullable | 各指标均值（结束时写入），如 `{"faithfulness": 0.87, ...}` |
| started_at / finished_at | datetime | |

> 逐条结果冗余存储 question/answer/contexts，天然构成「当时评测集的快照」，无需额外版本表——评测集后续被修改不影响历史 run 的可追溯性。

### 3.4 `eval_run_item` 评测逐条结果

| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| run_id | FK | 所属任务 |
| question / reference | text | 快照自评测集条目 |
| answer | text | 链路生成答案 |
| contexts_json | JSON | 检索上下文列表 |
| retrieval_paths_json | JSON | 每条 context 的检索路径（向量/BM25/RRF） |
| latency_s | float | 单条耗时 |
| error | str, nullable | 执行失败原因 |
| metric_scores_json | JSON, nullable | 逐条各指标得分 |
| is_badcase | bool | 人工标记 |
| badcase_reason | enum, nullable | `检索未召回` / `排序差` / `答案幻觉` / `答非所问` / `标注错误` / `其他` |
| badcase_note | text, nullable | 补充说明 |
| marked_by | FK | 标记人 |
| marked_at | datetime | |

## 4. API 设计（`routers/eval.py`，前缀 `/api/eval`）

### 评测集

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/testsets` | 列表（含条目数、最近一次 run 摘要） |
| POST | `/testsets` | 创建 |
| PUT / DELETE | `/testsets/{id}` | 编辑 / 删除 |
| GET | `/testsets/{id}/items` | 条目分页列表（支持按 origin/enabled/关键词筛选） |
| POST | `/testsets/{id}/items` | 新增单条 |
| PUT / DELETE | `/testsets/{id}/items/{item_id}` | 行内编辑 / 删除（enabled 切换同 PUT） |
| POST | `/testsets/{id}/import` | 上传导入：JSONL / CSV / Excel，返回成功/重复/失败计数 |
| GET | `/testsets/{id}/export?format=jsonl\|csv\|xlsx` | 导出 |

### 评测任务

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/runs` | 创建并启动：`{testset_id, kb_id?, metrics[], ablation{}, llm_config_id?}` |
| GET | `/runs` | 任务列表（支持按 testset 筛选），含状态与指标均值 |
| GET | `/runs/{id}` | 任务详情（进度：done/total） |
| POST | `/runs/{id}/cancel` | 取消（跑完当前条后停止） |
| GET | `/runs/{id}/items?page=&filter=` | 逐条结果分页（filter: 全部/badcase/error/低分） |
| GET | `/runs/{id}/report` | 导出逐条明细 CSV（同脚本 report_xxx.csv） |
| GET | `/runs/{id}/progress` | SSE 进度流（复用工作流引擎的 SSE 模式；MVP 可先用前端轮询 GET /runs/{id}） |

### Badcase 标记与回流

| 方法 | 路径 | 说明 |
|---|---|---|
| PUT | `/runs/{id}/items/{item_id}/badcase` | 标记/取消：`{is_badcase, reason?, note?}` |
| PUT | `/runs/{id}/items/batch-badcase` | 批量标记 |
| POST | `/runs/{id}/backflow` | 回流：`{target_testset_id, run_item_ids[]}`，将已标记条目写为 `origin=badcase` 的新条目（重复 question 自动跳过并在响应中列出） |

### 执行模型

- 复用脚本逻辑：service 内逐条 `RAGService.query` → 采集中途落库（每条完成即写 `eval_run_item`）→ 全部完成后跑 ragas 评分回填 `metric_scores_json` 与 `metrics_summary_json`。中途失败/取消不影响已完成部分的留存（与脚本「单条失败不中断」一致）。
- **评估 LLM 解析**：`llm_config_id` 未传 → 用 `llm_configs` 表生效配置（等价脚本默认行为）；传了 → 按该套配置（provider/api_key/base_url/model）构造独立 `ChatOpenAI` → 包 `LangchainLLMWrapper` 给 ragas。与脚本 `resolve_llm(--eval-model)` 逻辑一致，仅数据源从命令行参数换成 `llm_configs` 表。
- 并发控制：全局同时只允许 1 个 `running` 任务（防 LLM 限流拖垮服务），新任务排队为 `pending`。
- 后台执行：`asyncio.create_task` + DB 状态流转；服务重启后 `running` 任务标记为 `failed`（评测可重跑，成本可接受）。

## 5. 前端页面（`components/eval/`，新增模块）

路由：`/eval`（评测中心），下三个 Tab。

### 5.1 Tab1 评测集管理

```
┌──────────────────────────────────────────────────────┐
│ [+新建] [上传导入] [导出]            搜索框            │
├──────────────────────────────────────────────────────┤
│ 评测集列表（名称/条目数/来源/最近得分/更新时间）→ 点入 │
├──────────────────────────────────────────────────────┤
│ 条目表格：question │ reference │ 来源 │ 状态 │ 操作    │
│  - question/reference 行内编辑（点击变输入框）        │
│  - 来源列标注 manual/upload/synthesized/⚡badcase回流  │
│  - 状态开关（enabled）                                │
└──────────────────────────────────────────────────────┘
```

上传导入支持 JSONL / CSV / Excel，预览前 5 条确认后入库；重复条目（同 question+kb）提示跳过。

### 5.2 Tab2 评测任务

```
┌──────────────────────────────────────────────────────┐
│ [+发起评测]                                          │
│  弹窗：选评测集 → 选知识库 → 勾选指标（默认 faithfulness │
│  +response_relevancy+context_precision+context_recall）│
│  → 消融开关（BM25/查询改写/Rerank）                    │
│  → 评估模型下拉（数据源=模型配置页全部配置，            │
│    默认「跟随生效配置」，可选任意一套，如 DeepSeek）     │
├──────────────────────────────────────────────────────┤
│ 任务列表：名称 │ 状态 │ 进度条 │ 各指标均值 │ 配置快照  │
│  （点配置快照看当时的消融开关与模型，消融对比一目了然）  │
└──────────────────────────────────────────────────────┘
```

### 5.3 Tab3 结果详情（Badcase 闭环核心）

```
┌──────────────────────────────────────────────────────┐
│ 任务头：指标均值卡片（对比同评测集历史 run，涨跌箭头）   │
├──────────────────────────────────────────────────────┤
│ 筛选：[全部│已标记│执行失败│低分]                       │
│ 逐条结果表格：                                        │
│  question │ 各指标分(红黄绿) │ 耗时 │ badcase标记 │     │
│  行展开：answer / 引用contexts（标注检索路径）/ error   │
│  标记操作：☑ 标记badcase → 选原因 → 备注               │
├──────────────────────────────────────────────────────┤
│ 底部操作栏：已选 N 条 → [回流到评测集 ▼选择目标集]       │
└──────────────────────────────────────────────────────┘
```

回流后在评测集条目里可见来源标记（⚡badcase + 溯源链接跳回原 run_item），形成闭环可见性。

## 6. 代码改动清单

| 位置 | 改动 |
|---|---|
| `backend/models.py` | 新增 4 张表 |
| `backend/schemas.py` | 新增对应 Pydantic schema |
| `backend/services/rag_eval_service.py` | **新增**：从脚本抽取 `run_pipeline`（采集）/`score_rows`（ragas评分）/`apply_ablation`/`METRIC_SPECS`，改为 DB 读写 |
| `backend/routers/eval.py` | **新增**：上表全部端点；`server.py` 注册 |
| `backend/scripts/rag_eval/eval_rag_ragas.py` | 改造：核心逻辑改为调用 `rag_eval_service`，CLI 保留（gen-testset、离线跑分场景仍用脚本） |
| `front/src/components/eval/` | **新增**：`EvalCenter.vue`（容器+Tab）、`TestsetPanel.vue`、`RunPanel.vue`、`RunDetailPanel.vue` |
| `front/src/api/eval.js` | **新增**：接口封装 |
| `front/src/router/index.js` | 注册 `/eval` 路由与菜单入口 |

## 7. 不做的（明确边界）

- 不做评测任务并发与分布式调度——单任务串行已满足 3k 规模
- 不做 Agent 全链路评测（工具调用/多轮）——本期只覆盖 RAG 链路，Agent 评测后续单独立项
- 不自动回流——回流必须人工标记触发（防止脏数据污染回归集），页面只提供便捷标记

## 8. 里程碑

1. **M1 后端**：表模型 + service 抽取 + 评测集 CRUD/导入导出 API（脚本改造兼容）
2. **M2 后端**：评测任务异步执行 + 结果留存 + badcase 标记/回流 API
3. **M3 前端**：三个 Tab 页面 + API 对接
4. **M4 收尾**：历史 run 指标对比、CSV 报告导出、联调回归（跑一次全量评测验证与脚本结果一致）
