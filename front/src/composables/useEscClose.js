import { onActivated, onDeactivated, onMounted, onUnmounted } from 'vue'

/**
 * 全局 ESC 关闭管理。
 *
 * 多个弹窗同时打开时，按注册顺序从后往前找到第一个“当前打开”的弹窗并关闭它，
 * 从而保证后打开的弹窗优先被关闭。组件被 KeepAlive 缓存（失活）时会自动从栈中移除，
 * 避免离开页面后仍然劫持 ESC。
 */
const stack = []
let bound = false

function onKeydown(e) {
  if (e.key !== 'Escape' && e.key !== 'Esc') return
  for (let i = stack.length - 1; i >= 0; i -= 1) {
    const entry = stack[i]
    if (entry && entry.isOpen()) {
      entry.close()
      // 捕获阶段拦截，避免弹窗关闭时再触发底层的菜单/下拉关闭逻辑
      e.stopPropagation()
      return
    }
  }
}

function bind() {
  if (bound || typeof window === 'undefined') return
  bound = true
  window.addEventListener('keydown', onKeydown, true)
}

/**
 * 为组件内的弹窗注册 ESC 关闭能力。
 *
 * @param {() => Array<[boolean, () => void]>} getPairs
 *   返回当前组件内所有弹窗的 [是否打开, 关闭函数] 列表；列表越靠后优先级越高。
 */
export function useEscClose(getPairs) {
  const entry = {
    isOpen: () => getPairs().some(([open]) => open),
    close: () => {
      const pairs = getPairs()
      for (let i = pairs.length - 1; i >= 0; i -= 1) {
        if (pairs[i][0]) {
          pairs[i][1]()
          return
        }
      }
    },
  }

  const add = () => {
    bind()
    if (!stack.includes(entry)) stack.push(entry)
  }
  const remove = () => {
    const index = stack.indexOf(entry)
    if (index !== -1) stack.splice(index, 1)
  }

  onMounted(add)
  onActivated(add)
  onDeactivated(remove)
  onUnmounted(remove)
}
