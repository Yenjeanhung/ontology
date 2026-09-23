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
| `backup.sh` | 旧环境数据导出：PG 逻辑导出 + Neo4j/Milvus/应用卷冷拷贝 → 备份目录 |
| `restore.sh` | 新环境数据导入：起 PG 导入 → 冷灌其余卷 → 起全栈（与 backup.sh 配对） |

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

```bash
docker compose -f docker-compose.prod.yml up -d --build backend    # 只重建后端
docker compose -f docker-compose.prod.yml up -d --build frontend   # 只重建前端
docker compose -f docker-compose.prod.yml logs -f backend          # 看日志
```

compose 的 `build:` 字段替代了手敲 `docker build`；`image:` 给产物打固定 tag。
跨机交付（构建机 ≠ 部署机）时改用镜像分发：`docker build → tag → push`，
部署机 `pull` 后把 compose 里 `build:` 段删掉留 `image:`（命令见
`doc/部署/前后端部署指南.md` §7）。

## 五、与开发环境（根目录 compose）的关系

- **同一台机器二选一**：容器名/端口与开发栈相同（`ontology-postgres` 等），
  同时起会冲突；先 `docker compose down`（根目录）再起本栈。
- **数据彻底隔离**：本栈用独立命名卷（`knowsource_*` 前缀），与开发卷
  （`ontology_*`）互不接触；数据进入只走导出/导入脚本（见下节），不做卷直连。
- 本栈默认 `name: knowsource`，网络/卷前缀可预测，不受执行目录影响。

## 六、运维要点

- **备份/迁移**：数据进出统一走本目录 `backup.sh` / `restore.sh`（也是定期备份方案）。
  完整教程（三种场景 / 前置检查 / 验证清单 / 排错与回退）见 `doc/部署/数据迁移教程.md`。
  ```bash
  bash backup.sh                    # 从来源环境（开发栈/上一台机器）导出 → backup_<时间戳>/
  scp -r backup_20260923_* new:/srv/knowsource/   # 跨机器时拷贝备份目录
  bash restore.sh backup_20260923_* # 导入本栈卷并起全栈（同机切换 = down 旧栈后执行）
  ```
  要点：PG 用 `pg_dump -Fc` 逻辑导出（跨版本稳）；Milvus 必须连 etcd/MinIO 三个
  卷成套迁移；导入会清空目标卷重灌；密码须与来源环境一致。
- **回滚**：`image:` 改回旧版本号，`up -d`（无 `--build`）即用仓库/本地旧镜像。
- **HTTPS**：入口只有前端容器一个（80 端口）。挂 TLS 两选一：
  云负载均衡终结证书回源 80，或宿主机 Nginx/Caddy 443 反代到本栈。
- **单实例约束**：后端内嵌 APScheduler 定时引擎与本地嵌入模型，**不要把
  backend 副本数调大于 1**（任务重复调度 + 内存翻倍）；要横向扩容先拆调度器（见 `../k8s/README.md` §六）。
- **资源基线**：整机内存 ≥ 16GB（Milvus standalone 约 4~6GB + Neo4j 堆 4G + 嵌入模型 2G）。
