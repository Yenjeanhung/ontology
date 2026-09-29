@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
rem ══════════════════════════════════════════════════════════════
rem  数据导入（在新环境跑：已装好 Docker Desktop，代码已同步）
rem  配对脚本：backup.bat / backup.sh 产生的备份目录
rem
rem  用法：restore.bat <备份目录>
rem  流程：停栈 → 起 PG 导关系数据 → 其余数据冷灌卷 → 起全栈
rem  注意：会把目标卷清空重灌；密码必须与旧环境一致（见 README）
rem
rem  说明：与 restore.sh 等价，Windows（cmd.exe）专用，需 UTF-8 无 BOM 保存
rem ══════════════════════════════════════════════════════════════

where docker >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 docker 命令，请先安装并启动 Docker Desktop
  exit /b 1
)

set "DIR=%~1"
if not defined DIR (
  echo 用法: restore.bat ^<备份目录^>
  exit /b 1
)
if not exist "%DIR%\postgres.dump" (
  echo [错误] 备份目录无效：缺 %DIR%\postgres.dump
  exit /b 1
)
for %%p in ("%DIR%") do set "DIRABS=%%~fp"

cd /d "%~dp0"
if not exist "docker-compose.prod.yml" (
  echo [错误] 当前目录没有 docker-compose.prod.yml，请在 deploy\docker 下运行
  exit /b 1
)

echo ==^> 1/4 停栈（卷保留），确保没有服务占用数据卷
docker compose -f docker-compose.prod.yml down
if errorlevel 1 (
  echo    未在运行或停止失败，继续（不影响后续导入）
)

echo ==^> 2/4 起 PostgreSQL 并等待 healthy
docker compose -f docker-compose.prod.yml up -d postgres
if errorlevel 1 (
  echo [错误] PostgreSQL 启动失败
  exit /b 1
)
set "STATUS="
for /L %%i in (1,1,60) do (
  set "STATUS="
  for /f "delims=" %%s in ('docker inspect -f "{{.State.Health.Status}}" ontology-postgres 2^>nul') do set "STATUS=%%s"
  if "!STATUS!"=="healthy" goto :pg_ready
  rem 等 2 秒（不用 timeout：stdin 被重定向时会报 Input redirection is not supported）
  ping -n 3 127.0.0.1 >nul 2>nul
)
echo [错误] PostgreSQL 未在预期时间内 healthy
exit /b 1
:pg_ready
echo   PostgreSQL healthy

echo ==^> 3/4 导入 PostgreSQL（--clean 覆盖 backend 自动建的表）
docker cp "%DIRABS%\postgres.dump" ontology-postgres:/tmp/pg_restore.dump
if errorlevel 1 (
  echo [错误] 拷入 dump 失败
  exit /b 1
)
docker exec ontology-postgres sh -c "pg_restore -U ontology -d knowsource --clean --if-exists /tmp/pg_restore.dump"
if errorlevel 1 (
  echo    [警告] pg_restore 返回非 0：--clean 删除不存在的对象属常见告警，请核对上方日志
)
docker exec ontology-postgres rm -f /tmp/pg_restore.dump
if errorlevel 1 (
  echo    [警告] 容器内临时 dump 删除失败，可忽略
)

echo ==^> 4/4 其余数据冷灌卷（Neo4j / Milvus 三件套 / 应用卷，容器未启动，无并发写）
call :untarvol knowsource_neo4j_data   neo4j_data.tgz
if errorlevel 1 exit /b 1
call :untarvol knowsource_neo4j_import neo4j_import.tgz
if errorlevel 1 exit /b 1
call :untarvol knowsource_milvus_data  milvus_data.tgz
if errorlevel 1 exit /b 1
call :untarvol knowsource_etcd_data    etcd_data.tgz
if errorlevel 1 exit /b 1
call :untarvol knowsource_minio_data   minio_data.tgz
if errorlevel 1 exit /b 1
call :untarvol knowsource-data         knowsource-data.tgz
if errorlevel 1 exit /b 1
call :untarvol knowsource-uploads      knowsource-uploads.tgz
if errorlevel 1 exit /b 1

echo ==^> 起全栈
docker compose -f docker-compose.prod.yml up -d
if errorlevel 1 (
  echo    [警告] 起栈未完全成功（常见原因：前端镜像构建拉不到基镜像）。数据已导入，
  echo           网络/镜像问题解决后重跑：docker compose -f docker-compose.prod.yml up -d --build
  exit /b 1
)

echo.
echo 导入完成。验证：
echo   docker compose -f docker-compose.prod.yml ps
echo   curl http://127.0.0.1/api/healthz
exit /b 0

rem ──────────────────────────────────────────────────────────────
rem  untarvol <卷名> <备份文件.tgz>：清空目标卷后回灌
rem ──────────────────────────────────────────────────────────────
:untarvol
echo   %~2 → 卷 %~1
rem 清空用 find 而非 /dst/.[!.]*：本脚本开了延迟展开，! 会被吞掉（等价 .sh 的清卷效果）
docker run --rm -v "%~1":/dst -v "%DIRABS%":/bak alpine sh -c "rm -rf /dst/* ; find /dst -mindepth 1 -maxdepth 1 -name '.*' -exec rm -rf {} \; ; tar xzf /bak/%~2 -C /dst"
if errorlevel 1 (
  echo [错误] 回灌 %~2 失败
  exit /b 1
)
exit /b 0
