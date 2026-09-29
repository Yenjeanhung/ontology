# KnowSource 单机部署（中小团队，无 K8s）

## 一、这形态适合谁

| | 本目录（单机 compose） | `../k8s/`（K8s） |
|---|---|---|
| 适用 | 几台以内机器、团队 < 10 人、发布频率低 | 多节点、要滚动更新/自愈/弹性伸缩 |
| 运维心智 | 会 docker 就行 | 需要 K8s 技能栈 |
| 中断窗口 | 更新时秒级中断（单实例） | 滚动/灰度，用户无感 |
| 升级路径 | 业务涨了 → 清单翻译成 K8s YAML，参数一一对应 | —— |

中间件已在单机跑稳、没有多机编排诉求时，**不必为了"正规"而上 K8s**。

## 二、形态与文件

```
浏览器 ──► frontend 容器（Nginx，宿主 80）
             ├── /               → front/dist 静态资源（SPA 回退）
             ├── /api /docs ...  → backend 容器（FastAPI，单实例）
             │                       ├── PostgreSQL / Neo4j / Milvus(+etcd+MinIO)
             │                       └── vLLM（独立 GPU 机器，OpenAI 兼容接口）
             └── SSE：proxy_buffering off + read_timeout 3600s
```

| 文件 | 内容 |
|---|---|
| `docker-compose.prod.yml` | 全栈：中间件 + 后端 + 前端，一条命令起全部 |
| `nginx.conf` | 前端容器配置：静态托管 + `/api` 反代 + SSE 纪律（挂载进容器，改配置免重建镜像） |
| `backup.sh` / `backup.bat` | 旧环境数据导出：PG 逻辑导出 + Neo4j/Milvus/应用卷冷拷贝 → 备份目录 |
| `restore.sh` / `restore.bat` | 新环境数据导入：起 PG 导入 → 冷灌其余卷 → 起全栈（与 backup.sh 配对） |
| `build.sh` / `build.bat` | 打自研镜像：默认用「时间到分」当版本号，可用 `-t` 手动指定，可选 `-p` 推送阿里云 |

两套脚本等价：Linux/macOS 用 `.sh`，Windows（cmd.exe）用 `.bat`。
`.bat` 版本需以 **UTF-8 无 BOM** 保存（首行已 `chcp 65001`，否则中文注释乱码）；
另外脚本要把备份目录挂进 alpine 容器，需在 Docker Desktop → Settings →
Resources → File sharing 里勾选备份目录所在的盘符（如 `D:`）。

## 三、三步部署

```bash
# 1. 改配置：中间件默认密码（与 backend/.env 同步改）；
#    LLM 的 Key 在页面「配置中心」维护，不进 .env 也不进镜像

# 2. 一条命令起全部（首启自动构建镜像；中间件 healthy 后才起后端）
cd deploy/docker
docker compose -f docker-compose.prod.yml up -d --build

# 3. 验证
docker compose -f docker-compose.prod.yml ps    # 全部 healthy / running
curl http://127.0.0.1/api/healthz                # 经前端反代探后端
# 浏览器访问 http://<服务器IP>/
```

首启注意：后端要建表/迁移 + 下载嵌入模型（HF 镜像加速已配），日志看到
`Application startup complete` 前接口可能 502，属正常等待。

## 四、日常发布

改完代码用 `build.sh` / `build.bat`（版本号默认时间到分），详见「改了代码怎么发布」。
本节先把背后的机制讲清楚，方便你不靠脚本也能操作（看日志固定用
`docker compose -f docker-compose.prod.yml logs -f backend`）。

compose 的 `build:` 字段替代了手敲 `docker build`；`image:` 给产物打 tag，
写成 `${TAG:-v1.0.0}`——**不带环境变量时仍是 `v1.0.0`**，带了就用环境变量的值
（`build.sh` / `build.bat` 就是靠它把版本号注进镜像名的）。
跨机交付（构建机 ≠ 部署机）时改用镜像分发：`docker build → tag → push`，
部署机 `pull` 后把 compose 里 `build:` 段删掉留 `image:`（命令见
`doc/部署/前后端部署指南.md` §7）。

### 单独打镜像：手敲 `docker build`

compose 用的是 `build: ../../backend` / `build: ../../front`——**构建上下文就是那两个
目录本身**，所以手敲时 `cd` 进去、`docker build` 的点号指代当前目录：

```bash
# 后端（上下文 = backend/，里面有 .dockerignore 已排掉 data/uploads/logs/.env）
cd backend
docker build -t knowsource-backend:v1.0.0 .

# 前端（上下文 = front/；会把 front/nginx.conf 烤进镜像、跑 npm ci + npm run build）
cd front
docker build -t knowsource-frontend:v1.0.0 .
```

要点：

- 上下文必须是**包目录自身**（不是仓库根）：两个 Dockerfile 里都是
  `COPY requirements.txt ./backend/...` / `COPY . .`，用根目录当上下文会多传几 GB
  且路径对不上。
