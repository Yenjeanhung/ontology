# ══════════════════════════════════════════════════════════════
# KnowSource 纯后端镜像（API-only，不含前端托管）
#
# server.py 对 front/dist 的静态托管带 exists() 判断，目录缺失时自动跳过，
# 因此本镜像只提供 /api/* 接口；前端用 Nginx 托管 dist 并把 /api 反代到本镜像。
#
# 构建（在 backend/ 目录执行，上下文即 backend/）：
#   docker build -t knowsource-backend:v1.0.0 .
# 运行（需先启动中间件，见仓库根目录 docker-compose.yml）：
#   docker run -p 8000:8000 --env-file .env knowsource-backend:v1.0.0
# K8s 部署清单见 deploy/k8s/（与本镜像完全兼容）
# ══════════════════════════════════════════════════════════════

FROM python:3.11-slim

# tika 文档解析兜底需要 JRE（未安装时 tika 自动降级，其它解析器继续可用）
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# PYTHONUNBUFFERED：日志直出；HF_HOME：嵌入模型缓存，部署时挂载持久卷
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/srv/backend/data/hf

# 先装依赖（利用层缓存：代码变更不触发重装）
# ① 预装 CPU 版 torch：sentence-transformers 默认会拖 CUDA 版（torch 本体 ~900MB
#    + 数 GB nvidia 运行库），本镜像无 GPU，纯浪费；预装后依赖安装直接复用不再下载
# ② -i 清华 PyPI 镜像：国内直连 pypi.org 慢
COPY requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir torch \
        --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r ./backend/requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple

# 后端源码。注意：sql/ 必须保留——init_db 启动时依赖 schema.sql（建表）
# 与 migrations.sql（增量迁移），全新数据库缺它们会漏建表
COPY . ./backend/

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
