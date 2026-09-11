<script setup>
import { computed, onMounted, ref } from 'vue'
import { permApi, roleApi, settingsApi } from '../../api/auth'
import { useEscClose } from '../../composables/useEscClose'
import { useToast } from '../../composables/useToast'
import ConfirmDialog from '../common/ConfirmDialog.vue'

const toast = useToast()

const tab = ref('roles')
const errorMsg = ref('')

/* ── 统一确认弹窗（替代原生 confirm） ── */
const confirmDlg = ref({ visible: false, title: '确认操作', message: '', confirmText: '确认', action: null })
function askConfirm({ title, message, confirmText = '确认', action }) {
  confirmDlg.value = { visible: true, title, message, confirmText, action }
}
async function runConfirm() {
  const act = confirmDlg.value.action
  confirmDlg.value.visible = false
  if (act) await act()
}
/* ESC 关闭：按弹窗层级从上到下（越靠后优先级越高） */
useEscClose(() => [
  [confirmDlg.value.visible, () => { confirmDlg.value.visible = false }],
  [creating.value, () => { creating.value = false }],
])

/* ── 角色 ── */
const roles = ref([])
const tree = ref([])
const current = ref(null)
const selected = ref([])
const dirty = ref(false)

async function load() {
  roles.value = await roleApi.list()
  tree.value = await permApi.tree()
}

async function openRole(role) {
  errorMsg.value = ''
  const detail = await roleApi.get(role.id)
  current.value = detail
  selected.value = [...(detail.permissions || [])]
  dirty.value = false
}

function togglePerm(code) {
  const i = selected.value.indexOf(code)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(code)
  dirty.value = true
}

function toggleModule(group, checked) {
  for (const p of group.permissions) {
    const has = selected.value.includes(p.code)
    if (checked && !has) selected.value.push(p.code)
    if (!checked && has) selected.value.splice(selected.value.indexOf(p.code), 1)
  }
  dirty.value = true
}

const moduleChecked = (group) => group.permissions.every((p) => selected.value.includes(p.code))

async function savePerms() {
  if (!current.value) return
  try {
    await roleApi.update(current.value.id, { permissions: selected.value })
    dirty.value = false
    await load()
    current.value = await roleApi.get(current.value.id)
  } catch (err) { errorMsg.value = err.message }
}

/* ── 新建角色 ── */
const creating = ref(false)
const form = ref({ code: '', name: '', description: '', permissions: [] })
const createError = ref('')

async function createRole() {
  createError.value = ''
  try {
    await roleApi.create(form.value)
    creating.value = false
    form.value = { code: '', name: '', description: '', permissions: [] }
    toast.success('角色已创建')
    await load()
  } catch (err) { createError.value = err.message }
}

function removeRole(role) {
  askConfirm({
    title: '删除角色',
    message: `确认删除角色 ${role.name}？`,
    confirmText: '删除',
    action: async () => {
      try {
        await roleApi.remove(role.id)
        if (current.value?.id === role.id) current.value = null
        toast.success('角色已删除')
        await load()
      } catch (err) { errorMsg.value = err.message }
    },
  })
}

/* ── 安全策略 ── */
const settings = ref({})
const settingsSchema = ref([])

async function loadSettings() {
  const res = await settingsApi.get()
  settings.value = res.values || {}
  settingsSchema.value = res.schema || []
}

async function saveSettings() {
  try {
    const res = await settingsApi.update(settings.value)
    settings.value = res.values || settings.value
    toast.success('安全策略已保存')
  } catch (err) { errorMsg.value = err.message }
}

const SETTING_LABELS = {
  auth_enabled: '启用登录鉴权（关闭后全站免登录）',
  password_min_length: '密码最小长度',
  password_complexity: '密码复杂度（none/low/medium/high）',
  password_expire_days: '密码有效期（天，0=不过期）',
  max_failed_attempts: '最大登录失败次数（0=不锁定）',
  lock_minutes: '锁定时长（分钟，0=需管理员解锁）',
  max_sessions_per_user: '同账号最大会话数（0=不限）',
  idle_timeout_minutes: '空闲超时（分钟，0=不限）',
  access_token_ttl_minutes: '访问令牌有效期（分钟）',
  refresh_token_ttl_days: '刷新令牌有效期（天）',
  single_device_login: '单设备登录（新登录挤掉旧会话）',
  audit_log_retention_days: '操作日志保留天数',
  auth_log_retention_days: '认证日志保留天数',
  log_get_requests: '记录 GET 请求（会产生大量日志）',
}

const isBool = (v) => v === 'true' || v === 'false'

