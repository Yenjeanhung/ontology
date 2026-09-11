<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '../api/auth'
import { applyLogin, auth, loadAuthStatus } from '../stores/auth'

const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const remember = ref(true)
const loading = ref(false)
const errorMsg = ref('')

async function submit() {
  if (loading.value) return
  if (!username.value.trim() || !password.value) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await authApi.login(username.value.trim(), password.value)
    applyLogin(res)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.replace(redirect)
  } catch (err) {
    errorMsg.value = err.message || '登录失败'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  const reason = sessionStorage.getItem('ks.login.reason')
  if (reason) {
    errorMsg.value = reason
    sessionStorage.removeItem('ks.login.reason')
  }
  if (!auth.statusLoaded) await loadAuthStatus()
  if (!auth.enabled) {
    router.replace('/')
  }
})
</script>

<template>
  <div class="login-shell">
    <div class="login-card">
      <div class="login-brand">
        <span class="brand-mark">K</span>
        <div class="brand-text">
          <div class="brand-title">KnowSource</div>
          <div class="brand-sub">本体知识平台</div>
        </div>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label class="field">
          <span class="field-label">用户名</span>
          <input
            v-model="username"
            class="field-input"
            type="text"
            autocomplete="username"
            placeholder="请输入用户名"
            autofocus
          />
        </label>
        <label class="field">
          <span class="field-label">密码</span>
          <input
            v-model="password"
            class="field-input"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
            @keyup.enter="submit"
          />
        </label>

        <p v-if="errorMsg" class="login-error">{{ errorMsg }}</p>

        <button class="login-btn" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>

      <p class="login-tip">首次登录请使用初始管理员账号，登录后请立即修改密码</p>
    </div>
  </div>
</template>

<style scoped>
.login-shell {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(circle at 30% 20%, rgba(45, 212, 191, 0.10), transparent 55%),
    var(--c-bg);
  padding: 24px;
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 14px;
  padding: 32px 30px 24px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.18);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}

.brand-mark {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  background: var(--c-accent);
  color: #fff;
  font-weight: 700;
  font-size: 20px;
}

.brand-title {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0.3px;
}

.brand-sub {
  font-size: 12px;
  color: var(--c-secondary);
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12px;
  color: var(--c-secondary);
  font-weight: 600;
}

.field-input {
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-bg);
  color: var(--c-fg);
  font-size: 14px;
  font-family: var(--font);
  outline: none;
}

.field-input:focus {
  border-color: var(--c-accent);
  box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.12);
}

.login-error {
  font-size: 12.5px;
  color: var(--c-danger);
  background: color-mix(in srgb, var(--c-danger) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--c-danger) 30%, transparent);
  border-radius: var(--radius-sm);
  padding: 7px 10px;
}

.login-btn {
  height: 40px;
  margin-top: 4px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--c-btn-primary-bg, var(--c-accent));
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 4px;
  cursor: pointer;
  font-family: var(--font);
}

.login-btn:hover:not(:disabled) {
  background: var(--c-btn-primary-bg-hover, var(--c-accent));
  filter: brightness(1.08);
}

.login-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.login-tip {
  margin-top: 18px;
  font-size: 11.5px;
  color: var(--c-secondary);
  text-align: center;
  line-height: 1.6;
}
</style>
