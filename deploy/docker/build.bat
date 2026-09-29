@echo off
chcp 65001 >nul
rem ══════════════════════════════════════════════════════════════
rem 构建 +（可选）推送自研镜像（与 build.sh 等价，Windows cmd.exe 用）
rem
rem   build.bat                     :: 前后端都构建，tag = 当前时间到分（20260929-0845）
rem   build.bat frontend            :: 只构建前端
rem   build.bat backend -t v1.0.1   :: 手动指定版本号
rem   build.bat all -p              :: 构建并按同一 tag 推送到阿里云仓库
rem   build.bat frontend -b         :: 只构建、不起容器（等部署机 pull）
rem ══════════════════════════════════════════════════════════════
setlocal EnableExtensions
cd /d "%~dp0"

set "REG=crpi-6kpkr0i3rgonuwur.cn-zhangjiakou.personal.cr.aliyuncs.com"
set "NS=yjh_ontology"
set "FILE=docker-compose.prod.yml"
set "SVC=all"
set "TAG="
set "PUSH=0"
set "UP=1"

:parse
if "%~1"=="" goto gotargs
if /i "%~1"=="-t" (set "TAG=%~2" & shift & shift & goto parse)
if /i "%~1"=="-p" (set "PUSH=1" & shift & goto parse)
if /i "%~1"=="-b" (set "UP=0" & shift & goto parse)
if /i "%~1"=="-h" goto usage
if /i "%~1"=="backend"  (set "SVC=backend"  & shift & goto parse)
if /i "%~1"=="frontend" (set "SVC=frontend" & shift & goto parse)
if /i "%~1"=="all"      (set "SVC=all"      & shift & goto parse)
echo [错误] 未知参数：%~1
goto usage

:gotargs
rem 未手动指定 → 当前时间到分（PowerShell 优先，取不到再退回 %date%/%time%）
if not defined TAG (
  for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"`) do set "TAG=%%i"
)
if not defined TAG (
  for /f "tokens=1-3 delims=/- " %%a in ("%date%") do set "D=%%a%%b%%c"
  for /f "tokens=1-2 delims=:." %%a in ("%time%") do set "T=%%a%%b"
  set "TAG=%D%-%T%"
)

echo ==^> TAG=%TAG% 服务=%SVC%
if /i "%SVC%"=="all" (
  call :dosvc backend || goto fail
  call :dosvc frontend || goto fail
) else (
  call :dosvc %SVC% || goto fail
)

if "%PUSH%"=="0" echo     推送可加 -p
echo ==^> 完成：%SVC% --^> knowsource-*:%TAG%
goto :eof

:usage
echo 用法: build.bat [backend^|frontend^|all] [-t ^<版本号^>] [-p] [-b]
echo   -t  手动指定版本号；不给则用当前时间到分
echo   -p  构建后按同一 tag 推送到阿里云仓库（需先 docker login）
echo   -b  只构建镜像，不起容器
exit /b 1

:fail
echo [错误] 构建失败
exit /b 1

:dosvc
echo --- build %~1 ---
rem compose 内嵌 bake 会强制附带 attestation 清单，阿里云个人版拒收，故直接 docker build 禁掉
if /i "%~1"=="backend" (set "CTX=..\..\backend") else set "CTX=..\..\front"
docker build --provenance=false --sbom=false -t knowsource-%~1:%TAG% "%CTX%" || exit /b 1
if "%PUSH%"=="1" (
  docker tag knowsource-%~1:%TAG% %REG%/%NS%/knowsource-%~1:%TAG% || exit /b 1
  docker push %REG%/%NS%/knowsource-%~1:%TAG% || exit /b 1
)
if "%UP%"=="1" docker compose -f "%FILE%" up -d %~1 || exit /b 1
exit /b 0
