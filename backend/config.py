import os

from pydantic_settings import BaseSettings
from typing import Literal

from dotenv import load_dotenv

# 把 .env 中的变量（含 HTTP_PROXY/HTTPS_PROXY 等）注入进程环境变量，
# 这样 HuggingFace 下载、requests 等外网请求才能真正走代理；已存在的环境变量优先。
load_dotenv(override=False)


class Settings(BaseSettings):
    # 服务
    HOST: str
    PORT: int

    # 数据库
    DATABASE_URL: str

    # 嵌入模型
    EMBEDDING_PROVIDER: Literal["local", "openai"]
    EMBEDDING_MODEL: str
    EMBEDDING_DIMENSION: int
    HF_CACHE_DIR: str = ""  # HuggingFace 模型本地缓存目录，为空时走默认下载
    # OpenAI 嵌入（EMBEDDING_PROVIDER=openai 时使用）
    OPENAI_EMBEDDING_MODEL: str
    OPENAI_EMBEDDING_DIMENSION: int

    # 向量存储
    VECTOR_STORE_PROVIDER: Literal["chroma", "milvus"]
    CHROMA_PERSIST_DIR: str
    VECTOR_WRITE_BATCH_SIZE: int = 1
    # Milvus（VECTOR_STORE_PROVIDER=milvus 时使用）
    MILVUS_HOST: str
    MILVUS_PORT: int

    # 工作流
    WORKFLOW_KEEP_RUNS: int = 10  # 每个工作流保留的最近运行记录数（超出自动裁剪）
    # 工作流人工节点
    WORKFLOW_HUMAN_BATCH_LIMIT: int = 100  # 单次批量处理人工任务条数上限
    # 外发通知渠道（逗号分隔，如 webhook / email / wecom / dingtalk）；留空 = 仅站内待办
    NOTIFY_CHANNELS: str = ""

    # 图存储（默认 Neo4j；kuzu 仅作为嵌入式备用后端保留）
    GRAPH_STORE_PROVIDER: Literal["kuzu", "neo4j"] = "neo4j"
    KUZU_DB_PATH: str = "./data/graph/graph.kuzu"
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4j"
    NEO4J_DATABASE: str = "neo4j"
    GRAPH_ENTITY_EXTRACTION_ENABLED: bool = True
    GRAPH_EXTRACTION_BATCH_SIZE: int = 6
    GRAPH_EXTRACTION_CONCURRENCY: int = 3
    GRAPH_MIN_CHARS_FOR_EXTRACTION: int = 80
    # 图推理（P2）：置信度闸门 + 单规则建议上限（防建议洪水）
    INFERENCE_MIN_CONFIDENCE: float = 0.7
    INFERENCE_RULE_LIMIT: int = 200
    GRAPH_MAX_ENTITIES_PER_CHUNK: int = 12
    GRAPH_MAX_RELATIONS_PER_CHUNK: int = 12
    # 抽取质量治理：过滤低价值实体名（日期/纯数值/URL/版本号/整句等）
    GRAPH_FILTER_LOW_VALUE_ENTITIES: bool = True
    # 抽取后丢弃的无语义通用关系类型（逗号分隔；置空则不过滤）
    GRAPH_GENERIC_RELATION_BLOCKLIST: str = "涉及,提到,关联,有关,相关"
    # 图谱清洗安全护栏：单次 apply 删除实体/关系占比超过此值则中止（防止误操作清空整个图谱）。
    # 取 0.8：允许对"噪声为主"的脏图一次清掉大多数噪声，同时拦截接近清空的误操作。
    GRAPH_CLEANUP_MAX_DELETE_RATIO: float = 0.8
    # ── 语义实体对齐（清洗建议的语义通道）──
    # 对「实体名+描述」embedding 后做近邻比对（blocking）+ 阈值精判（verification），
    # 补足字面相似度聚簇抓不到的「语义同、字面远」重复（简称/全称/别名）。
    # 向量缓存在 entity_vectors 表（派生数据，可整表重建），仅对无有效缓存的实体增量编码。
    GRAPH_CLEANUP_SEMANTIC_ENABLED: bool = True
    GRAPH_CLEANUP_SEMANTIC_THRESHOLD: float = 0.90    # 余弦相似度阈值（语义通道从紧，控制误合率）
    GRAPH_CLEANUP_SEMANTIC_TOPK: int = 5              # 每个待判实体保留的近邻候选数
    GRAPH_CLEANUP_SEMANTIC_MAX_ENTITIES: int = 20000  # 参与语义通道的实体数上限（超出跳过，防首跑过重）
    GRAPH_CLEANUP_SEMANTIC_TEXT_MAXLEN: int = 300     # 参与编码的「名称+描述」文本截断长度
    # 单类型全量比对上限：向量有缓存后比对只是矩阵乘，常规类型每次全量比（建议可重复出现，
    # 与字面通道行为一致）；超过此值的超大类型退化为只比增量侧，存量随增量逐轮收敛。
    GRAPH_CLEANUP_SEMANTIC_TYPE_MATRIX_LIMIT: int = 5000
    # ── 实体抽取规则（doc/知识库/实体抽取属性级规则与人工复核设计.md）──
    # 全局兜底实体置信度门槛，0 = 关闭；对象类型 min_confidence > 属性 confidence_threshold 优先
    GRAPH_MIN_ENTITY_CONFIDENCE: float = 0.0
    # 复核队列总开关：关闭时未通过规则的实体照常入库（仅计入抽取报告），
    # 保证新规则上线初期不会丢数据；队列功能就绪后置 true
    GRAPH_EXTRACTION_REVIEW_ENABLED: bool = False
    # 可选补充：采用模型自报置信度（默认关闭，用证据计算，见设计文档 §4.5.5）
    GRAPH_USE_MODEL_CONFIDENCE: bool = False

    # LLM
    # openai = OpenAI 兼容（含 DeepSeek / Qwen / 智谱 / 自定义 OpenAI 格式）；anthropic = Anthropic 格式
    # 以下 LLM 配置可通过页面配置管理（/config/llm），.env 中不设置时使用默认值
    LLM_PROVIDER: Literal["openai", "anthropic"] = "openai"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    LLM_MODEL: str = ""
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.7

    # ───────────────────────── LangSmith 可观测与评估 ─────────────────────────
    # 总开关：true 且配置 API Key 时，LangChain / LangGraph 的 LLM 调用与多智能体图执行
    # 自动上报 LangSmith 云端 trace（免费 Developer 计划：5k traces/月、14 天保留）；
    # false 或无 Key 时静默跳过，不影响本地运行。框架原生回调，业务代码零侵入。
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "ontology-multi-agent"

    # ───────────────── Function Calling 工具链 / MCP ─────────────────
    # ToolAgent 的工具调用循环与 MCP 外部工具接入（services/agent_loop.py、
    # services/tool_registry.py）；工具清单见 GET /api/agent/multi/tools。
    TOOL_LOOP_MAX_ITERATIONS: int = 4     # 单节点工具循环 LLM 轮数上限
    TOOL_TIMEOUT: float = 25.0            # 单次工具执行超时（秒）
    MCP_TOOL_TIMEOUT: float = 30.0        # MCP 远程工具单次调用超时（秒）
    # MCP 服务器清单（JSON 数组文本，空=不接入）。示例：
    # MCP_SERVERS=[{"name":"fs","transport":"stdio","command":"npx",
    #               "args":["-y","@modelcontextprotocol/server-filesystem","D:/data"]},
    #              {"name":"corp","transport":"streamable_http","url":"http://10.0.0.8:9001/mcp"}]
    # 平台自身也可作为 MCP 服务器被外部 Agent 消费：python scripts/mcp_server.py
    MCP_SERVERS: str = ""

    # ───────────────── DeepAgents 深度模式（第二执行路径） ─────────────────
    # services/multi_agent/deep_agent.py：LangChain 官方 agent harness（deepagents>=0.7，
    # LangGraph 1.x 运行时同源）作为复杂任务深度模式，与 StateGraph 团队并存、零替换。
    # 前端「深度模式」勾选（请求体 deep=true）+ 总闸双确认；关闭或未安装时回落普通团队。
    DEEP_AGENT_ENABLED: bool = False      # 总闸：false 时 deep=true 一律回落普通团队
    DEEP_AGENT_TIMEOUT: float = 300.0     # 整轮超时（秒）：多轮工具循环比常规节点慢
    DEEP_AGENT_SUBAGENTS: bool = False    # True=子智能体模式（主代理经 task 派发，
                                          #   子研究员上下文隔离；默认主代理直带工具）

    # ───────────────── LangGraph Checkpointer 断点恢复（P0） ─────────────────
    # services/multi_agent/engine.py：协作团队 StateGraph 每步落 checkpoint，
    # 服务崩溃/异常后按 thread_id 断点续跑（已完成节点不重复执行），并可回放
    # 状态历史。thread_id = 会话ID::随机token（路由层生成，一轮运行一条）。
    MULTI_AGENT_CHECKPOINTER: bool = True     # 总闸：false 时行为同无 checkpointer 旧版
    MULTI_AGENT_CHECKPOINT_DB: str = ""       # checkpoint 库路径（空 = data/multi_agent_checkpoints.db）

    # ───────────────── 单智能体问答工具循环（L2：agent loop + tools） ─────────────────
    # 问答页 /agent/query 的生成阶段允许 LLM 自主调用平台工具（kb_search/graph_search/
    # data_query + MCP）补充检索：检索管线照跑（引用体系不变），工具作为口径补充与
    # KB 未命中兜底。默认关闭（避免所有问答额外延迟），请求体 use_tools 可按次开启。
    AGENT_TOOL_LOOP_ENABLED: bool = False

    # 分块
    CHUNK_STRATEGY: Literal["fixed", "semantic", "sentence", "recursive", "heading"] = "fixed"
    # 以下为全局兜底默认值：仅在文件未分析 / 批量处理未确认时使用；
    # 正常流程的分片策略与参数由「上传后自动分析 + 处理确认弹窗」按文件决定并持久化
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    # 上传/挂载后自动分析分片策略的兜底默认值；页面「自动分析」开关（app_settings）优先。
    # 默认关闭：分析需解析全文并统计特征，大文档耗时明显，需要时可在处理确认弹窗手动触发。
    CHUNK_AUTO_ANALYZE: bool = False
    # 分析时是否启用 embedding 抽样计算语义突变密度（成本较高，默认关闭）
    CHUNK_ANALYZE_SEMANTIC: bool = False
    # 分片预览默认返回块数上限
    CHUNK_PREVIEW_LIMIT: int = 20
    # 分片后端：self = 自研实现（默认）；langchain = 优先使用 langchain_text_splitters，
    # 依赖缺失或运行异常时自动回落 self，行为不中断。
    CHUNK_BACKEND: Literal["self", "langchain"] = "self"

    # 召回
    SIMILARITY_THRESHOLD: float = 0.3

    # OAG 智能体（本体增强生成）
    OAG_ENABLED: bool = True              # 总开关
    OAG_VEC_K: int = 50                   # 向量召回数
    OAG_TOP_N: int = 12                   # 融合后最终来源分片数
    OAG_SEED_ENTITY_LIMIT: int = 8        # 种子实体上限
    OAG_GRAPH_CHUNK_LIMIT: int = 12       # 图谱召回分片上限
    OAG_RRF_K: int = 60                   # RRF 融合常数
    OAG_NEIGHBOR_HOPS: int = 1            # 子图跳数（v1 固定 1 跳）
    OAG_NEIGHBOR_LIMIT: int = 40          # 子图关系条数上限
    OAG_ENTITY_LIST_LIMIT: int = 5000     # 实体链接词面匹配时加载的实体数上限
    OAG_BM25_ENABLED: bool = True         # OAG 是否启用 BM25 关键词召回（与向量/图谱三路 RRF）
    OAG_BM25_RECALL_K: int = 50           # OAG BM25 召回候选数（参与 RRF 融合）
    OAG_EMPTY_RECALL_THRESHOLD: float = 0.1  # 向量路为空时兜底重试的相似度阈值（<=0 关闭兜底）

    # 知识问答混合检索（BM25 + 向量）
    BM25_ENABLED: bool = True        # 知识问答是否启用 BM25 关键词召回
    BM25_RECALL_K: int = 50          # BM25 召回候选数（参与 RRF 融合）
    HYBRID_TOP_N: int = 12           # 融合后最终来源分片数
    HYBRID_RRF_K: int = 60           # RRF 融合常数

    # 查询改写（Multi-Query）：用 LLM 生成同义查询变体，多路召回后统一 RRF 融合。
    # 关闭时行为与旧版完全一致（仅原始 query 单路召回）。
    QUERY_REWRITE_ENABLED: bool = False
    QUERY_REWRITE_COUNT: int = 3     # 生成变体数（不含原始 query）

    # ── 两级意图路由（0.6B 路由服务，v6 LoRA；见 doc/模型微调/训练与部署手册.md）──
    # 第一级：chat→大模型直答 / data·graph·kb→精简组合 / 低置信→全组合老规则兜底。
    # 服务不可达或超时一律自动回落，行为与关闭路由时完全一致。
    INTENT_ROUTING_ENABLED: bool = True
    INTENT_ROUTER_URL: str = "http://127.0.0.1:8001/v1/chat/completions"
    INTENT_ROUTER_MODEL: str = "qwen3-0.6b-router"
    INTENT_ROUTER_TIMEOUT: float = 3.0   # 路由调用超时（秒）
    INTENT_CONF_MIN: float = 0.6         # 置信低于该值 → 视为低置信，全组合兜底
    # 第二级：NL2Filter 结构化抽取（仅 data 类触发；抽不出 → DataAgent 词频老路兜底）
    NL2FILTER_ENABLED: bool = True
    NL2FILTER_URL: str = ""              # 留空复用 INTENT_ROUTER_URL（同服务双模式）
    NL2FILTER_MODEL: str = "qwen3-0.6b-router"      # 先与第一级同一个 0.6B；专属 LoRA 部署后在 .env 改名即切换
    NL2FILTER_TIMEOUT: float = 3.0
    KB_REWRITE_GATE: bool = True         # kb 模式下 Retriever 检索前轻量改写（门控）
    # 任务澄清判定：开跑前 0.6B 判断任务是否缺关键信息，缺则先发澄清选项（用户补充后跳过，防循环）
    CLARIFY_ENABLED: bool = True
    CLARIFY_URL: str = ""                # 留空复用 NL2FILTER_URL → INTENT_ROUTER_URL（同服务多模式）
    CLARIFY_MODEL: str = ""              # 留空回退 NL2FILTER_MODEL → INTENT_ROUTER_MODEL（.env 未配时默认值曾指向不存在的模型 → 404 静默失效）
    CLARIFY_TIMEOUT: float = 5.0
    # DataAgent 本体驱动 NL2SQL（三级链第一级；失败回落 NL2Filter → 词频老路）
    NL2SQL_ENABLED: bool = True
    NL2SQL_MAX_ROWS: int = 500           # 行数上限（校验器自动纠偏 LIMIT）
    NL2SQL_TIMEOUT_S: float = 8.0        # 只读执行超时（秒）
    NL2SQL_MAX_RETRIES: int = 2          # 执行/校验失败回灌重试轮数上限

    # Rerank 精排：RRF 融合后用 cross-encoder / LLM 二次打分再截断。
    # 默认关闭：cross-encoder 需首次下载模型，开启前请确认 RERANK_MODEL 可访问。
    # 模型来源与嵌入模型共用 HF_CACHE_DIR：配置后优先本地缓存离线加载，未命中自动在线下载。
    RERANK_ENABLED: bool = False
    RERANK_PROVIDER: Literal["cross-encoder", "llm"] = "cross-encoder"
    RERANK_MODEL: str = "BAAI/bge-reranker-base"
    RERANK_CANDIDATE_K: int = 30     # 送入精排的候选数（从 RRF 结果头部截取）
    RERANK_TOP_N: int = 12           # 精排后保留的分片数
    RERANK_MIN_SCORE: float = 0.0    # cross-encoder 分数下限（bge 输出可为负），低于此值丢弃
    RERANK_MAX_CHARS: int = 1024     # 单个候选送入精排的最大字符数（超长截断）
    RERANK_LLM_BATCH: int = 10       # llm 精排时单批候选数（控制单次提示长度）

    # 技能指令
    AGENT_SKILL_CHAR_BUDGET: int = 24000   # 技能指令总字符软上限（市场技能包 SKILL.md 常见 8-15K）

    # ───────────────────────── 智能体会话（短期记忆，doc/智能体/智能体会话_功能设计.md）─────────
    CHAT_SESSION_WINDOW_TURNS: int = 6     # 注入 prompt 的最近轮数（一轮 = 用户一问 + 助手一答）
    CHAT_SESSION_CHAR_BUDGET: int = 6000   # 历史注入总字符预算（约 2k tokens），超预算从最旧开始丢弃
    CHAT_SUMMARY_ENABLED: bool = True      # 超长滚动摘要总开关
    CHAT_SUMMARY_TRIGGER_TURNS: int = 20   # 会话累计用户消息数超过该值时触发滚动摘要
    CHAT_SUMMARY_MAX_CHARS: int = 1000     # 滚动摘要字符上限
    CHAT_SUMMARY_MODEL: str = ""           # 摘要压缩专用模型；空 = 复用主对话模型（LLM_MODEL）

    # ───────────────────────── mem0 长期记忆（默认关闭；需 pip install mem0ai）─────────
    MEM0_ENABLED: bool = False             # 总开关：false 时不启用长期记忆（自动降级，不影响主链路）
    MEM0_COLLECTION: str = "agent_memories"  # Milvus 独立 collection（与知识库 collection 隔离）
    MEM0_SEARCH_LIMIT: int = 5             # 每次检索注入 prompt 的事实条数上限
    MEM0_HISTORY_DB_PATH: str = "./data/mem0_history.db"  # mem0 操作历史（SQLite，供回溯/调试）

    # 工作流
    WORKFLOW_MAX_NODES: int = 100          # 单工作流节点数上限
    WORKFLOW_MAX_STEPS: int = 200          # 单次运行最多执行节点数
    WORKFLOW_RUN_TIMEOUT_SECONDS: int = 300  # 单次运行总超时（秒）
    WORKFLOW_NODE_OUTPUT_LIMIT: int = 8192   # 节点输出 SSE 回传截断上限（字符）
    # 工作流 HTTP 节点
    WORKFLOW_HTTP_TIMEOUT_SECONDS: int = 30      # 节点未配置超时的兜底值（秒）
    WORKFLOW_HTTP_MAX_RESPONSE_MB: int = 10      # 响应体大小上限（MB），超限节点失败
    WORKFLOW_HTTP_ALLOW_PRIVATE_NET: bool = True # 是否允许调用内网/localhost（企业内部工具默认放行）

    # 技能 ZIP 包导入（安全上限，均可在 .env 覆盖）
    SKILL_ZIP_MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024        # 上传/下载 zip 原始体积上限
    SKILL_ZIP_MAX_TOTAL_UNCOMPRESSED: int = 100 * 1024 * 1024  # 解压总量上限（解压前按 ZipInfo 预检）
    SKILL_ZIP_MAX_ENTRIES: int = 1000                         # zip 条目数上限（真实多技能仓库可达数百条目）
    SKILL_ZIP_MAX_COMPRESSION_RATIO: int = 100                # 单文件压缩比上限（>1MB 且超比判 bomb）
    SKILL_FILE_MAX_CONTENT_BYTES: int = 64 * 1024             # 单文本文件内容导出回流上限，超出仅记清单
    SKILL_FILES_MAX_TOTAL_CONTENT_BYTES: int = 256 * 1024     # 单技能文件内容导出总上限
    SKILL_MANIFEST_MAX_LINES: int = 30                        # instructions 附带资源清单最多行数
    # 技能配套文件解压根目录（相对后端运行目录；文件落盘，数据库只存清单）
    SKILL_FILES_DIR: str = "./data/skills"

    # 文件上传
    UPLOAD_DIR: str
    CHUNK_DIR: str
    MAX_FILE_SIZE: int

    # 文档解析（Tika 兜底，可选）
    # Tika 作为未知格式（pptx/xlsx/html/eml 等）的兜底解析器；轻量格式 txt/md/pdf/docx 永远走专用库
    TIKA_FALLBACK_ENABLED: bool = True        # 兜底总开关；无 JRE 时自动降级，不影响应用启动
    TIKA_SERVER_ENDPOINT: str = ""            # 非空时走外部 Tika Server（如 http://tika:9998），跳过本地 JRE
    TIKA_JAVA_PATH: str = ""                  # 显式 java 路径，空则用 PATH 中的 java

    # 文件管理与联网采集
    DEFAULT_KB_UPLOAD_DIR: str = "知识库上传"
    CRAWL_ENABLED: bool = True
    CRAWL_MAX_PAGES: int = 5
    CRAWL_TIMEOUT_SECONDS: int = 15
    CRAWL_RATE_LIMIT_SECONDS: float = 1.0
    CRAWL_LLM_FILTER: bool = True
    CRAWL_SAVE_RAW_HTML: bool = False
    # 直连抓取失败 / 正文过短时，回退到 Jina Reader（r.jina.ai）渲染 JS 抓正文
    CRAWL_JINA_FALLBACK: bool = True
    # 可选：Jina API Token，配置后走更高额度；留空用免费匿名额度
    CRAWL_JINA_TOKEN: str = ""
    # 搜索引擎（tavily / bing / duckduckgo）
    SEARCH_PROVIDER: str = "tavily"
    TAVILY_API_KEY: str = ""

    class Config:
        env_file = ".env"
        # 允许 .env 中存在未在 Settings 中定义的键（如 HTTP_PROXY/HTTPS_PROXY/NO_PROXY），
        # 否则 pydantic-settings 默认 extra="forbid" 会在启动时直接报 ValidationError
        extra = "ignore"

    # ───────────────────────── 定时调度（Scheduler）─────────────────────────
    # 触发器计算时区（cron / interval / once 均按此时区）
    SCHEDULER_TIMEZONE: str = "Asia/Shanghai"
    # 调度引擎总开关：false 时不启动 APScheduler，仅保留计划 CRUD
    SCHEDULER_ENABLED: bool = True
    # 同时进行的调度触发执行上限（防止堆积）
    SCHEDULER_MAX_CONCURRENT_RUNS: int = 5
    # 服务重启/宕机后，错过触发时间在此窗口内仍补触发（秒）
    SCHEDULER_MISFIRE_GRACE_SECONDS: int = 300
    # 多个错过的触发合并为一次
    SCHEDULER_COALESCE: bool = True

    # ───────────────────────── 系统监控（Monitor）─────────────────────────
    # SSE 定时推送组件状态的间隔（秒）；打开监控页面时生效
    MONITOR_PUSH_INTERVAL_SECONDS: int = 30

    # ───────────────────────── OpenTelemetry 接口链路追踪 ─────────────────────────
    # doc/监控/OpenTelemetry/00-OTel接口链路追踪方案.md；span 存独立 SQLite
    # （与业务主库隔离），OTEL_ENABLED=false 或 SDK 未安装时全量降级 no-op。
    OTEL_ENABLED: bool = True
    OTEL_DB_PATH: str = "./data/otel_traces.db"   # trace 存储文件（WAL 模式）
    OTEL_RETENTION_DAYS: int = 7                  # span 保留天数（每日清理一次）
    OTEL_SLOW_MS: int = 3000                      # 慢调用阈值（前端标红 / slow_only 过滤）
    OTEL_EXPORT_INTERVAL_MS: int = 5000           # BatchSpanProcessor 批量落库间隔
    # FastAPIInstrumentor 排除路径（逗号分隔正则）：SSE 长连接与健康探针不入库
    OTEL_EXCLUDED_URLS: str = "/api/monitor/stream,/api/healthz,/api/notifications/summary"

    # ───────────────────────── 服务层方法日志（AOP 式自动织入）─────────────────────────
    # 启动时扫描 services 包，为类的公共方法统一织入「入参 + 返回值 + 耗时」日志
    SERVICE_TRACE_ENABLED: bool = True
    # 正常调用的日志级别（DEBUG=只进 debug.log，不干扰控制台；INFO=控制台可见）
    SERVICE_TRACE_LEVEL: str = "DEBUG"
    # 超过该耗时（毫秒）按 WARNING 输出，用于发现慢方法
    SERVICE_TRACE_SLOW_MS: int = 1000
    # 是否记录入参（自动脱敏 password/token/api_key 等，并按长度截断）
    SERVICE_TRACE_LOG_ARGS: bool = True
    SERVICE_TRACE_MAX_ARG_LEN: int = 300
    # 不织入的类名（逗号分隔，支持 fnmatch 通配），如 NotificationChannel
    SERVICE_TRACE_EXCLUDE: str = "NotificationChannel"

    # ───────────────────────── 用户与权限（Auth / RBAC）──────────────────────────
    # JWT 签名密钥：留空时首次启动自动生成并持久化到 data/.auth_secret_key，之后复用
    AUTH_SECRET_KEY: str = ""
    AUTH_ENABLED: bool = True              # 总开关：false 时全站免登录（本地调试用）
    # access/refresh 令牌有效期在「角色权限 → 安全策略」页面配置（security_settings 表）
    # 会话状态缓存 TTL（秒）：决定"踢人"在多副本间的生效延迟上限
    AUTH_CACHE_TTL_SECONDS: int = 10
    # 审计日志异步落库：批量大小与刷写间隔
    AUDIT_BATCH_SIZE: int = 100
    AUDIT_FLUSH_INTERVAL_SECONDS: float = 1.0
    AUDIT_QUEUE_MAXSIZE: int = 5000
    # CORS 白名单（逗号分隔，留空 = 沿用 *）；开启鉴权后建议显式配置
    CORS_ALLOW_ORIGINS: str = ""

    # ───────────────────────── HTTP 访问日志 ──────────────────────────
    # 由中间件统一记录：IP 方法 路径 -> 状态码 耗时（等价于 AOP 的请求切面）
    ACCESS_LOG_ENABLED: bool = True
    # 耗时超过该值（毫秒）按 WARNING 输出，便于发现慢接口
    ACCESS_LOG_SLOW_MS: int = 3000
    # 不记录日志的路径（逗号分隔）：前端高频轮询等，避免刷屏
    ACCESS_LOG_SKIP_PATHS: str = "/api/notifications/summary"


settings = Settings()

# LangSmith 开关同步到进程环境变量：langchain-core 运行时按 LANGSMITH_* 环境变量决定是否上报，
# pydantic Settings 只读取不写回，故在此统一注入，使 Settings 成为唯一事实源——
# .env 设 LANGSMITH_TRACING=true 即开启；关闭或无 Key 时 SDK 静默跳过，不影响本地运行。
os.environ["LANGSMITH_TRACING"] = "true" if settings.LANGSMITH_TRACING else "false"
os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
