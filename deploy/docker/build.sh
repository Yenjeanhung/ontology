#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 构建 +（可选）推送自研镜像
#
#   bash build.sh                     # 前后端都构建，tag = 当前时间到分（20260929-0845）
#   bash build.sh frontend            # 只构建前端
#   bash build.sh backend -t v1.0.1   # 手动指定版本号
#   bash build.sh all -p              # 构建并按同一 tag 推送到阿里云仓库
#   bash build.sh frontend -b         # 只构建、不起容器（等部署机 pull）
#
# tag 规则：给了 -t 就用指定的；否则用 YYYYmmdd-HHMM（同一分钟重复构建会覆盖，
# 需要区分就手动 -t）。tag 会写进镜像名 knowsource-<服务>:<TAG>，
# 由 docker-compose.prod.yml 的 image: ${TAG:-v1.0.0} 承接。
# ══════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"

REG=crpi-6kpkr0i3rgonuwur.cn-zhangjiakou.personal.cr.aliyuncs.com
NS=yjh_ontology                       # 阿里云命名空间（控制台里先建好）
FILE=docker-compose.prod.yml

usage() {
  sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'
}

SVC=all
TAG=""
PUSH=0
UP=1

while [ $# -gt 0 ]; do
  case "$1" in
    backend|frontend|all) SVC="$1"; shift ;;
    -t|--tag)   TAG="${2:-}"; shift 2 ;;
    -p|--push)  PUSH=1; shift ;;
    -b|--build-only) UP=0; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "[错误] 未知参数：$1" >&2; usage >&2; exit 1 ;;
  esac
done

# 未手动指定 → 当前时间到分
[ -z "$TAG" ] && TAG="$(date +%Y%m%d-%H%M)"
export TAG                              # compose 的 ${TAG:-...} 靠它

if [ "$SVC" = all ]; then SVCS="backend frontend"; else SVCS="$SVC"; fi

echo "==> TAG=$TAG  服务=$SVCS"
for s in $SVCS; do
  echo "--- build $s ---"
  # compose 内嵌 bake 会强制附带 attestation 清单，阿里云个人版拒收，故直接 docker build 禁掉
  case "$s" in backend) ctx=../../backend ;; frontend) ctx=../../front ;; esac
  docker build --provenance=false --sbom=false -t "knowsource-$s:$TAG" "$ctx"
  if [ "$PUSH" -eq 1 ]; then
    docker tag "knowsource-$s:$TAG" "$REG/$NS/knowsource-$s:$TAG"
    docker push "$REG/$NS/knowsource-$s:$TAG"
  fi
done

if [ "$UP" -eq 1 ]; then
  # 无 --build：用刚打好的镜像替换容器（${TAG} 已注入镜像名）
  docker compose -f "$FILE" up -d $SVCS
fi

echo "==> 完成：$SVCS → knowsource-*:${TAG}"
if [ "$PUSH" -eq 0 ]; then
  echo "    推送可加 -p；也可手动：docker push $REG/$NS/knowsource-<svc>:${TAG}"
fi
