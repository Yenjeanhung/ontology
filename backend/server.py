"""
KnowSource backend entrypoint.
"""

import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# pydantic-settings 只把 .env 读进 Settings 字段、不导出到进程环境，
# 而 huggingface_hub/requests 等库只认真实环境变量（如 HTTPS_PROXY、HF_ENDPOINT），
# 这里显式加载一次让 .env 中的环境变量生效。
load_dotenv(Path(__file__).parent / ".env")

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings
from core.otel import init_otel, shutdown_otel, start_cleanup_task
from core.preflight import failed_required, has_run, render_report, run_preflight
from database import DatabaseUnavailableError, get_db, init_db
from middleware.access_log import AccessLogMiddleware
from middleware.audit import AuditMiddleware
from middleware.auth import AuthMiddleware
from middleware.permission import PermissionMiddleware

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

    # neo4j 驱动把服务端通知（如 IndexOrConstraintAlreadyExists）以 INFO 打到
    # neo4j.notifications，建schema时每条约束都会刷一行，这里只保留 WARNING 以上
    logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)

# 初始化日志配置
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage and provider singletons at startup."""
    # 组件自检：`uvicorn server:app` 启动时不会走 __main__ 的预检，这里补一次
    logger.info("Checking components...")
    try:
        already_checked = has_run()  # __main__ 已跑过则复用结果，不重复打印
        report = await run_preflight(dispose_engine=False)
        if not already_checked:
            print(render_report(report))
        logger.info("Component preflight: %s",
                    ", ".join(f"{i.name}={i.state}" for i in report))
        if failed_required(report):
            logger.error("Startup aborted: %d required component(s) unavailable",
                         len(failed_required(report)))
            os._exit(1)
    except Exception:
        # 自检只是辅助手段，自身异常不应阻断启动（后续 init_db 仍会给出数据库提示）
        logger.exception("Component preflight failed")

    logger.info("Initializing database...")
    try:
        await init_db()
    except DatabaseUnavailableError as exc:
        # 数据库没启动：打印可操作的中文提示后直接退出，不刷原始堆栈。
        # 用 os._exit 而非 sys.exit，避免 starlette/uvicorn 把 SystemExit 再展开成堆栈。
        print(exc.friendly_message(), file=sys.stderr)
        logger.error("Startup aborted: %s", exc)
        os._exit(1)

    logger.info("Loading embedding provider...")
    from providers.embedding import create_embeddings

    create_embeddings()

    logger.info("Loading LLM provider...")
    from providers.llm import create_llm
    from database import get_db
    from services.config_service import load_active_into_settings

    # 从数据库载入生效的 LLM 配置到内存 settings（LLM 配置不再走 .env）
    async for db in get_db():
        await load_active_into_settings(db)
        break

    llm = create_llm()
    if llm is None:
        logger.warning("LLM 未配置，请到 /config/llm 页面配置后使用")

    logger.info("Ensuring graph store schema...")
    from providers.graph_store import ensure_graph_schema

    ensure_graph_schema()

    logger.info("Cleaning up zombie processing tasks...")
    from services.file_service import FileService
    from database import get_db

    async for db in get_db():
        await FileService.cleanup_zombie_tasks(db)

    logger.info("Seeding preset agent skills...")
    from services.skill_service import seed_presets
    async for db in get_db():
        try:
            count = await seed_presets(db)
            if count:
                logger.info("Seeded %d preset agent skills", count)
        except Exception:
            logger.exception("Failed to seed preset agent skills")

    # Seed 内置「系统默认」智能体（幂等；不可删除、可修改，行为=问答页页面选择的回退）
    from services.agent_service import ensure_default_agent
    async for db in get_db():
        try:
            if await ensure_default_agent(db):
                logger.info("Seeded built-in default agent")
        except Exception:
            logger.exception("Failed to seed default agent")

    # 存量迁移：旧版本创建的会话不记录 agent_id（空串），新版按智能体过滤后
    # 这些历史会话在任何智能体下都不可见 → 统一归入内置「系统默认」（幂等）
    from sqlalchemy import update
    from models import ChatSession
    from services.agent_service import DEFAULT_AGENT_ID
    async for db in get_db():
        try:
            result = await db.execute(
                update(ChatSession)
                .where(ChatSession.agent_id == "")
                .values(agent_id=DEFAULT_AGENT_ID)
            )
            if result.rowcount:
                await db.commit()
                logger.info("Migrated %d legacy chat sessions (agent_id='') to default agent",
                            result.rowcount)
        except Exception:
            logger.exception("Failed to migrate legacy chat sessions")

    # 存量迁移：旧版本把配套文件内容存在数据库里 → 迁到磁盘（幂等，失败不阻断启动）
    logger.info("Syncing skill files to disk...")
    from services.skill_import_service import sync_skill_files_to_disk
    async for db in get_db():
        try:
            await sync_skill_files_to_disk(db)
        except Exception:
            logger.exception("Failed to sync skill files to disk")

    # 用户与权限：建权限点/内置角色/初始管理员（幂等），并启动审计异步落库
    logger.info("Bootstrapping auth and permissions...")
    try:
        from services.auth_bootstrap import bootstrap
        from services.audit_service import AuditService

        async for db in get_db():
            await bootstrap(db)
            break
        AuditService.start()
    except Exception:
        logger.exception("Failed to bootstrap auth module")

    # 启动定时调度引擎（从历史计划恢复启用任务；失败不阻断主服务启动）
    logger.info("Starting scheduler engine...")
    try:
        from services.scheduler_engine import start as scheduler_start
        await scheduler_start()
    except Exception:
        logger.exception("Failed to start scheduler engine")

    # 启动 OpenTelemetry 每日清理任务。
    # 注意：init_otel(app) 必须在模块级（app 创建后、首个请求前）调用，
    # 放在 lifespan startup 里会因中间件栈已构建而静默失效（无 SERVER span）。
    try:
        start_cleanup_task()
    except Exception:
        logger.exception("Failed to start otel cleanup task")

    logger.info("KnowSource started.")
    yield
    logger.info("KnowSource stopped.")

    # 关闭链路追踪：强制刷新 BatchSpanProcessor 缓冲后释放 SQLite 连接
    try:
        shutdown_otel()
    except Exception:
        logger.exception("Failed to shutdown otel tracing")

    # 关闭审计异步落库（给在途日志一个收尾窗口）
    try:
        from services.audit_service import AuditService

        await AuditService.stop()
    except Exception:
        logger.exception("Failed to stop audit service")

    # 关闭调度引擎
    try:
        from services.scheduler_engine import shutdown as scheduler_shutdown
        await scheduler_shutdown()
    except Exception:
        logger.exception("Failed to shutdown scheduler engine")


app = FastAPI(title="KnowSource", lifespan=lifespan)

# OpenTelemetry 链路追踪：必须在 app 创建后、首个请求（含 uvicorn 的 lifespan
# 握手）之前执行。FastAPIInstrumentor 靠替换 build_middleware_stack 挂载 HTTP
# SERVER span 中间件，而 Starlette 首次 __call__ 即构建并缓存中间件栈；若放到
# lifespan startup 里，栈已构建、patch 永不生效，SERVER span 会静默丢失。
try:
    init_otel(app)
except Exception:
    logger.exception("Failed to init otel tracing")

# CORS：配置了白名单时收紧（并允许携带凭证）；留空则沿用 * 以兼容本地开发
_cors_origins = [o.strip() for o in (settings.CORS_ALLOW_ORIGINS or "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=bool(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

# 中间件栈：Starlette 的 add_middleware 会把后加的放在更外层，
# 因此下面的 add 顺序对应「外层 → 内层」= AccessLog → Auth → Audit → Permission → CORS。
# 顺序要点：
#   - Auth 必须在 Audit / Permission 之前，否则后者读不到 scope["auth_user"]；
#   - Audit 在 Permission 之前，这样被 403 拒绝的越权尝试同样会留下审计记录。
app.add_middleware(PermissionMiddleware)
app.add_middleware(AuditMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(AccessLogMiddleware)

from routers import app_settings, agent, audit, auth, chat, config, entity, files, graph, graph_analysis, graph_sync, kb, library, monitor, multi_agent, notifications, ontology, ontology_function, ontology_interface, ontology_service, ontology_version, ontology_view, query, role, scheduler, session, user, vector_data, workflow

app.include_router(kb.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(library.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(graph_sync.router, prefix="/api")
app.include_router(graph_analysis.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(multi_agent.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(vector_data.router, prefix="/api")
app.include_router(ontology.router, prefix="/api")
app.include_router(ontology_interface.router, prefix="/api")
app.include_router(ontology_function.router, prefix="/api")
app.include_router(ontology_service.router, prefix="/api")
app.include_router(ontology_view.router, prefix="/api")
app.include_router(ontology_version.router, prefix="/api")
app.include_router(entity.router, prefix="/api")
app.include_router(workflow.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(scheduler.router, prefix="/api")
app.include_router(monitor.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(user.router, prefix="/api")
app.include_router(role.router, prefix="/api")
app.include_router(session.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(app_settings.router, prefix="/api")

front_dist = Path(__file__).parent.parent / "front" / "dist"
if front_dist.exists():
    app.mount("/", StaticFiles(directory=str(front_dist), html=True), name="frontend")


@app.get("/api/healthz")
async def healthz():
    """进程存活探针（K8s liveness/readiness）：不探测外部组件，毫秒级返回。

    组件级健康（数据库/图库/向量库/LLM…）见 /api/monitor/*，用于诊断而非探针——
    探针若依赖外部组件，组件抖动会误杀 Pod。
    """
    return {"ok": True}


if __name__ == "__main__":
    # 启动前自检各外部组件（数据库 / 图库 / 向量库 / 嵌入 / Tika），
    # 未就绪时打印状态清单与排查建议后退出，避免刷原始堆栈。
    try:
        report = asyncio.run(run_preflight())
    except Exception as exc:  # 自检本身异常时兜底，仍走友好提示
        print(f"组件自检失败：{exc}", file=sys.stderr)
        sys.exit(1)
    print(render_report(report))
    if failed_required(report):
        sys.exit(1)

    uvicorn.run(
        app, 
        host=settings.HOST, 
        port=settings.PORT,
        access_log=False  # 关闭访问日志，避免状态轮询日志刷屏
    )
