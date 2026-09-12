# RAG 面试题 · 全量精简汇总

> 覆盖 RAG 全链路：基础 → 文档处理 → 检索 → 生成 → 评估 → 优化进阶。项目检索链路：**查询改写 → 向量 + BM25 混合召回 → RRF 融合 → Rerank 精排**。

# 一、RAG 基础

## Q1 什么是 RAG，为什么需要它

检索增强生成 (Retrieval-Augmented Generation, RAG) = 先从外部知识库检索相关内容，再拼进 prompt 让 LLM 基于资料回答。解决 LLM 三个问题：知识过时（训练截止后的事不知道）、幻觉（编造事实）、私有知识（企业内部文档没在训练语料里），且答案可溯源。

## Q2 RAG vs 微调怎么选

知识频繁更新、需要溯源、低成本快速上线 → RAG；改变模型行为风格/领域语言理解 → 微调；两者不冲突，可微调后再接 RAG。RAG 优势是知识热更新（改文档即生效）+ 可溯源 + 无需训练成本；微调优势是内化领域语言风格、降低 prompt 负担。

## Q3 RAG 完整链路

离线：文档加载 → 解析清洗 → 分块 → embedding → 存向量库（+ 建倒排/图谱索引）；在线：query 改写 (Query Rewriting) → 混合召回 (Hybrid Retrieval) → 融合排序 (RRF, Reciprocal Rank Fusion) → 精排 (Rerank) → 拼 prompt → LLM 生成 → 引用溯源输出。

# 二、文档处理与索引

## Q4 分块策略有哪些

**基础切分策略：**

| # | 策略 | 原理 | 适用条件 | 优点 | 缺点 |
|---|---|---|---|---|---|
| 1 | **固定长度切分** (Fixed-size Chunking) | 按固定字符/token 数硬切，切完拉倒 | 纯文本、原型阶段快速验证 | 实现极简，块大小可控 | 完全不管语义边界，一句话可能被切成两半；不适合生产 |
| 2 | **按句子/段落切分** (Sentence/Paragraph Splitting) | 依据句号/换行等自然语言标点分割 | 普通文章、报告、邮件、博客 | 零成本保住语义边界完整 | 块大小不可控，长段落会超限 |
| 3 | **递归字符切分** (Recursive Character Splitting) | 按 `\n\n`→`\n`→句号→空格 分隔符**层级递归**切，优先保段落、超限再降级 | 通用文档默认选择（LangChain `RecursiveCharacterTextSplitter`） | 语义完整性与块大小控制间取得平衡，工程最常用 | 对表格/代码等强结构内容效果一般 |
| 4 | **滑动窗口/重叠切片** (Sliding Window/Overlap) | 固定长度基础上设置 overlap（通常 chunk_size 的 10%~20%） | 技术文档、合同等句意跨行的文本 | 防止关键信息在边界被切断 | 块数变多、内容冗余，存储和索引成本上升 |

**进阶语义策略：**

| # | 策略 | 原理 | 适用条件 | 优点 | 缺点 |
|---|---|---|---|---|---|
| 5 | **语义切分** (Semantic Chunking) | 相邻句子算 embedding 相似度，在**语义骤降处**（话题转换点）切开 | FAQ、客服日志、调研报告 | 语义完整度最好，块内主题聚焦 | 全文要跑 embedding，慢且贵；切出的块大小不规则 |
| 6 | **层级/标题切分** (Hierarchical/Heading Chunking) | 解析文档目录或 Markdown 标题层级（H1~H6），按章节结构切 | 教材、技术手册、带目录的 PDF | 尊重文档逻辑，可携带层级元数据过滤 | 依赖解析质量；超长章节仍需二次切分 |
| 7 | **父子分块** (Parent-Child Indexing / Small-to-Big) | 小粒度子块（200~400 token）做向量检索，命中后返回大粒度父块（1000~2000 token）作生成上下文 | 答案散落、需要上下文才能理解的问题 | 检索精度与上下文完整性兼得 | 索引结构复杂，存储翻倍 |
| 8 | **Agentic/LLM 分块** (Agentic Chunking) | LLM 理解内容后按语义单元切 | 高价值、结构混乱的文档 | 切分最符合人类理解 | 成本最高、最慢，不适合大规模 |