- 根目录那个 `Dockerfile`（前后端一体化镜像）**不在本栈使用**，别拿它打包。
- `front/nginx.conf` 变更要重建镜像；本目录 `nginx.conf` 是 compose 挂载进去的，改完
  `docker compose restart frontend` 即可，不用重建。

### 改了代码怎么发布

统一用本目录的 `build.sh` / `build.bat`（Linux 用 `.sh`，Windows cmd.exe 用 `.bat`，
两者等价）。**版本号规则**：

- 不指定 → 自动用**当前时间到分**（如 `20260929-1003`），镜像名
  `knowsource-frontend:20260929-1003`；
- 指定 `-t`（如 `-t v1.0.1`）→ 用你给的版本号。

```bash
bash build.sh                      # 前后端都构建并用新镜像替换容器（最常用）
bash build.sh backend              # 只改了后端
bash build.sh frontend             # 只改了前端
bash build.sh frontend -t v1.0.1   # 手动指定版本号（正式版/里程碑）
bash build.sh frontend -b          # 只打镜像不起容器（准备给部署机 pull）
bash build.sh all -p               # 构建 + 按同一版本号推送阿里云
```

```bat
build.bat                          :: Windows 下同上（tag 同样是时间到分）
build.bat backend
build.bat frontend -t v1.0.1
```

脚本内部是 `docker build --provenance=false --sbom=false`（原因见下节要点）+
`docker compose up -d`；不想用脚本就手敲 `up -d --build`，只是 tag 固定回 `v1.0.0`：

```bash
# 先改docker目录下的.env中的版本,docker compose中打包时会使用该版本打包和部署
docker compose -f docker-compose.prod.yml up -d --build backend
docker compose -f docker-compose.prod.yml up -d --build frontend
docker compose -f docker-compose.prod.yml logs -f backend   # 看到 Application startup complete
```

前端改完浏览器 Ctrl+F5 强刷（Vite 产物文件名带 hash，一般自动失效缓存）。
构建速度：依赖层（pip / npm ci）有缓存，**只改源码**时后端约 10~30s、前端约 10~20s；
改了 `requirements.txt` / `package.json` 才会重装依赖，明显变慢。

跨机交付时用 `-p` 一步完成「构建 → 打远端 tag → push」（见下节），
部署机 `pull` 后 `up -d`（无 `--build`）。
版本号要不要每次都换：**同机构建部署**不必（本地 tag 覆盖即生效）；
**经仓库交付**必须换——部署机本地已有同名 tag 时 `up -d` 不会重拉，
必须显式 `docker compose pull`，且旧镜像会被覆盖、失去回滚点。

### 推送镜像到阿里云仓库
地址：https://cr.console.aliyun.com/cn-zhangjiakou/instance/namespaces
本目录只构建两个自研镜像（`knowsource-backend` / `knowsource-frontend`），
中间件一律用公共镜像，不需要推。

**首选**：用上一节的构建脚本一键完成，版本号沿用「时间到分」（如 `20260929-1003`）：

```bash
docker login --username=yanjiaheng crpi-6kpkr0i3rgonuwur.cn-zhangjiakou.personal.cr.aliyuncs.com   # 一次即可
bash build.sh all -b -t 20260929-1003 -p     # 构建 + 打远端 tag + 推送（也可让脚本自己生成时间 tag）
```

等价的手敲步骤如下（脚本里的 `REG` / `NS` 就是下面这俩变量，可自行改动）：

```bash
# 0. 变量：REG 为仓库公网地址，NS 为命名空间（在阿里云控制台「命名空间」里建，
#    如不存在的命名空间要先建，push 不会自动创建）
REG=crpi-6kpkr0i3rgonuwur.cn-zhangjiakou.personal.cr.aliyuncs.com
NS=yjh_ontology            # ← 换成你的命名空间
VER=20260929-1003          # ← 版本号，默认用「时间到分」，正式版可用 v1.0.1

# 1. 登录（密码是阿里云镜像服务开通时设的固定密码，不是账号登录密码）
docker login --username=yanjiaheng $REG
密码:yjh....!

# 2. 本地已有镜像（up -d --build 会自动打出 knowsource-*:v1.0.0），打远端 tag
docker tag knowsource-backend:$VER  $REG/$NS/knowsource-backend:$VER
docker tag knowsource-frontend:$VER $REG/$NS/knowsource-frontend:$VER

# 3. 推送
docker push $REG/$NS/knowsource-backend:$VER
docker push $REG/$NS/knowsource-frontend:$VER
```

部署机侧：

```bash
docker login --username=yanjiaheng $REG
docker pull $REG/$NS/knowsource-backend:$VER
docker pull $REG/$NS/knowsource-frontend:$VER
# 方式 A（推荐）：保留 build: 段，把版本号传进去，compose 直接用同名镜像
#   image: knowsource-backend:${TAG:-v1.0.0} → TAG=20260929-1003 docker compose up -d
# 方式 B：把 backend/frontend 的 build: 段删掉，image: 改成 $REG/$NS/knowsource-*:<VER>
docker compose -f docker-compose.prod.yml up -d        # 无 --build，直接用已 pull 的镜像
```

