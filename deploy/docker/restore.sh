#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 数据导入（在新环境跑：已装好 Docker，代码已同步）
# 配对脚本：backup.sh 产生的备份目录
#
# 用法：./restore.sh <备份目录>
# 流程：先起 PG 导关系数据 → 其余数据冷灌卷 → 一键起全栈
# 注意：会把目标卷清空重灌；密码必须与旧环境一致（见 README）
# ══════════════════════════════════════════════════════════════
set -euo pipefail

DIR="${1:?用法: ./restore.sh <备份目录>}"
[ -f "$DIR/postgres.dump" ] || { echo "备份目录无效：缺 $DIR/postgres.dump"; exit 1; }
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.prod.yml"

untarvol() {  # untarvol <卷名> <备份文件.tgz> —— 清空目标卷后回灌
  echo "  $DIR/$2 → 卷 $1"
  docker run --rm -v "$1":/dst -v "$(cd "$DIR" && pwd)":/bak alpine \
    sh -c 'rm -rf /dst/* /dst/..?* /dst/.[!.]*; tar xzf "$1" -C /dst' _ "/bak/$2"
}

echo "==> 1/4 停栈（卷保留），确保没有服务占用数据卷"
$COMPOSE down 2>/dev/null || true

echo "==> 2/4 起 PostgreSQL 并等待 healthy"
$COMPOSE up -d postgres
for i in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' ontology-postgres 2>/dev/null)" = "healthy" ] && break
  [ "$i" = 60 ] && { echo "PG 未在预期时间内 healthy"; exit 1; }
  sleep 2
done

echo "==> 3/4 导入 PostgreSQL（--clean 覆盖 backend 自动建的表）"
docker exec -i ontology-postgres \
  pg_restore -U ontology -d knowsource --clean --if-exists < "$DIR/postgres.dump"

echo "==> 4/4 其余数据冷灌卷（Neo4j / Milvus 三件套 / 应用卷，容器未启动，无并发写）"
untarvol knowsource_neo4j_data   neo4j_data.tgz
untarvol knowsource_neo4j_import neo4j_import.tgz
untarvol knowsource_milvus_data  milvus_data.tgz
untarvol knowsource_etcd_data    etcd_data.tgz
untarvol knowsource_minio_data   minio_data.tgz
untarvol knowsource-data       knowsource-data.tgz
untarvol knowsource-uploads    knowsource-uploads.tgz

echo "==> 起全栈"
$COMPOSE up -d
echo
echo "导入完成。验证："
echo "  $COMPOSE ps"
echo "  curl http://127.0.0.1/api/healthz"
