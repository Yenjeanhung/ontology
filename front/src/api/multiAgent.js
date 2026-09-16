/**
 * 多智能体通用 API（业务无关，面向场景注册表编程）。
 *
 * 后端契约见 doc/智能体/多智能体场景.md：
 * - GET  /api/agent/multi/scenarios                          已注册场景列表
 * - GET  /api/agent/multi/scenarios/{sid}/targets            场景目标列表（通用目标卡）
 * - POST /api/agent/multi/scenarios/{sid}/targets/{tid}/run  对目标发起研判（SSE）
 * - POST /api/agent/multi/scenarios/{sid}/run                自由任务研判（SSE，adhoc 场景）
 * - GET/POST /api/agent/multi/tasks · PUT/DELETE /tasks/{tid} 任务库（可配置任务提示词模板）
 *
 * 鉴权：interceptor.js 已全局拦截 fetch 并注入 Bearer token，这里保持裸 fetch。
 */
import { API, apiDetail } from './index'

export async function listMultiScenarios() {
  const res = await fetch(`${API}/api/agent/multi/scenarios`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '加载场景列表失败'))
  }
  return res.json()
}

export async function listScenarioTargets(scenarioId) {
  const res = await fetch(`${API}/api/agent/multi/scenarios/${encodeURIComponent(scenarioId)}/targets`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '加载目标列表失败'))
  }
  return res.json()
}

/**
 * 订阅多智能体研判 SSE 流——目标卡模式（事件契约与业务场景无关）。
 * @param {string} scenarioId
 * @param {string} targetId
 * @param {{ onEvent?: (evt: object) => void, signal?: AbortSignal }} handlers
 */
export async function streamScenarioRun(scenarioId, targetId, { onEvent, signal } = {}) {
  await _stream(`${API}/api/agent/multi/scenarios/${encodeURIComponent(scenarioId)}/targets/${encodeURIComponent(targetId)}/run`, {}, { onEvent, signal })
}

/**
 * 订阅多智能体 SSE 流——自由任务模式（adhoc 场景，自由编制）。
 * @param {string} scenarioId
 * @param {string} task 任务描述全文（任务类型不限：研判/写作/总结/问答…）
 * @param {string[]} agents 可选能力智能体 id 列表（空数组 = 后端默认编制）
 * @param {{ onEvent?: (evt: object) => void, signal?: AbortSignal }} handlers
 */
export async function streamTaskRun(scenarioId, task, agents = [], { onEvent, signal } = {}) {
  await _stream(
    `${API}/api/agent/multi/scenarios/${encodeURIComponent(scenarioId)}/run`,
    { task, agents },
    { onEvent, signal },
  )
}

// ─────────────────────── 任务库（可配置任务提示词模板） ───────────────────────

export async function listMultiTasks() {
  const res = await fetch(`${API}/api/agent/multi/tasks`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '加载任务库失败'))
  }
  return res.json()
}

export async function createMultiTask({ name, prompt, agents = [] }) {
  const res = await fetch(`${API}/api/agent/multi/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, prompt, agents }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '创建任务失败'))
  }
  return res.json()
}

export async function updateMultiTask(taskId, patch) {
  const res = await fetch(`${API}/api/agent/multi/tasks/${encodeURIComponent(taskId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '更新任务失败'))
  }
  return res.json()
}

export async function deleteMultiTask(taskId) {
  const res = await fetch(`${API}/api/agent/multi/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '删除任务失败'))
  }
  return res.json()
}

/** SSE 读取公共实现：解析 data: 行并逐事件回调。 */
async function _stream(url, payload, { onEvent, signal } = {}) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => null)
    throw new Error(apiDetail(body, '研判请求失败'))
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() || ''
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data:')) continue
      const data = trimmed.slice(5).trim()
      if (!data || data === '[DONE]') continue
      try {
        const evt = JSON.parse(data)
        if (onEvent) onEvent(evt)
      } catch {
        /* 忽略不完整行 */
      }
    }
  }
}
