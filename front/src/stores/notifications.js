import { ref } from 'vue'
import { API, fetchNotificationSummary } from '../api'

// 全局通知计数（侧栏红点 + 顶栏小喇叭）。跨组件共享。
// 首屏用一次 HTTP 拉取，之后由 SSE 长连接推送变更 —— 不再做任何定时轮询。
export const notifications = ref({
  suggestions: 0,
  files_processing: 0,
  files_failed: 0,
  schedule_alerts: 0,
  human_tasks: 0,
  total: 0,
  items: [],
})

export async function refreshNotifications() {
  try {
    notifications.value = await fetchNotificationSummary()
  } catch {
    // 拉取失败时保留上一次的值
  }
}

// ───────────────────── SSE 推送（替代轮询）───────────────────────
// 整站只维持一条连接：服务端仅在计数变化时下发，无变化时只有心跳，零业务请求。

let es = null

export function startNotificationStream() {
  if (es || typeof EventSource === 'undefined') return

  es = new EventSource(`${API}/api/notifications/stream`)

  es.onmessage = (e) => {
    try {
      notifications.value = JSON.parse(e.data)
    } catch {
      // 忽略非 JSON 帧（如心跳注释行不会触发 onmessage，此处仅兜底）
    }
  }

  // 不主动 close：EventSource 自带指数退避重连。
  // 这里只做日志，避免断网瞬间的错误噪音。
  es.onerror = () => {}
}

export function stopNotificationStream() {
  if (es) {
    es.close()
    es = null
  }
}

// 页面重新可见时立即拉一次，避免后台期间的状态滞后（SSE 通常已推送，这里仅为兜底）
export function bindVisibilityRefresh() {
  const handler = () => {
    if (document.visibilityState === 'visible') refreshNotifications()
  }
  document.addEventListener('visibilitychange', handler)
  return () => document.removeEventListener('visibilitychange', handler)
}
