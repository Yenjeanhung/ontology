"""
KnowSource backend entrypoint.
"""

import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from database import init_db

# 日志目录配置
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def setup_logging():
    """配置日志系统，按级别输出到不同文件"""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 移除默认处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 格式化器
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 控制台处理器 (INFO级别及以上)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # DEBUG级别日志文件 (轮转，保留5个文件，每个最大50MB)
    debug_handler = RotatingFileHandler(
        LOG_DIR / "debug.log",
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(formatter)
    root_logger.addHandler(debug_handler)

    # INFO级别日志文件
    info_handler = RotatingFileHandler(
        LOG_DIR / "info.log",
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(formatter)
    root_logger.addHandler(info_handler)

    # ERROR级别日志文件
    error_handler = RotatingFileHandler(
        LOG_DIR / "error.log",
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)

    # 设置httpx日志级别为ERROR，避免INFO级别的HTTP请求日志刷屏
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.ERROR)

# 初始化日志配置
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage and provider singletons at startup."""
    logger.info("Initializing database...")
    await init_db()

    logger.info("Loading embedding provider...")
    from providers.embedding import create_embeddings

    create_embeddings()

    logger.info("Loading LLM provider...")
    from providers.llm import create_llm

    create_llm()

    logger.info("Ensuring graph store schema...")
    from providers.graph_store import ensure_graph_schema

    ensure_graph_schema()

    logger.info("Cleaning up zombie processing tasks...")
    from services.file_service import FileService
    from database import get_db

    async for db in get_db():
        await FileService.cleanup_zombie_tasks(db)

    logger.info("KnowSource started.")
    yield
    logger.info("KnowSource stopped.")


app = FastAPI(title="KnowSource", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from routers import agent, entity, files, graph, kb, library, ontology, query, vector_data

app.include_router(kb.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(library.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(vector_data.router, prefix="/api")
app.include_router(ontology.router, prefix="/api")
app.include_router(entity.router, prefix="/api")

front_dist = Path(__file__).parent.parent / "front" / "dist"
if front_dist.exists():
    app.mount("/", StaticFiles(directory=str(front_dist), html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(
        app, 
        host=settings.HOST, 
        port=settings.PORT,
        access_log=False  # 关闭访问日志，避免状态轮询日志刷屏
    )
