import { ref } from 'vue'
import { fetchNotificationSummary } from '../api'

// 全局通知计数（侧栏红点 + 顶栏小喇叭）。跨组件共享：审核/拒绝/删除等
// 变更后由对应页面主动刷新，App.vue 负责首屏载入与定时轮询。
export const notifications = ref({
  suggestions: 0,
  files_processing: 0,
  files_failed: 0,
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