**块大小权衡**：太小→语义不完整、召回碎片化；太大→噪声多、token 贵、检索精度降（embedding 被多主题稀释）。经验 200~500 token 起步，按评估集 Recall@K 调优。

项目支持 `CHUNK_BACKEND=langchain + semantic`（语义切分），并保留 `page_number`/`start_offset` 元数据供溯源。

## Q5 表格/图片等复杂内容怎么办

表格转 markdown 保留结构再切块；扫描件走 OCR；图片可走多模态模型生成描述文本再索引。项目按 `file_ext` 区分类型，分块时保留 `page_number`/`start_offset` 便于溯源定位。

## Q6 embedding 模型怎么选

看语言匹配（中文选 `bge-small-zh-v1.5` 这类中文模型）、维度（小维度省存储、速度快）、效果榜单（C-MTEB）、部署方式（本地 sentence-transformers vs API）。项目支持 `EMBEDDING_PROVIDER=local/openai` 可切换。

# 三、检索

## Q7 向量检索的原理和短板

query 和 chunk 各自编码成向量，算余弦相似度取 top-K（ANN, Approximate Nearest Neighbor 近似最近邻）。短板：**语义相近但字面不同的能召回，但专有名词、编号、型号类查询反而弱**——embedding 会把"HR-2024-003 号文件"和普通文本的距离抹平。

## Q8 为什么还要 BM25，两者怎么互补

BM25 (Best Matching 25) 基于词频/逆文档频率 (TF-IDF) 的稀疏检索 (Sparse Retrieval)，对精确关键词（人名、编号、术语）强；向量检索 (Dense Retrieval) 对同义改写、语义泛化强。混合双路召回互补覆盖，项目里每个分片标 `retrieval` 字段（`vector`/`bm25`/`both`），`both` 的相关性通常最高。

## Q9 多路召回为什么用 RRF 不用分数加权

向量分（0~1）和 BM25 分（无上界）量纲不同，加权需归一化且对参数敏感；RRF (Reciprocal Rank Fusion 倒数排名融合) 只用排名倒数 1/(k+rank) 融合，无量纲问题、无需调参、鲁棒。

## Q10 查询改写 (Query Rewriting) / Multi-Query 是干嘛的

用 LLM 把用户 query 生成多个语义相同但措辞不同的变体，每个变体分别召回再融合，扩大召回面，缓解"用户表述和文档用词不一致"导致的漏召回。项目里 `QUERY_REWRITE_ENABLED` 控制，LLM 失败自动退化为原 query，行为与关闭时完全一致（降级设计）。

## Q11 Rerank 精排原理，和向量检索的区别

向量检索是 **bi-encoder**（query 和 doc 各自独立编码，可离线建库、快但粗）；Rerank 是 **cross-encoder**（query+doc 拼接后交互编码，精度高但每对都要过一次模型，慢），所以只能对少量候选（几十条）精排。项目先 RRF 融合出候选池，`RERANK_CANDIDATE_K` 保证池子够大（否则只是 TOP_N 内部重排，收益有限），再精排取 `RERANK_TOP_N`。

## Q12 相似度阈值的作用

`SIMILARITY_THRESHOLD` 过滤低分结果，是 Precision/Recall 权衡——调高滤掉噪声但可能漏掉相关内容。兜底策略：过滤后为空就拒答"未找到相关内容"，不带噪声上下文进 LLM。

# 四、生成

## Q13 生成阶段 prompt 怎么设计

System Prompt 约束"只根据参考资料回答，没有就如实说明"；资料带 `[来源N]` 编号拼入上下文；要求答案引用处标注来源，方便用户核查，也为程序化评估 Faithfulness 打基础。