onMounted(async () => {
  await load()
  await loadSettings()
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h2 class="page-title">角色权限</h2>
        <p class="page-desc">角色是权限集合，角色之间无继承关系；推荐把角色授予用户组而非逐个用户</p>
      </div>
      <div class="head-actions">
        <button v-if="tab === 'roles'" class="btn primary" @click="creating = true">+ 新建角色</button>
        <button v-else class="btn primary" @click="saveSettings">保存策略</button>
      </div>
    </header>

    <nav class="tabs">
      <button :class="['tab', { active: tab === 'roles' }]" @click="tab = 'roles'">角色与权限</button>
      <button :class="['tab', { active: tab === 'policy' }]" @click="tab = 'policy'">安全策略</button>
    </nav>

    <p v-if="errorMsg" class="err-bar">{{ errorMsg }}</p>

    <section v-if="tab === 'roles'" class="role-layout">
      <div class="role-list">
        <div v-for="r in roles" :key="r.id" :class="['role-item', { active: current?.id === r.id }]" @click="openRole(r)">
          <div class="role-top">
            <span class="role-name">{{ r.name }}</span>
            <span v-if="r.is_system" class="tag sys">内置</span>
          </div>
          <div class="role-meta">{{ r.code }} · {{ r.permission_count }} 项权限 · {{ r.user_count }} 人</div>
          <div class="role-desc">{{ r.description }}</div>
          <button v-if="!r.is_system" class="link danger role-del" @click.stop="removeRole(r)">删除</button>
        </div>
      </div>

      <div class="perm-panel">
        <div v-if="!current" class="perm-empty">选择左侧角色以配置权限</div>
        <template v-else>
          <div class="perm-head">
            <div>
              <b>{{ current.name }}</b>
              <span class="dim">（{{ current.code }}）</span>
              <span class="dim">已选 {{ selected.length }} 项</span>
            </div>
            <button class="btn primary" :disabled="!dirty" @click="savePerms">保存权限</button>
          </div>
          <p v-if="current.code === 'super_admin'" class="notice">超级管理员默认拥有全部权限且不参与鉴权校验，无需单独配置。</p>
          <div class="tree">
            <div v-for="g in tree" :key="g.module" class="tree-group">
              <label class="tree-module">
                <input type="checkbox" :checked="moduleChecked(g)" @change="toggleModule(g, $event.target.checked)" />
                <b>{{ g.module_name }}</b>
                <span class="dim">（{{ g.module }}）</span>
              </label>
              <div class="tree-items">
                <label v-for="p in g.permissions" :key="p.code" class="tree-item" :title="p.resource || p.code">
                  <input type="checkbox" :checked="selected.includes(p.code)" @change="togglePerm(p.code)" />
                  <span>{{ p.name }}</span>
                  <code class="code">{{ p.code }}</code>
                </label>
              </div>
            </div>
          </div>
        </template>
      </div>
    </section>

    <section v-else class="policy">
      <div class="policy-grid">
        <label v-for="s in settingsSchema" :key="s.key" class="policy-item">
          <span class="policy-label">{{ SETTING_LABELS[s.key] || s.key }}</span>
          <select v-if="isBool(settings[s.key])" v-model="settings[s.key]" class="input">
            <option value="true">开启</option><option value="false">关闭</option>
          </select>
          <select v-else-if="s.key === 'password_complexity'" v-model="settings[s.key]" class="input">
            <option value="none">none 不限制</option>
            <option value="low">low 字母+数字</option>
            <option value="medium">medium 大小写+数字</option>
            <option value="high">high 大小写+数字+符号</option>
          </select>
          <input v-else v-model="settings[s.key]" class="input" type="number" min="0" />
          <code class="code">{{ s.key }}</code>
        </label>
      </div>
    </section>

    <!-- 新建角色弹窗（点击遮罩不关闭，需手动关闭或按 ESC） -->
    <div v-if="creating" class="mask">
      <div class="modal">
        <h3 class="modal-title">新建角色</h3>
        <div class="grid">
          <label class="f"><span>角色标识 <b>*</b></span><input v-model="form.code" class="input" placeholder="如 data_reviewer" /></label>
          <label class="f"><span>角色名称 <b>*</b></span><input v-model="form.name" class="input" /></label>
          <label class="f span2"><span>说明</span><input v-model="form.description" class="input" /></label>
        </div>
        <p v-if="createError" class="err-bar modal-err">{{ createError }}</p>
        <footer class="modal-foot">
          <button class="btn" @click="creating = false">取消</button>
          <button class="btn primary" @click="createRole">创建</button>
        </footer>
      </div>
    </div>

    <!-- 统一确认弹窗 -->
    <ConfirmDialog
      v-model="confirmDlg.visible"
      :title="confirmDlg.title"
      :message="confirmDlg.message"
      :confirm-text="confirmDlg.confirmText"
      @confirm="runConfirm"
    />
  </div>
</template>

<style scoped>
.page { padding: 22px 26px 40px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-title { font-size: 19px; font-weight: 700; }
.page-desc { margin-top: 4px; font-size: 12.5px; color: var(--c-secondary); }
.tabs { display: flex; gap: 4px; margin: 16px 0 12px; border-bottom: 1px solid var(--c-border); }
.tab {
  padding: 8px 16px; font-size: 13px; font-weight: 600; border: none; background: transparent;
  color: var(--c-secondary); cursor: pointer; border-bottom: 2px solid transparent; font-family: var(--font);
}
.tab.active { color: var(--c-accent); border-bottom-color: var(--c-accent); }
.err-bar {
  margin-bottom: 10px; padding: 8px 12px; font-size: 12.5px; color: var(--c-danger);
  border: 1px solid color-mix(in srgb, var(--c-danger) 30%, transparent);
  background: color-mix(in srgb, var(--c-danger) 10%, transparent); border-radius: var(--radius-sm);
}
/* 弹窗内错误条：固定在底部按钮区上方，保存失败时始终可见 */
.modal-err { margin: 14px 0 0; }
.role-layout { display: grid; grid-template-columns: 280px 1fr; gap: 16px; align-items: start; }
.role-list { display: flex; flex-direction: column; gap: 8px; }
.role-item {
  position: relative; padding: 10px 12px; border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); cursor: pointer;
}
.role-item:hover { border-color: var(--c-accent); }
.role-item.active { border-color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 8%, transparent); }
.role-top { display: flex; align-items: center; gap: 6px; }
.role-name { font-size: 13.5px; font-weight: 700; }
.role-meta { margin-top: 2px; font-size: 11.5px; color: var(--c-accent); }
.role-desc { margin-top: 3px; font-size: 11.5px; color: var(--c-secondary); line-height: 1.5; }
.role-del { position: absolute; right: 10px; bottom: 8px; }
.tag { padding: 0 6px; font-size: 11px; border-radius: 4px; border: 1px solid var(--c-border); color: var(--c-secondary); }
.perm-panel { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 14px 16px; min-height: 320px; }
.perm-empty { padding: 60px 0; text-align: center; color: var(--c-secondary); font-size: 13px; }
.perm-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--c-border); }
.notice { margin: 10px 0; font-size: 12px; color: var(--c-secondary); }
.tree { margin-top: 8px; max-height: 60vh; overflow: auto; }
.tree-group { padding: 8px 0; border-bottom: 1px dashed var(--c-border); }
.tree-module { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; }
.tree-items { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 6px; padding-left: 22px; }
.tree-item { display: flex; align-items: center; gap: 5px; font-size: 12.5px; cursor: pointer; min-width: 220px; }
.code { font-family: ui-monospace, Consolas, monospace; font-size: 11px; color: var(--c-secondary); }
.dim { color: var(--c-secondary); font-weight: 400; font-size: 12px; }
.policy-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.policy-item {
  display: grid; grid-template-columns: 1fr auto; gap: 4px 10px; align-items: center;
  padding: 10px 12px; border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel);
}
.policy-label { font-size: 12.5px; font-weight: 600; }
.policy-item .code { grid-column: 1 / -1; }
.input {
  height: 32px; padding: 0 10px; font-size: 13px; font-family: var(--font);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg); color: var(--c-fg); outline: none; min-width: 120px;
}
.input:focus { border-color: var(--c-accent); }
.btn {
  height: 32px; padding: 0 14px; font-size: 13px; font-weight: 600; font-family: var(--font);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg); color: var(--c-fg); cursor: pointer;
}
.btn:hover { background: var(--c-muted); }
.btn.primary { background: var(--c-btn-primary-bg, var(--c-accent)); border-color: transparent; color: #fff; }
.btn.primary:disabled { opacity: 0.5; cursor: not-allowed; }
.link { border: none; background: none; color: var(--c-accent); cursor: pointer; font-size: 12px; font-family: var(--font); }
.link.danger { color: var(--c-danger); }
.mask { position: fixed; inset: 0; background: var(--c-overlay); display: flex; align-items: center; justify-content: center; z-index: 60; padding: 20px; }
.modal { width: 100%; max-width: 480px; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 12px; padding: 20px 22px; }
.modal-title { font-size: 15px; font-weight: 700; margin-bottom: 16px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.f { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--c-secondary); font-weight: 600; }
.f.span2 { grid-column: span 2; }
.f b { color: var(--c-danger); }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
</style>
