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
    GRAPH_MAX_ENTITIES_PER_CHUNK: int = 12
    GRAPH_MAX_RELATIONS_PER_CHUNK: int = 12
    # 抽取质量治理：过滤低价值实体名（日期/纯数值/URL/版本号/整句等）
    GRAPH_FILTER_LOW_VALUE_ENTITIES: bool = True
    # 抽取后丢弃的无语义通用关系类型（逗号分隔；置空则不过滤）
    GRAPH_GENERIC_RELATION_BLOCKLIST: str = "涉及,提到,关联,有关,相关"
    # 图谱清洗安全护栏：单次 apply 删除实体/关系占比超过此值则中止（防止误操作清空整个图谱）。
    # 取 0.8：允许对"噪声为主"的脏图一次清掉大多数噪声，同时拦截接近清空的误操作。
    GRAPH_CLEANUP_MAX_DELETE_RATIO: float = 0.8

    # LLM
    # openai = OpenAI 兼容（含 DeepSeek / Qwen / 智谱 / 自定义 OpenAI 格式）；anthropic = Anthropic 格式
    # 以下 LLM 配置可通过页面配置管理（/config/llm），.env 中不设置时使用默认值
    LLM_PROVIDER: Literal["openai", "anthropic"] = "openai"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    LLM_MODEL: str = ""
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.7

    # 分块
    CHUNK_STRATEGY: Literal["fixed", "semantic", "sentence"] = "fixed"
    CHUNK_SIZE: int
    CHUNK_OVERLAP: int

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

    # 知识问答混合检索（BM25 + 向量）
    BM25_ENABLED: bool = True        # 知识问答是否启用 BM25 关键词召回
    BM25_RECALL_K: int = 50          # BM25 召回候选数（参与 RRF 融合）
    HYBRID_TOP_N: int = 12           # 融合后最终来源分片数
    HYBRID_RRF_K: int = 60           # RRF 融合常数

    # 技能指令
    AGENT_SKILL_CHAR_BUDGET: int = 24000   # 技能指令总字符软上限（市场技能包 SKILL.md 常见 8-15K）

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

    # ───────────────────────── HTTP 访问日志 ──────────────────────────
    # 由中间件统一记录：IP 方法 路径 -> 状态码 耗时（等价于 AOP 的请求切面）
    ACCESS_LOG_ENABLED: bool = True
    # 耗时超过该值（毫秒）按 WARNING 输出，便于发现慢接口
    ACCESS_LOG_SLOW_MS: int = 3000
    # 不记录日志的路径（逗号分隔）：前端高频轮询等，避免刷屏
    ACCESS_LOG_SKIP_PATHS: str = "/api/notifications/summary"


settings = Settings()