## Q14 幻觉怎么缓解

检索侧——召回质量高、空结果拒答不进 LLM；生成侧——prompt 强约束、温度调低、强制引用标注；评估侧——无引用句子占比作幻觉代理指标，RAGAS Faithfulness 监控。

## Q15 上下文太长/超窗口怎么办

上下文压缩 (Context Compression：LLM 对每个 chunk 先提取要点再拼接)、排序截断（lost in the middle 问题：重要内容放头尾）、map-reduce 分段生成再汇总。项目用 top-N 截断控制上下文规模。

## Q16 多轮对话怎么处理检索

后续问题常有指代（"那它的第二点呢"），需先结合历史做 query 改写/指代消解，得到独立完整的检索 query 再走召回，否则拿代词去检索必然失败。

# 五、评估

> 评估 4 层次：**L1 检索质量**（能不能找到）、**L2 生成质量**（答案准不准）、L3 端到端（用户满意吗）、L4 业务价值（值不值得做）。面试重点 L1 + L2。

## Q17 检索侧核心指标

Recall@K（相关内容有没有找到）、Precision@K（找到的多少相关）、MRR (Mean Reciprocal Rank，第一个正确结果排第几)、NDCG (Normalized Discounted Cumulative Gain，位置+相关度的综合排序质量)。

|                    | 实际相关 | 实际不相关 |
|--------------------|:---:|:---:|
| **被检索到** (TP+FP) | TP | FP |
| **未被检索到** (FN+TN) | FN | TN |

> **Recall = TP/(TP+FN)**，**Precision = TP/(TP+FP)**——Recall 分母看第一列，Precision 分母看第一行。

- **数值例（同一套数据）**：库里共 10 个相关文档，top-K 返回 20 条，其中 6 条相关 →
  **Recall = 6/10 = 0.6**（该找到的找到了 60%，4 个漏检 FN）；
  **Precision = 6/20 = 0.3**（找出来的只有 30% 相关，14 条噪声 FP）。
  同一次检索，两个指标回答两个不同问题：Recall 分母是**全部相关文档**，Precision 分母是**实际返回结果**。
- **@K 含义**：只看排序后前 K 条来算。RAG 里 K 通常取喂给 LLM 的 top-N（比如 5）——排后面的 chunk 进不了上下文，找到了也白找。
- **跷跷板关系**：K 调大→Recall↑ Precision↓；相似度阈值调高→Precision↑ Recall↓（对应项目里 `SIMILARITY_THRESHOLD`/`HYBRID_TOP_N` 的调参权衡）。
- **跷跷板原理**：top-K 是集合包含关系，K 扩大时命中相关数**只增不减**→Recall 单调涨；但排序把最相关的放前面，尾部新增条目相关密度越来越低，分母 K 线性涨而分子跟不上→Precision 被噪声稀释。
  数值例（库里 10 个相关）：

  | K | 前 K 条中相关数 | Recall@K（命中/库内相关总数） | Precision@K（命中/K） |
  |---|---|---|---|
  | 5 | 4 | 4/10 = 0.4 | 4/5 = **0.8** |
  | 10 | 6 | 6/10 = 0.6 | 6/10 = 0.6 |
  | 20 | 6（第 7~20 名全是噪声） | 6/10 = 0.6（卡住不再涨） | 6/20 = **0.3** |

  K=10→20 这段：尾部一条相关的都没有，Recall 纹丝不动，Precision 却砍半——"为了召回硬凑数量"的代价。

##  生成侧 RAGAS 四件套

生成侧评估：衡量最终生成的答案质量。

- **忠实度 (Faithfulness)**：答案是否忠实于检索到的上下文，有没有幻觉
  - 计算方法：LLM 先把答案**拆成一条条原子声明 (claims)**，逐条问"这条能否从检索上下文推断出来"，得分 = 能被支持的声明数 / 总声明数
  - 得分低 → 答案里夹带了上下文中没有的信息，即幻觉
