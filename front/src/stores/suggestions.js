import { ref } from 'vue'
import { fetchOntologySuggestions } from '../api'

// 全局待审核本体建议数（侧边栏徽标）。跨组件共享：审核/拒绝/删除后由
// SuggestionListPage 主动刷新，App.vue 仅负责首屏载入与展示。
export const pendingSuggestionCount = ref(0)

export async function refreshPendingSuggestionCount() {
  try {
    const list = await fetchOntologySuggestions({ status: 'ready' })
    pendingSuggestionCount.value = Array.isArray(list) ? list.length : 0
  } catch {
    // 拉取失败时保留上一次的值
  }
}