注意：每发一个新版本号，部署机都要重新 `docker pull`（或 `docker compose pull`）——
`up -d` 只在本地「没有该 tag」时才拉。

要点：

- **个人版 ACR 不认 attestation 清单**：新版 Docker 构建（尤其 `docker compose build`）
  会默认附带 provenance/SBOM 清单（`application/vnd.oci.empty.v1+json`），push 时报
  `error from registry: unknown manifest class ...`、层全部传完仍然失败。
  构建脚本已用 `docker build --provenance=false --sbom=false` 规避；
  手敲构建时记得带这两个参数。
- **登录态**落在 `~/.docker/config.json`，长期不用记得 `docker logout $REG`；
  公共 CI/多用户机器建议改用短期凭证，别把密码写进脚本。
- **镜像里不含任何密钥**：LLM Key 走页面「配置中心」入库，中间件密码走
  `.env` / compose 环境变量，构建产物可以放心推。
- **版本号即回滚单位**：默认「时间到分」天然递增；正式版可用 `v1.0.1` 之类。
  老版本号留在仓库就是回滚点——把 compose 的 `image:` 改回旧 tag 后 `up -d` 即可。

## 五、与开发环境（根目录 compose）的关系

- **同一台机器二选一**：容器名/端口与开发栈相同（`ontology-postgres` 等），
  同时起会冲突；先 `docker compose down`（根目录）再起本栈。
- **数据彻底隔离**：本栈用独立命名卷（`knowsource_*` 前缀），与开发卷
  （`ontology_*`）互不接触；数据进入只走导出/导入脚本（见下节），不做卷直连。
- 本栈默认 `name: knowsource`，网络/卷前缀可预测，不受执行目录影响。

## 六、运维要点

- **备份/迁移**：数据进出统一走本目录 `backup` / `restore` 脚本（也是定期备份方案）。
  完整教程（三种场景 / 前置检查 / 验证清单 / 排错与回退）见 `doc/部署/数据迁移教程.md`。
  ```bash
  bash backup.sh                    # 从来源环境（开发栈/上一台机器）导出 → backup_<时间戳>/
  scp -r backup_20260923_* new:/srv/knowsource/   # 跨机器时拷贝备份目录
  bash restore.sh backup_20260923_* # 导入本栈卷并起全栈（同机切换 = down 旧栈后执行）
  ```
  Windows 用同名的 `.bat`（cmd.exe 里跑）：`backup.bat` → `restore.bat backup_20260923_*`。
  要点：PG 用 `pg_dump -Fc` 逻辑导出（跨版本稳）；Milvus 必须连 etcd/MinIO 三个
  卷成套迁移；导入会清空目标卷重灌；密码须与来源环境一致。
- **回滚**：`image:` 改回旧版本号，`up -d`（无 `--build`）即用仓库/本地旧镜像。
- **HTTPS**：入口只有前端容器一个（80 端口）。挂 TLS 两选一：
  云负载均衡终结证书回源 80，或宿主机 Nginx/Caddy 443 反代到本栈。
- **单实例约束**：后端内嵌 APScheduler 定时引擎与本地嵌入模型，**不要把
  backend 副本数调大于 1**（任务重复调度 + 内存翻倍）；要横向扩容先拆调度器（见 `../k8s/README.md` §六）。
- **资源基线**：整机内存 ≥ 16GB（Milvus standalone 约 4~6GB + Neo4j 堆 4G + 嵌入模型 2G）。
- **首次构建/启动的三个坑（本机实测）**：
  1. **基镜像拉不动**（`auth.docker.io` 超时）：给 Docker Desktop 配 `registry-mirrors`
     （Docker Engine → `{"registry-mirrors":["https://docker.m.daocloud.io"]}` → Apply & Restart），
     或临时 `docker pull docker.m.daocloud.io/library/node:20-alpine` 后
     `docker tag` 回官方名，构建即可命中本地缓存。
  2. **`sqlalchemy` 必须带 asyncio 附加依赖**：`requirements.txt` 写
     `sqlalchemy[asyncio]>=2.0.0`，否则容器里 `import sqlalchemy.ext.asyncio` 直接
     ImportError（缺 greenlet），后端反复 Restarting。
  3. **后端以非 root 运行**：`/srv/logs`、`/srv/backend/{data,uploads}` 必须在
     Dockerfile 里预建并 `chown -R appuser:appuser /srv`（`server.py` 启动即 `LOG_DIR.mkdir`）。
- **嵌入模型（离线）**：`.env` 的 `HF_CACHE_DIR` 是宿主机 Windows 路径，容器内由 compose
  `environment:` 覆盖为 `/srv/backend/data/hf/hub`；模型缓存从 Windows 拷进卷后，
  快照里的软链接目标仍是反斜杠（`..\..\blobs\xxx`），Linux 下全部失效，必须重建为正斜杠链接，
  否则离线加载报 `couldn't find them in the cached files`。
