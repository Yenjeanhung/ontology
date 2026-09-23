# KnowSource K8s / 生产部署指南（最终形态）

## 一、最终生产部署形态

```
                          ┌────────────────────────────────────────────┐
        用户浏览器          │           入口层（TLS 终结）                │
      ──────────────────►  │  单机：宿主机 Nginx                          │
        HTTPS              │  K8s ：Ingress Controller                   │
                          │  纪律：SSE 不缓冲、read-timeout 3600s        │
                          └───────┬───────────────────────┬────────────┘
                                  │ /                     │ /api  /docs
                                  ▼                       ▼
                     ┌─────────────────┐     ┌──────────────────────────┐
                     │  前端静态资源     │     │  knowsource-backend       │
                     │  front/dist      │     │  纯后端镜像（单副本！）      │
                     │  Nginx 托管      │     │  FastAPI + uvicorn        │
                     │  （纯静态，随便放）│     │  探针 /api/healthz        │
                     └─────────────────┘     └────┬──┬──┬──┬─────────────┘
                                                  │  │  │  │
              ┌───────────────────┬───────────────┘  │  │  └──────────┐
              ▼                   ▼                  ▼  ▼             ▼
        ┌──────────┐      ┌──────────┐      ┌──────────────┐   ┌──────────────┐
        │PostgreSQL│      │  Neo4j   │      │Milvus        │   │ vLLM 模型服务  │
        │ 主数据    │      │  图数据库 │      │ +etcd+MinIO  │   │ GPU 独立机器   │
        │ 生产必选  │      │          │      │ 向量库        │   │ OpenAI 兼容API│
        └──────────┘      └──────────┘      └──────────────┘   └──────────────┘
              ▲                   持久卷（PVC / named volume）：数据/模型缓存/上传原件
              └── LLM Key 不进镜像：页面「配置中心」维护，存数据库
```

**镜像选型**：生产主线用**纯后端镜像**（`backend/Dockerfile`，API-only，前端独立托管）。
根目录 `Dockerfile`（前端 dist 打进镜像的全家桶）仅作省事的备选。

| 组件 | 单机形态（中小规模） | K8s 形态（规模化） |
|---|---|---|
| 前端 | 宿主机 Nginx 托管 `front/dist` | `knowsource-frontend`（`front/Dockerfile` + `03-frontend.yaml`，可多副本） |
| 后端 | `docker run` 单容器 | 本目录清单（Deployment 单副本 + Service + Ingress） |
| 中间件 | `docker compose up -d` | 云托管或独立 StatefulSet（不在本清单范围） |
| 模型服务 | vLLM 独立 GPU 机器 | 同左（独立节点池） |

## 二、文件清单

| 文件 | 内容 |
|---|---|
| `00-namespace.yaml` | 命名空间 |
| `01-config.yaml` | ConfigMap（非敏感配置）+ Secret 模板（连接串/密钥，apply 前替换 `<CHANGE_ME>` 占位符） |
| `02-app.yaml` | PVC + Deployment + Service + Ingress（SSE 注解；按路径拆分：`/` → 前端，`/api` `/docs` `/openapi.json` → 后端） |
| `03-frontend.yaml` | 前端静态站点 Deployment + Service（nginx 托管 dist，可多副本） |
| `build-backend.sh` | **后端一键流程**：构建 → 推仓库 → 应用 config/app → 滚动更新 → 就绪验证（含 Secret 占位符预检） |
| `build-frontend.sh` | **前端一键流程**：构建 → 推仓库 → 应用前端清单 → 滚动更新（与后端零耦合，可独立发布） |

> 单机 docker 形态的完整操作（构建、compose、run、Nginx 配置、坑速查）见
> `doc/部署/前后端部署指南.md`，本文档只给最终形态与 K8s 步骤。

## 三、镜像构建（两种形态通用）

```bash
# 后端（上下文 = backend/）
cd backend
docker build -t <registry>/knowsource-backend:v1.0.0 .
docker push <registry>/knowsource-backend:v1.0.0

# 前端（上下文 = front/；Vite 构建 + Nginx 托管，即 03-frontend.yaml 所用镜像）
cd ../front
docker build -t <registry>/knowsource-frontend:v1.0.0 .
docker push <registry>/knowsource-frontend:v1.0.0
```

- `backend/.dockerignore` 已剔除 `data/`、`uploads/`、`logs/`、`.env` 等运行时数据；
  **`sql/` 必须进镜像**（`init_db` 启动建表 + 增量迁移依赖 `schema.sql`/`migrations.sql`）
- 版本纪律：用明确版本号，不用 `latest` 上生产，方便回滚

## 四、形态 A：单机 docker（最终运行命令）

