# RAG 评测方案（RAGAS 框架）

> 评测对象：`services/rag_service.py`（知识问答混合检索链路）与 `services/oag_service.py`（OAG 本体增强链路）。
> 工具：[ragas](https://docs.ragas.org) + 项目自有 providers/服务直调。
> 配套脚本：`backend/scripts/rag_eval/eval_rag_ragas.py`（一键入口：`backend/scripts/rag_eval/run_eval_flight_ops.py`）。

---

## 1. 评测对象与链路阶段

```
用户问题
  │
  ├─ ① 查询改写（Multi-Query）        QUERY_REWRITE_ENABLED / QUERY_REWRITE_COUNT
  │
  ├─ ② 双路召回                      BM25_ENABLED / BM25_RECALL_K
  │     向量（Chroma/Milvus, k=50）   SIMILARITY_THRESHOLD
  │     BM25（jieba + rank-bm25）
  │
  ├─ ③ RRF 融合                      HYBRID_RRF_K / HYBRID_TOP_N
  │
  ├─ ④ Rerank 精排                   RERANK_ENABLED / RERANK_MODEL / RERANK_TOP_N
  │
  └─ ⑤ LLM 生成（带 [来源N] 引用）    RAG_SYSTEM_PROMPT / LLM_MODEL / LLM_TEMPERATURE
```

OAG 链路差异：②③ 中追加图谱召回（实体 MENTIONS 反查分片），⑤ 注入【图谱事实】。
两条链路可用**同一评测集**横向对比。

## 2. 指标体系与链路映射

ragas 把 RAG 质量拆成「检索质量」与「生成质量」两层，正好对应本项目的阶段开关：

| 阶段 | ragas 指标 | 回答的问题 | 主要受哪些配置影响 |
|---|---|---|---|
| 检索 | `LLMContextPrecisionWithReference`（context_precision） | 相关分片是否排在上下文前列（排序质量） | HYBRID_RRF_K、RERANK_*、HYBRID_TOP_N |
| 检索 | `LLMContextRecall`（context_recall） | 答案需要的信息是否都被召回（漏检率） | SIMILARITY_THRESHOLD、BM25_*、分块大小、QUERY_REWRITE_* |
| 生成 | `Faithfulness` | 答案是否忠于上下文（幻觉率的反面） | RAG_SYSTEM_PROMPT 约束、上下文噪声、LLM_TEMPERATURE |
| 生成 | `ResponseRelevancy`（answer_relevancy） | 答案是否切题、信息量是否足够 | ①改写质量、⑤生成模型 |
| 参考 | `FactualCorrectness` | 与标准答案的事实一致性 | 端到端 |
| 参考 | `NoiseSensitivity` | 噪声分片混入是否拖垮答案 | 阈值过滤、TOP_N 过大 |

默认跑前四个；`FactualCorrectness` / `NoiseSensitivity` 成本较高，按需加。

> Faithfulness 与本项目「[来源N] 引用标注」天然契合：faithfulness 低说明答案里有上下文撑不住的断言，即引用注了但内容编了，或没注来源。

## 3. 评测集建设

评测集为 JSONL，每行一条：

```json
{"question": "问题", "reference": "标准答案（ground truth）", "kb_id": "可选，缺省用命令行 --kb-id"}
```

两种建集方式（可混用）：

1. **人工金标集（推荐起步）**：50～100 条，覆盖四类问题——
   - 事实型（"X 的定义是什么"）
   - 汇总型（"Y 有哪些步骤"）
   - 关键词型（含专有名词/型号/编号，考察 BM25 价值）
   - 否定型（知识库中没有答案，看 faithfulness / 拒答行为）
   模板见 `backend/scripts/rag_eval/eval_data/golden_set.sample.jsonl`。
2. **ragas 合成集（扩量）**：`gen-testset` 子命令从 `list_kb_documents(kb_id)` 抽分片，用 TestsetGenerator 生成 QA 对。中文生成质量需人工抽查后再入库。

原则：评测集版本化（进 git），**金标答案不来自被测系统本身**；固定 seed，改动检索配置后用同一集对比。

> **实测教训（2026-09-13）**：首版 golden set 八成问题超出语料范围（签派放行、除冰、MEL 等民航规章通用知识），reference 只能按通用知识编写，context_recall 被拉低到 0.74；锚定语料逐字核对重写后（17 正例覆盖全部章节 + 2 条 KB 外负例），同一链路 recall 升至 0.96。**评测集超纲会把"检索没问题"错判成"检索差"**——建集前先通读语料再出题。

## 4. 怎么跑

脚本直调 `RAGService.query()`（不绕 HTTP，绕开鉴权/流式解析，且能拿到 chunks 明细），
自动 `chdir` 到 `backend/`，复用 `.env` 与所有 providers。

```powershell
cd backend

# 0) 环境与依赖（本项目实测用 conda py11env，ragas 已装齐）
conda activate py11env        # 或免激活：D:\installedSoftware\anaconda3\envs\py11env\python.exe <脚本> ...
pip install "ragas>=0.2.6" datasets pandas   # 首次使用才需要

# 一键评测「航班运行处置建议」知识库（--kb-name 缺省「航班运行处置建议」，评测集行内 kb_name/kb_id 优先）
python scripts/rag_eval/run_eval_flight_ops.py
python scripts/rag_eval/run_eval_flight_ops.py --limit 5     # 冒烟：只跑前 5 题
python scripts/rag_eval/run_eval_flight_ops.py --ablation    # 完整链路 + baseline + no-bm25 对比

# 手动方式（其他知识库同理；--kb-id / --kb-name 二选一作缺省，评测集行内优先）
python scripts/rag_eval/eval_rag_ragas.py run --kb-id <KB_ID> --testset scripts/rag_eval/eval_data/flight_ops_advisory_golden.jsonl
python scripts/rag_eval/eval_rag_ragas.py run --kb-name <知识库名> --testset scripts/rag_eval/eval_data/golden.jsonl

# 1)（可选）从知识库分片合成评测集
python scripts/rag_eval/eval_rag_ragas.py gen-testset --kb-id <KB_ID> --size 20 --out scripts/rag_eval/eval_data/testset_auto.jsonl

# 2) 只采集不算分（先验证链路产物，省评估 token；事后用 score 补算）
python scripts/rag_eval/eval_rag_ragas.py run --kb-id <KB_ID> --testset golden.jsonl --skip-score

# 3) 消融：关掉某一路再跑（--tag 区分结果）
python scripts/rag_eval/eval_rag_ragas.py run --kb-id <KB_ID> --testset golden.jsonl --no-bm25 --tag nobm25

# 4) 只重算分（换指标/换评估模型，免重跑链路、省 token）
python scripts/rag_eval/eval_rag_ragas.py score --results scripts/rag_eval/eval_out/results_run_xxx.jsonl --metrics faithfulness,factual_correctness

# 5) 评估模型用更强的外部模型（默认复用项目 LLM）
python scripts/rag_eval/eval_rag_ragas.py score --results results.jsonl --eval-model deepseek-chat --eval-base-url https://api.deepseek.com
```

> 评测集行内可写 `"kb_name": "航班运行处置建议"`（按名称模糊匹配，唯一命中才通过），也可写 `"kb_id"`，或由命令行 `--kb-name/--kb-id` 提供缺省值；三者的优先级为 行内 kb_name > 行内 kb_id > 命令行缺省。

输出：`backend/scripts/rag_eval/eval_out/` 下 `results_<tag>_<时间戳>.jsonl`（逐条明细 + 配置快照 `.config.json`）与 `report_<tag>_<时间戳>.csv`（ragas 逐条得分）。示例：`results_run_20260913_175213.jsonl` / `report_score_20260913_181903.csv`。

> `run` 时每条问题真实过一遍检索+生成，50 条约几分钟（取决于 LLM）；建议评测期把 `LLM_TEMPERATURE` 调低（`--llm-temperature 0.1`）减少波动。实测参考：19 条全量（生成 + 76 项 judge 判分）约 35 分钟。

## 5. 消融实验矩阵

同一评测集逐组对比，量化每个开关的边际收益：

| 组 | 命令附加参数 | 验证目标 |
|---|---|---|
| baseline（纯向量） | `--no-bm25 --no-rewrite --no-rerank` | 底线 |
| +BM25 混合 | `--no-rewrite --no-rerank` | BM25 增益（关键词型问题应显著） |
| +查询改写 | `--no-bm25 --no-rerank --enable-rewrite` | Multi-Query 增益（看 context_recall） |
| +Rerank | `--no-rewrite --enable-rerank` | 精排增益（看 context_precision） |
| 完整链路 | 无 | 生产配置 |
| OAG vs RAG | 调 `services.oag_service`（可在脚本中替换被测入口） | 图谱召回增益 |

## 6. 结果解读 → 调参映射

| 症状 | 优先排查 |
|---|---|
| context_recall 低 | `SIMILARITY_THRESHOLD` 过高滤掉了结果；BM25 关闭；分块过大（`CHUNK_SIZE`）把答案切碎 |
| context_precision 低 | `HYBRID_RRF_K`/`HYBRID_TOP_N` 配比；Rerank 未开或 `RERANK_CANDIDATE_K` 太小 |
| faithfulness 低 | 上下文噪声多（缩 TOP_N）；`RAG_SYSTEM_PROMPT` 约束不够硬；温度太高 |
| response_relevancy 低 | 改写偏题（关 QUERY_REWRITE 对比）；生成模型对中文提示遵循差 |
| noise_sensitivity 高 | 阈值过滤失效；相关分片被噪声淹没（调 RERANK） |

## 7. 成本与注意事项

- 评估本身也是 LLM 调用：50 题 × 4 指标 ≈ 数百次调用，评估模型建议用强模型（`--eval-model`），弱评估模型会把指标拉低失真。
- 脚本已设 `raise_exceptions=False` + `RunConfig(max_retries=3)`：单条评估失败不打断整体，失败项计 NaN。
- 评估器 embedding 复用项目 `create_embeddings()`，保证 response_relevancy 语义空间与线上一致。
- Windows 控制台编码已在脚本内兜底（UTF-8 reconfigure）。
- ragas 版本间 API 有差异，脚本对 `ResponseRelevancy/AnswerRelevancy`、`generate_with_langchain_docs/generate_with_llms` 做了双版本兼容；报 ImportError 时按提示升级 ragas。

## 8. 后续可持续化

- 每次调检索参数/换模型后，`run` + `score` 出对比表，沉淀到本目录（如 `reports/2026-09-rerank-on.csv`）。
- 评测集、results JSONL、config 快照一起版本化，保证可复现。
- 进阶：把 `run` 包一层 pytest 标记，作为发布前回归门槛（faithfulness ≥ 0.85 之类阈值）。

## 9. 实测记录（2026-09-13 基线）

### 9.1 实际执行的命令

在 `backend/` 目录下，conda `py11env` 环境（免激活，直接用绝对路径解释器）：

```powershell
D:\installedSoftware\anaconda3\envs\py11env\python.exe -u scripts\rag_eval\eval_rag_ragas.py run `
  --kb-id a20a9a0a6492 `
  --testset scripts\rag_eval\eval_data\flight_ops_advisory_golden.jsonl `
  > scripts\rag_eval\eval_out\run_rerun.log 2>&1
```

- 评测集：`flight_ops_advisory_golden.jsonl`，19 条 = 17 条锚定语料正例 + 2 条 KB 外负例（行李赔偿/退改签）
- 规模：19 题 × 4 指标 = 76 项 judge 判分，全量约 35 分钟（生成约 15 min + judge 约 20 min）
- 等价的快捷方式：`python scripts/rag_eval/run_eval_flight_ops.py`（`--kb-name` 缺省即该知识库）

### 9.2 结果汇总

| 指标 | 首版集（八成超纲） | 本次锚定集 | 变化 |
|---|---|---|---|
| faithfulness | 0.8698 | **0.9707** | +10.1pt，生成侧基本无幻觉 |
| context_recall | 0.7434 | **0.9561** | +21.3pt，检索没坏，是旧考卷超纲 |
| answer_relevancy | 0.7866 | 0.8014 | 正例均值 ≈0.885，被 2 条负例拒答（0 分，正确行为）拉低 |
| llm_context_precision | 0.5800 | 0.4994 | 仅 2 条有有效值（4 个 judge 超时），样本不足不具可比性 |

**核心结论：检索本身没坏，是考卷考了语料里没有的东西。** reference 锚定语料后 recall 0.74→0.96、faithfulness 0.87→0.97。

### 9.3 逐条明细里的三个发现

1. **两条负例 answer_relevancy = 0.0 是正确行为**：系统对 KB 外问题拒答，RAGAS 反推不出原问题自然 0 分——恰证明拒答生效，而非链路故障。
2. **context_precision 失真**：76 项 judge 有 4 项超时（`raise_exceptions=False` 兜住），precision 仅剩 2 条有效值（Q6 处置原则 0.27、Q9 0.73），下轮应换更强/更快的评估模型重跑 `score`。
3. **真实优化点（非 bug）**：Q8（微关门）recall 0.5——处置建议与"牵头部门"句落在相邻两个 chunk，只召回一半；Q6 的"处置原则" chunk 排序靠后。都是章节切分边界导致，可通过调 `CHUNK_SIZE`/overlap 或按小节切块改善。

### 9.4 结果文件

| 文件 | 内容 |
|---|---|
| `eval_out/results_run_20260913_175213.jsonl` | 逐条采集明细（question/reference/contexts/answer/latency） |
| `eval_out/results_run_20260913_175213.config.json` | 链路配置快照（复现用） |
| `eval_out/report_score_20260913_181903.csv` | ragas 逐条得分 |
| `eval_out/run_rerun.log` | 运行日志 |
