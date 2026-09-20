// 接口链路追踪 API 封装（OpenTelemetry span 自托管查询，后端见 core/otel.py）
const API = import.meta.env.DEV
  ? ''  // dev mode uses Vite proxy
  : 'http://localhost:8000'

// 接口聚合榜（慢接口定位）：按 p95 倒序
export async function fetchTraceStats(hours = 24) {
  const res = await fetch(`${API}/api/monitor/traces/stats?hours=${hours}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return await res.json()
}

// 调用列表（SERVER span 倒序），支持接口过滤 / 只看慢调用
export async function fetchTraceCalls({ hours = 24, endpoint = '', limit = 100, slowOnly = false } = {}) {
  const params = new URLSearchParams({
    hours: String(hours),
    limit: String(limit),
  })
  if (endpoint) params.set('endpoint', endpoint)
  if (slowOnly) params.set('slow_only', 'true')
  const res = await fetch(`${API}/api/monitor/traces?${params.toString()}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return await res.json()
}

// 单次调用完整 span 树（瀑布图数据）
export async function fetchTraceDetail(traceId) {
  const res = await fetch(`${API}/api/monitor/traces/${encodeURIComponent(traceId)}`)
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || `HTTP ${res.status}`)
  }
  return await res.json()
}
