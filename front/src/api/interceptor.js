/**
 * 全局请求拦截器（在 main.js 中最先引入，只安装一次）。
 *
 * 背景：front/src/api/index.js 是历史累积的巨型文件（上百个裸 fetch 调用），
 * 逐个改造成本高风险大。这里统一在 fetch / EventSource 层注入登录凭证，
 * 让存量代码零改动即可适配鉴权；新代码建议直接用 `api/auth.js` 的 request()。
 *
 * 行为：
 *  - 为指向本站 /api 的请求自动带上 `Authorization: Bearer <token>`；
 *  - 捕获 401：清空本地令牌并派发 `ks-auth-expired` 事件（由路由跳登录页）；
 *  - EventSource 不支持自定义请求头，改为在 URL 上追加 `?token=`。
 */
import { clearTokens, getToken } from './auth'

const AUTH_EXPIRED_EVENT = 'ks-auth-expired'

function isSameApi(url) {
  if (typeof url !== 'string') return false
  if (url.startsWith('/api/')) return true
  try {
    // 只判断路径：生产构建里 API 走绝对地址 http://localhost:8000，
    // 与页面可能不同源（如用别的端口打开前端），因此不能校验 origin。
    return new URL(url, window.location.origin).pathname.startsWith('/api/')
  } catch {
    return false
  }
}

function appendToken(url) {
  const token = getToken()
  if (!token || !isSameApi(url)) return url
  try {
    const parsed = new URL(url, window.location.origin)
    if (!parsed.searchParams.has('token')) parsed.searchParams.set('token', token)
    return parsed.toString()
  } catch {
    return url
  }
}

let installed = false

export function installInterceptors() {
  if (installed) return
  installed = true

  const originalFetch = window.fetch.bind(window)

  window.fetch = async function patchedFetch(input, init) {
    const url = typeof input === 'string' ? input : input?.url
    if (isSameApi(url)) {
      const token = getToken()
      if (token) {
        const headers = new Headers(init?.headers || (typeof input !== 'string' ? input.headers : undefined))
        if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)
        init = { ...(init || {}), headers }
      }
    }

    const res = await originalFetch(input, init)

    if (res && res.status === 401 && isSameApi(url)) {
      const isAuthProbe = String(url).includes('/api/auth/')
      if (!isAuthProbe) {
        clearTokens()
        window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, {
          detail: { code: 'TOKEN_INVALID', message: '登录已失效，请重新登录' },
        }))
      }
    }
    return res
  }

  if (typeof window.EventSource === 'function') {
    const Original = window.EventSource
    function PatchedEventSource(url, config) {
      return new Original(appendToken(url), config)
    }
    PatchedEventSource.prototype = Original.prototype
    PatchedEventSource.CONNECTING = Original.CONNECTING
    PatchedEventSource.OPEN = Original.OPEN
    PatchedEventSource.CLOSED = Original.CLOSED
    window.EventSource = PatchedEventSource
  }
}
