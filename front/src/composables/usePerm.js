import { hasAnyPerm, hasPerm } from '../stores/auth'

/**
 * 按钮级权限判断：
 *   const { can } = usePerm()
 *   v-if="can('kb:create')"
 */
export function usePerm() {
  return {
    can: hasPerm,
    canAny: hasAnyPerm,
  }
}

export { hasPerm, hasAnyPerm }
