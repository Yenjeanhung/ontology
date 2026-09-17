# KnowSource K8s 部署指南

应用容器化（多阶段构建：前端 dist + FastAPI 后端）+ K8s 编排清单。中间件
（PostgreSQL / Milvus / Neo4j）沿用仓库根目录 `docker-compose.yml` 的拓扑，
生产建议使用云托管服务或独立 StatefulSet（不在本清单范围内）。

## 文件清单

| 文件 | 内容 |
|---|---|
| `00-namespace.yaml` | 命名空间 |
| `01-config.yaml` | ConfigMap（非敏感配置）+ Secret 模板（连接串/密钥，apply 前替换占位符） |
| `02-app.yaml` | PVC + Deployment + Service + Ingress（含 SSE 长连接注解） |

## 部署步骤

```bash
# 1. 构建并推送镜像（仓库根目录）
docker build -t <registry>/knowsource:v1.0.0 .
docker push <registry>/knowsource:v1.0.0
# 记得同步修改 02-app.yaml 中的 image 地址

# 2. 填写 Secret 占位符（数据库/Neo4j/LangSmith）
vim deploy/k8s/01-config.yaml

# 3. 按序应用
kubectl apply -f deploy/k8s/00-namespace.yaml
kubectl apply -f deploy/k8s/01-config.yaml
kubectl apply -f deploy/k8s/02-app.yaml

# 4. 验证
kubectl -n knowsource rollout status deploy/knowsource
kubectl -n knowsource port-forward deploy/knowsource 8000:8000
curl http://127.0.0.1:8000/api/healthz          # 探针端点
curl http://127.0.0.1:8000/api/agent/multi/tools # 工具链自检（内置 + MCP 状态）
```

## 设计说明

- **探针三层分离**：`startupProbe`（5 分钟启动窗口，覆盖建表 + 嵌入模型加载）→
  `readinessProbe` / `livenessProbe` 均打 `/api/healthz`（只探测进程存活）。
  组件级健康诊断走 `/api/monitor/*`——探针不依赖外部组件，避免中间件抖动误杀 Pod。
- **单副本约束**：进程内嵌 APScheduler（历史计划恢复型定时引擎）与本地嵌入模型，
  多副本会重复调度任务并双倍占用内存。横向扩容需先将调度引擎拆出为独立部署
  （或引入分布式锁），届时再启用 HPA（`kubectl scale` 同理）。
- **SSE 友好**：Ingress 关闭 `proxy-buffering`、拉长 `proxy-read-timeout`，
  保证多智能体/工作流的流式输出不被缓冲截断。
- **运行时可写目录**：`uploads/`（文件原件与切分产物）与 `data/`（HF 模型缓存等）
  挂载同一 PVC 的不同 subPath。
- **LLM 配置不走环境变量**：模型 Provider/Key 在页面「配置中心」维护、持久化于
  数据库（`services/config_service.py`），镜像保持无状态无密钥。

## 本地 Docker 单容器运行

```bash
docker compose up -d postgres neo4j milvus etcd minio   # 中间件
docker build -t knowsource:latest .
docker run -p 8000:8000 --env-file backend/.env \
  -v ${PWD}/backend/uploads:/srv/backend/uploads knowsource:latest
```
