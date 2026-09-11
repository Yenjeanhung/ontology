import { computed, reactive } from 'vue'
import { authApi, clearTokens, isLoggedIn, setTokens } from '../api/auth'

/** 全局登录态：是否被后端启用、当前用户、角色与权限码 */
export const auth = reactive({
  statusLoaded: false,
  enabled: true,        // 后端 auth_enabled
  loaded: false,        // 是否已拉取过 /auth/me
  user: null,
  roles: [],
  permissions: [],
  isSuperAdmin: false,
})

export const isAuthed = computed(() => !auth.enabled || (isLoggedIn() && !!auth.user))

export async function loadAuthStatus() {
  try {
    const res = await authApi.status()
    auth.enabled = !!res?.auth_enabled
  } catch {
    // 后端不可达时按"需要登录"处理，避免把未鉴权页面暴露出去
    auth.enabled = true
  }
  auth.statusLoaded = true
  return auth.enabled
}

export async function loadMe() {
  if (!auth.enabled) {
    auth.loaded = true
    return null
  }
  if (!isLoggedIn()) {
    auth.user = null
    auth.loaded = true
    return null
  }
  const res = await authApi.me()
  auth.user = res.user
  auth.roles = res.roles || []
  auth.permissions = res.permissions || []
  auth.isSuperAdmin = !!res.is_super_admin
  auth.loaded = true
  return res
}

export function applyLogin(res) {
  setTokens(res.access_token, res.refresh_token)
  auth.user = res.user
  auth.roles = res.roles || []
  auth.permissions = res.permissions || []
  auth.isSuperAdmin = !!res.is_super_admin
  auth.enabled = true
  auth.loaded = true
}

export function clearAuth() {
  clearTokens()
  auth.user = null
  auth.roles = []
  auth.permissions = []
  auth.isSuperAdmin = false
  auth.loaded = false
}

export function hasPerm(code) {
  if (!code) return true
  if (auth.isSuperAdmin) return true
  if (!auth.enabled) return true
  return auth.permissions.includes(code)
}

export function hasAnyPerm(codes) {
  if (!codes || !codes.length) return true
  return codes.some(hasPerm)
}
