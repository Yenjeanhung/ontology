// 系统监控 API 封装
const API = import.meta.env.DEV
  ? ''  // dev mode uses Vite proxy
  : 'http://localhost:8000'

// 首次加载快照（组件全景 + 状态摘要 + 系统信息）
export async function fetchMonitorOverview() {
  const res = await fetch(`${API}/api/monitor/overview`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return await res.json()
}

// 手动触发一次全量健康检查（结果由服务端广播到 SSE；此接口直接返回最新快照）
export async function triggerMonitorCheck(key) {
  const url = key
    ? `${API}/api/monitor/check?key=${encodeURIComponent(key)}`
    : `${API}/api/monitor/check`
  const res = await fetch(url, { method: 'POST' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return await res.json()
}

// 建立 SSE 长连接：服务端定时推送 snapshot / heartbeat
// 返回 { close } 用于关闭连接
export function connectMonitorStream({ onSnapshot, onHeartbeat, onError }) {
  const es = new EventSource(`${API}/api/monitor/stream`)

  es.addEventListener('snapshot', (e) => {
    try {
      const data = JSON.parse(e.data)
      onSnapshot?.(data)
    } catch { /* skip malformed */ }
  })

  es.addEventListener('heartbeat', (e) => {
    try {
      const data = JSON.parse(e.data)
      onHeartbeat?.(data)
    } catch { /* skip malformed */ }
  })

  es.onerror = (e) => {
    // EventSource 会自动重连；仅通知上层
    onError?.(e)
  }

  return {
    close() { es.close() },
    get readyState() { return es.readyState },
  }
}

// 向量数据库手动测试：列出全部 collection
export async function fetchVectorStoreSchemas() {
  const res = await fetch(`${API}/api/monitor/vector_store/schemas`, { method: 'POST' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return await res.json()
}

// 向量数据库手动测试：执行语义检索
export async function runVectorStoreQuery(payload) {
  const res = await fetch(`${API}/api/monitor/vector_store/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

// 关系数据库手动测试：列出全部 schema（库/表结构）
export async function fetchDatabaseSchemas() {
  const res = await fetch(`${API}/api/monitor/database/schemas`, { method: 'POST' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return await res.json()
}

// 关系数据库手动测试：在选定 schema 中执行只读 SQL
export async function runDatabaseQuery(sql) {
  const res = await fetch(`${API}/api/monitor/database/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}

// LLM 流式调用：SSE 输出 thinking / reasoning / content / done
export async function streamMonitorLlm(prompt, { onReasoning, onContent, onDone, onError } = {}) {
  const res = await fetch(`${API}/api/monitor/llm/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || `HTTP ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const data = JSON.parse(line.slice(6))
        const t = data.event
        if (t === 'reasoning') onReasoning?.(data.content || '')
        else if (t === 'content') onContent?.(data.content || '')
        else if (t === 'done') onDone?.()
      } catch { /* skip malformed */ }
    }
  }
}