- **答案相关性 (Answer Relevancy)**：答案是否真正回答了用户的问题
  - 计算方法：LLM **从答案反向生成 N 个它可能回答的问题**，算这些问题与原 query 的**余弦相似度均值**
  - 得分低 → 答案跑题、含糊其辞、答非所问（"我不知道"式回避会拉低分数）
- **上下文相关性 (Context Relevancy / Context Precision)**：检索到的上下文对回答问题是否有用
  - 计算方法：LLM 判定每个检索 chunk 是否与问题相关，**相关 chunk 是否排在前面**（排序加权的相关占比）
  - 得分低 → 召回混入噪声，或相关内容被排在候选末尾（Rerank 失效信号）
- **上下文召回 (Context Recall)**：生成答案所需的关键信息是否都在检索到的上下文中
  - 计算方法：拿**标准答案 (ground truth)** 的每条关键声明，检查是否都能归因到检索上下文，得分 = 能归因的声明数 / 标准答案声明总数
  - 得分低 → 检索漏了关键内容，答案必然不完整——这是**检索侧 L1 问题**，改生成没意义

**记忆点**：前两个评**答案**，后两个评**上下文**；全程 LLM-as-a-Judge 自动打分。
**数据要求**：前三个只需 `question + context + answer`，**Context Recall 需要标注的标准答案**——没有标注时用 LLM 基于 chunk 反向生成 QA 对作 golden set（呼应 Q21）。
**诊断映射**：Faithfulness 低→改 prompt 约束/降温/换模型（生成问题）；Context Recall 低→回头改检索（分块/embedding/召回）；Context Precision 低→排序问题（上 Rerank）；Answer Relevancy 低→prompt 或上下文噪声。

## Q18 项目检索链路怎么评估

先建「问题+标准chunk」评估集；分层测向量单路/BM25 单路/混合的 Recall@50 定位瓶颈（用 `retrieval` 字段统计各通道贡献，`both` 质量最高）；对比 RRF 序 vs Rerank 序的 MRR/NDCG 验证精排收益；`SIMILARITY_THRESHOLD` 是 Precision/Recall 权衡。


## Q20 项目生成质量怎么保障

System Prompt 强制"没资料就如实说"；答案强制标 `[来源N]` 引用——可程序化校验 Faithfulness（无引用句子占比=幻觉代理指标）；空检索直接拒答不进 LLM；RAGAS 评估时 Faithfulness 低→改 prompt/模型，Context Recall 低→回头改检索（L1 问题）。

## Q21 没有标注数据怎么评

LLM 基于 chunk 反向生成 QA 对构建 golden set（自带标准答案+来源）；LLM-as-a-Judge 按维度打分；用户点赞点踩/追问率作 L3 信号。

# 六、优化与进阶

## Q22 检索指标差怎么归因

Recall@K 低→召回问题（embedding 模型、分块、查询改写扩召回面）；Recall 高但 MRR/NDCG 低→排序问题（上 Rerank、调 RRF k）；关键词类 query 差→向量短板，补 BM25（专有名词/编号 BM25 更强）。

## Q23 什么是 GraphRAG，和普通 RAG 的区别

普通 RAG 只检索文本 chunk，回答不了跨文档、多跳、全局总结类问题（"A 和 C 通过谁关联"）。GraphRAG 额外抽取实体/关系构建知识图谱，检索时可沿图扩展多跳邻居、聚合社区摘要。项目基于本体抽取实体关系存 Neo4j（`graph_extraction_service` + `graph_store`），文本检索和图谱检索可互补。

## Q24 工程化上做了哪些可靠性设计

降级链路——BM25 关闭/依赖缺失/语料拉取失败自动回退纯向量；查询改写失败退化为原 query；Rerank 不可用保留 RRF 序；LLM 未配置明确报错不崩。检索走 `asyncio.to_thread` 防阻塞事件循环，SSE 流式输出先发 chunks 再逐 token 出答案，首 token 延迟低。
