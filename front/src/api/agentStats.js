/** Agent 调用量统计接口（后端 routers/monitor.py 的 /api/monitor/agent-research/*）。 */
import { request } from './auth'

/** 概览：窗口总量 / 今日量 / 成功率 / 平均耗时 / 活跃智能体与用户数 */
export function fetchAgentResearchOverview(days = 7) {
  return request('/api/monitor/agent-research/overview', { params: { days } })
}

/** 按维度聚合：group_by = date|agent|scene|user|model|success */
export function fetchAgentResearchStats(params = {}) {
  return request('/api/monitor/agent-research/stats', { params })
}

/** 调用明细（倒序分页） */
export function fetchAgentResearchLogs(params = {}) {
  return request('/api/monitor/agent-research/logs', { params })
}

/** 筛选下拉：窗口内出现过的智能体 */
export function fetchAgentResearchAgents(days = 30) {
  return request('/api/monitor/agent-research/agents', { params: { days } })
}

/** 手动清理：删除 days 天之前的明细 */
export function cleanupAgentResearch(days = 30) {
  return request(`/api/monitor/agent-research/cleanup?days=${days}`, {
    method: 'POST',
    body: {},
  })
}
