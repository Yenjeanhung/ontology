from pydantic_settings import BaseSettings
from typing import Literal


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

    # 图存储
    GRAPH_STORE_PROVIDER: Literal["kuzu", "neo4j"] = "kuzu"
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

    # LLM
    LLM_PROVIDER: Literal["openai"]
    OPENAI_API_KEY: str
    OPENAI_BASE_URL: str
    LLM_MODEL: str
    LLM_MAX_TOKENS: int
    LLM_TEMPERATURE: float

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
    # 搜索引擎（tavily / bing / duckduckgo）
    SEARCH_PROVIDER: str = "tavily"
    TAVILY_API_KEY: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
