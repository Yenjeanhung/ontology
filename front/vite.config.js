import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import http from 'node:http'

// SSE（/api/notifications/stream）是长连接：
// 默认 http-proxy 每条请求都新建 TCP，断开后堆积 TIME_WAIT，
// 耗尽 Windows 动态端口（49152-65535）后报 connect EADDRINUSE。
// 这里用 keep-alive agent 复用连接，并关闭代理层超时。
const keepAliveAgent = new http.Agent({
  keepAlive: true,
  keepAliveMsecs: 30000,
  maxSockets: 32,
  maxFreeSockets: 8,
})

const proxyOptions = {
  target: 'http://127.0.0.1:8000',
  changeOrigin: true,
  agent: keepAliveAgent,
  // SSE：禁用代理层读写超时，避免长连接被中途掐断触发前端疯狂重连
  timeout: 0,
  proxyTimeout: 0,
}

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    proxy: {
      '/api': proxyOptions,
      '/docs': proxyOptions,
      '/openapi.json': proxyOptions,
    },
  },
})
