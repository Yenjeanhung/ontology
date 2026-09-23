#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# KnowSource 前端一键部署：构建 → 推送 → 应用前端清单 → 滚动更新
#
# 用法：
#   export REGISTRY=registry.cn-hangzhou.aliyuncs.com/your-ns
#   export VERSION=v1.0.0            # 缺省用时间戳
#   bash deploy/k8s/build-frontend.sh
#
# 说明：纯静态站点，与后端零耦合，可独立发布/扩缩容，无需 Secret 预检；
#       Ingress（02-app.yaml）的 /api /docs 段指向后端 Service，不受本脚本影响。
# 镜像名约定：$REGISTRY/knowsource-frontend:$VERSION（改名需同步改本脚本 set image 目标）
# ══════════════════════════════════════════════════════════════
set -euo pipefail

REGISTRY="${REGISTRY:?请先 export REGISTRY=<你的镜像仓库地址>}"
VERSION="${VERSION:-$(date +%Y%m%d-%H%M%S)}"
DIR="$(cd "$(dirname "$0")" && pwd)"
NS=knowsource
IMAGE="$REGISTRY/knowsource-frontend:$VERSION"

# ── 预检：依赖 ──
command -v docker  >/dev/null || { echo "✗ docker 不可用"  >&2; exit 1; }
command -v kubectl >/dev/null || { echo "✗ kubectl 不可用" >&2; exit 1; }

echo "==> [1/4] 构建前端镜像（上下文 front/）：$IMAGE"
docker build -t "$IMAGE" "$DIR/../../front"

echo "==> [2/4] 推送镜像"
docker push "$IMAGE"

echo "==> [3/4] 应用清单（namespace / frontend）"
kubectl apply -f "$DIR/00-namespace.yaml"
kubectl apply -f "$DIR/03-frontend.yaml"

echo "==> [4/4] 指定镜像版本并等待就绪"
kubectl -n "$NS" set image deploy/knowsource-frontend frontend="$IMAGE"
kubectl -n "$NS" rollout status deploy/knowsource-frontend --timeout=120s

echo "✓ 前端发布完成，访问 Ingress 域名即可"
