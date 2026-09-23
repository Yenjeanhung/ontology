#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 数据导出（在旧环境跑：ontology 开发栈所在机器）
# 产物：一个备份目录（拷到新机器后用 restore.sh 导入）
#
# 用法：./backup.sh [输出目录]     默认 ./backup_<时间戳>
# 前提：ontology-postgres 在运行（在线 pg_dump）；
#       脚本会临时停止 neo4j/milvus/etcd/minio 做冷拷贝（保证一致性）
# ══════════════════════════════════════════════════════════════
set -euo pipefail

OUT="${1:-backup_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$OUT"

tarvol() {  # tarvol <卷名> <输出文件名.tgz>
  echo "  $1 → $OUT/$2"
  docker run --rm -v "$1":/src:ro -v "$PWD:/bak" alpine \
    tar czf "/bak/$2" -C /src .
}

echo "==> 1/5 停止写入型服务（PG 在线导出不受影响）"
docker stop ontology-neo4j ontology-milvus ontology-milvus-etcd ontology-milvus-minio 2>/dev/null || true
docker rm -f amazing_joliot 2>/dev/null || true   # 手工 run 的旧后端容器（若存在）

echo "==> 2/5 PostgreSQL 在线逻辑导出"
docker exec ontology-postgres pg_dump -U ontology -d knowsource -Fc > "$OUT/postgres.dump"

echo "==> 3/5 Neo4j 冷拷贝"
tarvol ontology_neo4j_data   neo4j_data.tgz
tarvol ontology_neo4j_import neo4j_import.tgz

echo "==> 4/5 Milvus 三件套冷拷贝（milvus + etcd + minio，三者必须成套）"
tarvol ontology_milvus_data milvus_data.tgz
tarvol ontology_etcd_data   etcd_data.tgz
tarvol ontology_minio_data  minio_data.tgz

echo "==> 5/5 后端应用数据（嵌入模型缓存 / 上传件）"
tarvol knowsource-data    knowsource-data.tgz
tarvol knowsource-uploads knowsource-uploads.tgz

echo
echo "导出完成：$OUT/"
ls -lh "$OUT"
echo "下一步：把整个目录拷到新机器，跑 ./restore.sh $OUT 的拷贝路径"
