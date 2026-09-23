#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# KnowSource 后端一键部署：构建 → 推送 → 应用清单 → 滚动更新 → 验证
#
# 用法：
#   export REGISTRY=registry.cn-hangzhou.aliyuncs.com/your-ns
#   export VERSION=v1.0.0            # 缺省用时间戳；生产建议语义化版本
#   bash deploy/k8s/build-backend.sh
#
# 前置：01-config.yaml 的 <CHANGE_ME> 占位符必须先填写
# 镜像名约定：$REGISTRY/knowsource-backend:$VERSION（改名需同步改本脚本 set image 目标）
# ══════════════════════════════════════════════════════════════
set -euo pipefail

REGISTRY="${REGISTRY:?请先 export REGISTRY=<你的镜像仓库地址>}"
VERSION="${VERSION:-$(date +%Y%m%d-%H%M%S)}"
DIR="$(cd "$(dirname "$0")" && pwd)"
NS=knowsource
IMAGE="$REGISTRY/knowsource-backend:$VERSION"

# ── 预检：依赖与 Secret 占位符 ──
command -v docker  >/dev/null || { echo "✗ docker 不可用"  >&2; exit 1; }
command -v kubectl >/dev/null || { echo "✗ kubectl 不可用" >&2; exit 1; }
if grep -q "CHANGE_ME" "$DIR/01-config.yaml"; then
    echo "✗ 01-config.yaml 仍有 <CHANGE_ME> 占位符，请先填写数据库/Neo4j/LangSmith 密钥" >&2
    exit 1
fi

echo "==> [1/5] 构建后端镜像（上下文 backend/）：$IMAGE"
docker build -t "$IMAGE" "$DIR/../../backend"

echo "==> [2/5] 推送镜像"
docker push "$IMAGE"

echo "==> [3/5] 应用清单（namespace / config / app）"
kubectl apply -f "$DIR/00-namespace.yaml"
kubectl apply -f "$DIR/01-config.yaml"
kubectl apply -f "$DIR/02-app.yaml"

echo "==> [4/5] 指定镜像版本并等待就绪（首启含建表/迁移+模型加载，窗口放宽）"
kubectl -n "$NS" set image deploy/knowsource knowsource="$IMAGE"
kubectl -n "$NS" rollout status deploy/knowsource --timeout=600s

echo "==> [5/5] 后端发布完成。手动验证："
echo "  kubectl -n $NS port-forward deploy/knowsource 8000:8000"
echo "  curl http://127.0.0.1:8000/api/healthz            # 后端进程探针"
echo "  curl http://127.0.0.1:8000/api/agent/multi/tools  # 工具链自检"
