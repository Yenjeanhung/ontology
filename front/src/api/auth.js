const API = import.meta.env.DEV
  ? ''  // dev mode uses Vite proxy
  : 'http://localhost:8000'

export { API }

const TOKEN_KEY = 'knowsource.access_token'
const REFRESH_KEY = 'knowsource.refresh_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY) || ''
}

export function setTokens(access, refresh) {
  if (access) localStorage.setItem(TOKEN_KEY, access)
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearTokens() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export function isLoggedIn() {
  return !!getToken()
}

/** 给 SSE（EventSource）URL 追加 token：EventSource 无法自定义请求头 */
export function withToken(url) {
  const token = getToken()
  if (!token) return url
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token)
}

function normalizeDetail(body, status, fallback) {
  const d = body?.detail
  if (typeof d === 'string' && d) return d
  if (Array.isArray(d)) {
    return d.map((it) => (typeof it === 'string' ? it : it?.msg)).filter(Boolean).join('；')
  }
  if (d && typeof d === 'object') {
    const msg = d.message || d.msg || d.error
    if (typeof msg === 'string' && msg) return msg
  }
  return fallback || `请求失败（${status}）`
}

/**
 * 统一请求封装：自动注入 token、解析错误、处理登录态失效。
 * 抛出 Error，并附带 err.status / err.code。
 */
export async function request(path, { method = 'GET', body, params, skipAuth = false } = {}) {
  let url = `${API}${path}`
  if (params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v !== null && v !== undefined)
    ).toString()
    if (qs) url += (url.includes('?') ? '&' : '?') + qs
  }

  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (res.status === 204) return null

  const text = await res.text()
  let data = null
  if (text) {
    try { data = JSON.parse(text) } catch { data = null }
  }

  if (!res.ok) {
    const code = data?.code || (res.status === 401 ? 'TOKEN_INVALID' : res.status === 403 ? 'FORBIDDEN' : '')
    if (res.status === 401 && !skipAuth) {
      clearTokens()
      window.dispatchEvent(new CustomEvent('ks-auth-expired', { detail: { code, message: normalizeDetail(data, res.status, '登录已失效，请重新登录') } }))
    }
    const err = new Error(normalizeDetail(data, res.status))
    err.status = res.status
    err.code = code
    throw err
  }
  return data
}

const get = (path, params) => request(path, { method: 'GET', params })
const post = (path, body) => request(path, { method: 'POST', body: body ?? {} })
const put = (path, body) => request(path, { method: 'PUT', body: body ?? {} })
const patch = (path, body) => request(path, { method: 'PATCH', body: body ?? {} })
const del = (path) => request(path, { method: 'DELETE' })

/* ── 认证 ── */
export const authApi = {
  status: () => get('/api/auth/status'),
  login: (username, password) => post('/api/auth/login', { username, password }),
  logout: () => post('/api/auth/logout'),
  me: () => get('/api/auth/me'),
  permissions: () => get('/api/auth/permissions'),
  changePassword: (old_password, new_password) =>
    post('/api/auth/change-password', { old_password, new_password }),
}

/* ── 用户 ── */
export const userApi = {
  list: (params) => get('/api/users', params),
  get: (id) => get(`/api/users/${id}`),
  create: (payload) => post('/api/users', payload),
  update: (id, payload) => patch(`/api/users/${id}`, payload),
  remove: (id) => del(`/api/users/${id}`),
  batchDelete: (ids) => post('/api/users/batch-delete', { ids }),
  setStatus: (id, status) => patch(`/api/users/${id}/status`, { status }),
  resetPassword: (id, newPassword) => post(`/api/users/${id}/reset-password`, newPassword ? { new_password: newPassword } : {}),
  setRoles: (id, roleIds) => post(`/api/users/${id}/roles`, { role_ids: roleIds }),
}

/* ── 用户组 ── */
export const groupApi = {
  list: () => get('/api/user-groups'),
  get: (id) => get(`/api/user-groups/${id}`),
  create: (payload) => post('/api/user-groups', payload),
  update: (id, payload) => put(`/api/user-groups/${id}`, payload),
  remove: (id) => del(`/api/user-groups/${id}`),
}

/* ── 角色 / 权限 ── */
export const roleApi = {
  list: () => get('/api/roles'),
  get: (id) => get(`/api/roles/${id}`),
  create: (payload) => post('/api/roles', payload),
  update: (id, payload) => put(`/api/roles/${id}`, payload),
  remove: (id) => del(`/api/roles/${id}`),
  users: (id) => get(`/api/roles/${id}/users`),
}

export const permApi = {
  tree: () => get('/api/permissions'),
  sync: () => post('/api/permissions/sync'),
}

/* ── 会话（上下线）── */
export const sessionApi = {
  list: (params) => get('/api/sessions', params),
  stats: () => get('/api/sessions/stats'),
  kick: (sid) => del(`/api/sessions/${sid}`),
  kickUser: (userId) => post(`/api/sessions/user/${userId}/kick-all`),
  kickAll: () => post('/api/sessions/kick-all'),
}

/* ── 日志 ── */
export const auditApi = {
  logs: (params) => get('/api/audit/logs', params),
  log: (id) => get(`/api/audit/logs/${id}`),
  authLogs: (params) => get('/api/audit/auth-logs', params),
  actions: () => get('/api/audit/actions'),
  archive: () => post('/api/audit/archive'),
  async exportCsv(params) {
    const qs = new URLSearchParams(
      Object.entries(params || {}).filter(([, v]) => v !== '' && v !== null && v !== undefined)
    ).toString()
    const res = await fetch(`${API}/api/audit/logs/export${qs ? '?' + qs : ''}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
    if (!res.ok) throw new Error('导出失败')
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'audit-logs.csv'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },
}

/* ── 安全策略 ── */
export const settingsApi = {
  get: () => get('/api/security-settings'),
  update: (values) => put('/api/security-settings', { values }),
}
