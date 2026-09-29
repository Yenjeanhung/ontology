@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
rem ══════════════════════════════════════════════════════════════
rem  数据导出（在旧环境跑：ontology 开发栈所在机器）
rem  产物：一个备份目录（拷到新机器后用 restore.bat 导入）
rem
rem  用法：backup.bat [输出目录]     默认 .\backup_<时间戳>
rem  前提：ontology-postgres 在运行（在线 pg_dump）；
rem        脚本会临时停止 neo4j/milvus/etcd/minio 做冷拷贝（保证一致性）
rem
rem  说明：与 backup.sh 等价，Windows（cmd.exe）专用，需 UTF-8 无 BOM 保存
rem ══════════════════════════════════════════════════════════════

where docker >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 docker 命令，请先安装并启动 Docker Desktop
  exit /b 1
)

rem ---- 时间戳（优先用 PowerShell，拿不到再退回 %date%/%time%）----
set "TS="
for /f "delims=" %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss" 2^>nul') do set "TS=%%a"
if not defined TS set "TS=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "TS=%TS: =0%"

rem ---- 输出目录 ----
set "OUT=%~1"
if not defined OUT set "OUT=backup_%TS%"
if not exist "%OUT%" mkdir "%OUT%"
for %%p in ("%OUT%") do set "OUTABS=%%~fp"

echo ==^> 1/5 停止写入型服务（PG 在线导出不受影响）
docker stop ontology-neo4j ontology-milvus ontology-milvus-etcd ontology-milvus-minio
if errorlevel 1 (
  echo    无运行中容器，跳过
)
rem 手工 run 的旧后端容器（若存在）
docker rm -f amazing_joliot
if errorlevel 1 (
  echo    无 amazing_joliot 容器，跳过
)

echo ==^> 2/5 PostgreSQL 在线逻辑导出
docker exec ontology-postgres sh -c "pg_dump -U ontology -d knowsource -Fc -f /tmp/pg_backup.dump"
if errorlevel 1 (
  echo [错误] pg_dump 失败，确认 ontology-postgres 在运行
  exit /b 1
)
docker cp ontology-postgres:/tmp/pg_backup.dump "%OUT%\postgres.dump"
if errorlevel 1 (
  echo [错误] 拷出 dump 失败
  exit /b 1
)
docker exec ontology-postgres rm -f /tmp/pg_backup.dump
if errorlevel 1 (
  echo    [警告] 容器内临时 dump 删除失败，可忽略
)

echo ==^> 3/5 Neo4j 冷拷贝
call :tarvol ontology_neo4j_data   neo4j_data.tgz
if errorlevel 1 exit /b 1
call :tarvol ontology_neo4j_import neo4j_import.tgz
if errorlevel 1 exit /b 1

echo ==^> 4/5 Milvus 三件套冷拷贝（milvus + etcd + minio，三者必须成套）
call :tarvol ontology_milvus_data milvus_data.tgz
if errorlevel 1 exit /b 1
call :tarvol ontology_etcd_data   etcd_data.tgz
if errorlevel 1 exit /b 1
call :tarvol ontology_minio_data  minio_data.tgz
if errorlevel 1 exit /b 1

echo ==^> 5/5 后端应用数据（嵌入模型缓存 / 上传件）
call :tarvol knowsource-data    knowsource-data.tgz
if errorlevel 1 exit /b 1
call :tarvol knowsource-uploads knowsource-uploads.tgz
if errorlevel 1 exit /b 1

echo.
echo 导出完成：%OUT%\
dir "%OUT%"
echo 下一步：把整个目录拷到新机器，跑 restore.bat ^<该目录路径^>
exit /b 0

rem ──────────────────────────────────────────────────────────────
rem  tarvol <卷名> <输出文件名.tgz>：把 docker 卷打包到备份目录
rem ──────────────────────────────────────────────────────────────
:tarvol
echo   %~1 → %OUT%\%~2
docker run --rm -v "%~1":/src:ro -v "%OUTABS%":/bak alpine tar czf "/bak/%~2" -C /src .
if errorlevel 1 (
  echo [错误] 导出卷 %~1 失败
  exit /b 1
)
exit /b 0
