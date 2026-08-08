import { ref } from 'vue'

// 全局轻量 Toast：模块级单例 store，任意组件调用 useToast() 共享同一队列。
const toasts = ref([])
let _seq = 0

function dismiss(id) {
  const i = toasts.value.findIndex(t => t.id === id)
  if (i !== -1) toasts.value.splice(i, 1)
}

function show(type, text, duration = 3200) {
  const id = ++_seq
  toasts.value.push({ id, type, text })
  if (duration > 0) {
    setTimeout(() => dismiss(id), duration)
  }
  return id
}

export function useToast() {
  return {
    toasts,
    success: (text, d) => show('success', text, d),
    error: (text, d) => show('error', text, d ?? 5000),
    info: (text, d) => show('info', text, d),
    dismiss,
  }
}
