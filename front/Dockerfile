# ══════════════════════════════════════════════════════════════
# KnowSource 前端静态站点镜像（Vite 构建产物 + Nginx 托管）
#
# 构建（在 front/ 目录执行）：
#   docker build -t knowsource-frontend:v1.0.0 .
# K8s 清单：deploy/k8s/03-frontend.yaml
# Ingress 把 / 指向本镜像、/api /docs /openapi.json 指向后端（02-app.yaml）
# ══════════════════════════════════════════════════════════════

# ── Stage 1：前端构建（与根目录全家桶 Dockerfile 的 Stage 1 配方一致） ──
FROM node:20-alpine AS build
WORKDIR /build
COPY package.json package-lock.json ./
# 锁定依赖树保证可复现；npmmirror 加速国内构建
RUN npm ci --registry=https://registry.npmmirror.com
COPY . .
RUN npm run build          # 产物：/build/dist

# ── Stage 2：Nginx 托管（unprivileged 版，非 root 监听 8080，适配 K8s restricted） ──
FROM nginxinc/nginx-unprivileged:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html
EXPOSE 8080
