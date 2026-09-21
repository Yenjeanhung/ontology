// RAG 评测 API 封装（评测集 / 评测任务 / Badcase 标记回流）
const API = import.meta.env.DEV
  ? ''  // dev mode uses Vite proxy
  : 'http://localhost:8000'

import { apiDetail } from './index'

async function handle(res, fallback = '请求失败') {
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, fallback))
  }
  return res.json()
}

function jsonInit(method, payload) {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

// ── 评估模型选项 ──
export async function fetchEvalLlmOptions() {
  const res = await fetch(`${API}/api/eval/llm-options`)
  return handle(res, '加载模型配置失败')
}

// ── 评测集 ──
export async function fetchTestsets() {
  const res = await fetch(`${API}/api/eval/testsets`)
  return handle(res, '加载评测集失败')
}

export async function createTestset(payload) {
  return handle(await fetch(`${API}/api/eval/testsets`, jsonInit('POST', payload)))
}

export async function updateTestset(id, payload) {
  return handle(await fetch(`${API}/api/eval/testsets/${id}`, jsonInit('PUT', payload)))
}

export async function deleteTestset(id) {
  return handle(await fetch(`${API}/api/eval/testsets/${id}`, { method: 'DELETE' }), '删除失败')
}

export async function fetchTestsetItems(id, params = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))
  const res = await fetch(`${API}/api/eval/testsets/${id}/items?${qs}`)
  return handle(res, '加载条目失败')
}

export async function addTestsetItem(id, payload) {
  return handle(await fetch(`${API}/api/eval/testsets/${id}/items`, jsonInit('POST', payload)))
}

export async function updateTestsetItem(id, itemId, payload) {
  return handle(await fetch(`${API}/api/eval/testsets/${id}/items/${itemId}`, jsonInit('PUT', payload)))
}

export async function deleteTestsetItem(id, itemId) {
  return handle(await fetch(`${API}/api/eval/testsets/${id}/items/${itemId}`, { method: 'DELETE' }), '删除失败')
}

export async function importTestsetItems(id, file) {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${API}/api/eval/testsets/${id}/import`, { method: 'POST', body: fd })
  return handle(res, '导入失败')
}

export async function exportTestsetItems(id, format = 'jsonl') {
  const res = await fetch(`${API}/api/eval/testsets/${id}/export?format=${format}`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '导出失败'))
  }
  return await res.blob()
}

// ── 评测任务 ──
export async function fetchEvalRuns(params = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))
  const res = await fetch(`${API}/api/eval/runs?${qs}`)
  return handle(res, '加载评测任务失败')
}

export async function createEvalRun(payload) {
  return handle(await fetch(`${API}/api/eval/runs`, jsonInit('POST', payload)))
}

export async function fetchEvalRun(id) {
  return handle(await fetch(`${API}/api/eval/runs/${id}`), '加载任务失败')
}

// 订阅评测任务进度 SSE：进度/状态变化才推送，空闲零流量（token 由 interceptor 补到 URL 上）
export function connectRunStream({ onEvent, onError } = {}) {
  const es = new EventSource(`${API}/api/eval/runs/stream`)
  es.onmessage = (e) => {
    try { onEvent?.(JSON.parse(e.data)) } catch { /* skip malformed */ }
  }
  es.onerror = (e) => { onError?.(e) } // EventSource 自动重连
  return { close: () => es.close() }
}

export async function cancelEvalRun(id) {
  return handle(await fetch(`${API}/api/eval/runs/${id}/cancel`, { method: 'POST' }), '取消失败')
}

export async function deleteEvalRun(id) {
  return handle(await fetch(`${API}/api/eval/runs/${id}`, { method: 'DELETE' }), '删除失败')
}

export async function fetchEvalRunItems(id, params = {}) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))
  const res = await fetch(`${API}/api/eval/runs/${id}/items?${qs}`)
  return handle(res, '加载结果失败')
}

export async function fetchEvalRunItem(runId, itemId) {
  return handle(await fetch(`${API}/api/eval/runs/${runId}/items/${itemId}`), '加载详情失败')
}

export async function exportEvalReport(runId) {
  const res = await fetch(`${API}/api/eval/runs/${runId}/report`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '导出失败'))
  }
  return await res.blob()
}

// ── Badcase 标记与回流 ──
export async function markBadcase(runId, itemId, payload) {
  return handle(await fetch(`${API}/api/eval/runs/${runId}/items/${itemId}/badcase`, jsonInit('PUT', payload)))
}

export async function markBadcaseBatch(runId, runItemIds, payload) {
  return handle(await fetch(
    `${API}/api/eval/runs/${runId}/items/batch-badcase`,
    jsonInit('PUT', { run_item_ids: runItemIds, ...payload }),
  ))
}

export async function backflowBadcases(runId, payload) {
  return handle(await fetch(`${API}/api/eval/runs/${runId}/backflow`, jsonInit('POST', payload)))
}

// ── 下载辅助 ──
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
