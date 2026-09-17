# ══════════════════════════════════════════════════════════════
# KnowSource 一体化镜像（多阶段构建：前端 dist + FastAPI 后端）
# 构建（在仓库根目录执行）：
#   docker build -t knowsource:latest .
# 运行（需先启动中间件，见 docker-compose.yml）：
#   docker run -p 8000:8000 --env-file backend/.env knowsource:latest
# K8s 部署清单见 deploy/k8s/
# ══════════════════════════════════════════════════════════════

# ── Stage 1：前端构建（Vite） ──────────────────────────────────
FROM node:20-alpine AS front-build
WORKDIR /build
COPY front/package.json front/package-lock.json ./
# 锁定依赖树，保证可复现构建
RUN npm ci
COPY front/ ./
RUN npm run build          # 产物：/build/dist


# ── Stage 2：后端运行时 ────────────────────────────────────────
FROM python:3.11-slim

# tika 文档解析兜底需要 JRE（未安装时 tika 自动降级，其它解析器继续可用）
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/srv/backend/data/hf   # 嵌入模型缓存：部署时挂载持久卷

# 先装依赖（利用层缓存：代码变更不触发重装）
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# 后端代码 + 前端构建产物（server.py 按 ../front/dist 路径托管静态资源）
COPY backend/ ./backend/
COPY --from=front-build /build/dist ./front/dist

# 非 root 运行；uploads/data 为运行时可写目录（K8s 中挂 PVC）
RUN useradd -m -u 10001 appuser \
    && mkdir -p /srv/backend/uploads /srv/backend/data \
    && chown -R appuser:appuser /srv/backend
USER appuser

WORKDIR /srv/backend
EXPOSE 8000

# 进程存活探针（server.py /api/healthz，只探测进程不探测外部组件）
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/healthz', timeout=3).status==200 else 1)" || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