```bash
# 1. 中间件（仓库根目录）
docker compose up -d && docker compose ps        # 等全部 healthy

# 2. 后端（backend/ 目录下，那里有 .env）
docker run -d --name knowsource-backend \
  --network ontology_default \
  -p 8000:8000 \
  --env-file .env \
  -e DATABASE_URL="postgresql+asyncpg://ontology:ontology123@ontology-postgres:5432/knowsource" \
  -e MILVUS_HOST=ontology-milvus \
  -e NEO4J_URI="bolt://ontology-neo4j:7687" \
  -e HF_ENDPOINT=https://hf-mirror.com \
  -v knowsource-data:/srv/backend/data \
  -v knowsource-uploads:/srv/backend/uploads \
  <registry>/knowsource-backend:v1.0.0

# 3. 前端：front/dist 交给宿主机 Nginx 托管 + /api 反代（配置见部署指南 §6.2）
# 4. 验证
curl http://127.0.0.1:8000/api/healthz           # 进程探针
curl http://127.0.0.1:8000/api/agent/multi/tools # 工具链自检（内置 + MCP 状态）
```

发布更新 = 推新版本镜像 → `docker rm -f knowsource-backend` → 原命令重跑（秒级中断，单机形态可接受）。

## 五、形态 B：K8s（完整流程：构建 → 推送 → 部署 → 验证）

一键脚本（前后端各自独立文件，互不依赖）：

```bash
export REGISTRY=registry.cn-hangzhou.aliyuncs.com/your-ns
export VERSION=v1.0.0

bash deploy/k8s/build-backend.sh     # 后端（改后端代码后的日常发布）
bash deploy/k8s/build-frontend.sh    # 前端（改页面后的日常发布）
```

手工分解（与脚本逻辑一致）：

```bash
# 1. 填 01-config.yaml 的 Secret 占位符（数据库/Neo4j/LangSmith）
#    生产建议接 sealed-secrets / external-secrets，不用明文 Secret 入库
# 2. 改 02-app.yaml 的 Ingress host 为实际域名
# 3. 构建 + 推送双镜像（命令见第三节）
# 4. 按序应用
kubectl apply -f deploy/k8s/00-namespace.yaml
kubectl apply -f deploy/k8s/01-config.yaml
kubectl apply -f deploy/k8s/02-app.yaml
kubectl apply -f deploy/k8s/03-frontend.yaml
kubectl -n knowsource set image deploy/knowsource knowsource=$REGISTRY/knowsource-backend:$VERSION
kubectl -n knowsource set image deploy/knowsource-frontend frontend=$REGISTRY/knowsource-frontend:$VERSION
# 5. 验证
kubectl -n knowsource rollout status deploy/knowsource
kubectl -n knowsource port-forward deploy/knowsource 8000:8000
curl http://127.0.0.1:8000/api/healthz
```

Ingress 已按前后端拆好路径（`/` → 前端站点，`/api` `/docs` `/openapi.json` → 后端），
同域访问无需 CORS。仅部署过后端、从未部署前端时，`/` 路径 503 属预期。
改用全家桶镜像时：把 `/` 段改指 `knowsource` Service 并跳过 03。

后续演进：build.sh 的逻辑原样搬进 CI（GitLab CI / Jenkins / GitHub Actions）自动触发；
GitOps（ArgoCD）接手 apply，滚动更新/回滚全自动。

## 六、设计约束（为什么这么部署）

- **单副本约束**：进程内嵌 APScheduler（历史计划恢复型定时引擎）与本地嵌入模型，
  多副本会重复调度任务并双倍占用内存（`02-app.yaml` 已固定 `replicas: 1` + `Recreate`）。
  横向扩容前必须先把调度引擎拆出独立部署（或引入分布式锁），届时再启用 HPA。
- **探针三层分离**：`startupProbe`（5 分钟启动窗口，覆盖建表 + 嵌入模型加载）→
  `readinessProbe` / `livenessProbe` 均打 `/api/healthz`（只探测进程存活）。
  组件级健康诊断走 `/api/monitor/*`——探针不依赖外部组件，避免中间件抖动误杀 Pod。
- **SSE 长连接**：多智能体/工作流流式输出。Ingress 已关 `proxy-buffering`、
  `proxy-read-timeout: 3600`；自建 Nginx 同步这三条纪律（关缓冲、拉超时、多实例加粘性会话）。
- **持久化**：`uploads/`（文件原件）与 `data/`（HF 模型缓存、图库）挂同一 PVC 的不同
  subPath（K8s）或命名卷（单机）。容器可随意重建，数据在卷里。
- **数据库必用 PostgreSQL**：开发期 SQLite 仅限本地，生产走 `DATABASE_URL` 切 PG。
- **LLM 配置不走环境变量**：Provider/Key 在页面「配置中心」维护、持久化于数据库
  （`services/config_service.py`），镜像无状态无密钥。
- **可观测**：内置 OTel 链路追踪（`/trace` 页面）+ `/api/monitor/*` 组件诊断，
  保持 `OTEL_ENABLED=true`，无需额外接 APM。
