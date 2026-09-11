<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { groupApi, roleApi, userApi } from '../../api/auth'
import { useEscClose } from '../../composables/useEscClose'
import { useToast } from '../../composables/useToast'
import ConfirmDialog from '../common/ConfirmDialog.vue'
import MultiSelect from '../common/MultiSelect.vue'
import Pagination from '../common/Pagination.vue'

const toast = useToast()

const tab = ref('users')
const loading = ref(false)
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
  [pwdResult.value != null, closePwdResult],
  [pwdEditing.value, () => { pwdEditing.value = false }],
  [confirmDlg.value.visible, () => { confirmDlg.value.visible = false }],
  [editing.value, () => { editing.value = false }],
  [groupEditing.value, () => { groupEditing.value = false }],
])

/* ── 用户 ── */
const users = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = ref({ keyword: '', status: '', group_id: '' })

const allRoles = ref([])
const allGroups = ref([])

/* 多选组件的数据源（label 展示 + desc 次要信息） */
const roleOptions = computed(() =>
  allRoles.value.map((r) => ({ value: r.id, label: r.name, desc: r.code })))
const groupOptions = computed(() =>
  allGroups.value.map((g) => ({ value: g.id, label: g.name, desc: g.code || '' })))
const userOptions = computed(() =>
  allUsers.value.map((u) => ({ value: u.id, label: u.username, desc: u.nickname || '' })))

const STATUS_META = {
  active: { label: '正常', cls: 'ok' },
  disabled: { label: '已停用', cls: 'bad' },
  locked: { label: '已锁定', cls: 'warn' },
}

async function loadRoles() { allRoles.value = await roleApi.list() }
async function loadGroups() { allGroups.value = await groupApi.list() }

async function loadUsers() {
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await userApi.list({
      keyword: filters.value.keyword,
      status: filters.value.status,
      group_id: filters.value.group_id,
      page: page.value,
      page_size: pageSize.value,
    })
    users.value = res.items || []
    total.value = res.total || 0
  } catch (err) {
    errorMsg.value = err.message
  } finally {
    loading.value = false
  }
}

/* ── 用户编辑弹窗 ── */
const editing = ref(false)
const form = ref(blankForm())

function blankForm() {
  return {
    id: '', username: '', nickname: '', email: '', phone: '',
    password: '', status: 'active', remark: '', role_ids: [], group_ids: [],
  }
}

function openCreate() {
  form.value = blankForm()
  errorMsg.value = ''
  editing.value = true
}

async function openEdit(row) {
  try {
    const detail = await userApi.get(row.id)
    form.value = {
      id: detail.id, username: detail.username, nickname: detail.nickname || '',
      email: detail.email || '', phone: detail.phone || '', password: '',
      status: detail.status, remark: detail.remark || '',
      role_ids: (detail.roles || []).map((r) => r.id),
      group_ids: (detail.groups || []).map((g) => g.id),
    }
    errorMsg.value = ''
    editing.value = true
  } catch (err) {
    errorMsg.value = err.message
  }
}

async function saveUser() {
  errorMsg.value = ''
  try {
    const payload = { ...form.value }
    if (!payload.id) delete payload.id
    if (!payload.password) delete payload.password
    if (payload.id) {
      await userApi.update(payload.id, payload)
    } else {
      const created = await userApi.create(payload)
      if (created?.temp_password) {
        pwdResult.value = { title: '用户已创建', username: created.username, password: created.temp_password }
      }
    }
    editing.value = false
    await loadUsers()
  } catch (err) {
    errorMsg.value = err.message
  }
}

/* 初始密码展示弹窗（替代原生 alert，可复制） */
const pwdResult = ref(null)
const pwdCopied = ref(false)

async function copyTempPwd() {
  try {
    await navigator.clipboard.writeText(pwdResult.value.password)
    pwdCopied.value = true
    setTimeout(() => { pwdCopied.value = false }, 1600)
  } catch { /* 剪贴板不可用时静默 */ }
}
function closePwdResult() {
  pwdResult.value = null
}

async function toggleStatus(row) {
  const next = row.status === 'active' ? 'disabled' : 'active'
  try {
    await userApi.setStatus(row.id, next)
    await loadUsers()
  } catch (err) { errorMsg.value = err.message }
}

/* ── 修改密码弹窗（管理员为用户设置新密码，支持随机生成） ── */
const pwdEditing = ref(false)
const pwdForm = ref({ id: '', username: '', password: '' })
const pwdError = ref('')

function resetPwd(row) {
  pwdForm.value = { id: row.id, username: row.username, password: '' }
  pwdError.value = ''
  pwdEditing.value = true
}

/* 随机生成兼容各复杂度策略的密码（大小写 + 数字 + 特殊字符，避开易混淆字符） */
function genPassword() {
  const sets = ['abcdefghjkmnpqrstuvwxyz', 'ABCDEFGHJKLMNPQRSTUVWXYZ', '23456789', '!@#$%^&*']
  const all = sets.join('')
  const rand = (n) => crypto.getRandomValues(new Uint32Array(1))[0] % n
  const chars = [...sets.map((s) => s[rand(s.length)]), ...Array.from({ length: 8 }, () => all[rand(all.length)])]
  for (let i = chars.length - 1; i > 0; i--) {
    const j = rand(i + 1)
    ;[chars[i], chars[j]] = [chars[j], chars[i]]
  }
  pwdForm.value.password = chars.join('')
}

async function savePassword() {
  pwdError.value = ''
  if (!pwdForm.value.password) {
    pwdError.value = '请输入新密码，或点击「随机生成」'
    return
  }
  try {
    const res = await userApi.resetPassword(pwdForm.value.id, pwdForm.value.password)
    pwdEditing.value = false
    pwdResult.value = {
      title: '密码已修改', username: res.username, password: res.temp_password,
      pwdLabel: '新密码', tip: '密码已生效，该用户所有登录态已失效，请将新密码告知用户。',
    }
  } catch (err) { pwdError.value = err.message }
}

function removeUser(row) {
  askConfirm({
    title: '删除用户',
    message: `确认删除用户 ${row.username}？\n该操作会同时踢出该用户所有会话。`,
    confirmText: '删除',
    action: async () => {
      try {
        await userApi.remove(row.id)
        toast.success('用户已删除')
        await loadUsers()
      } catch (err) { errorMsg.value = err.message }
    },
  })
}

/* ── 用户组 ── */
const groups = ref([])
const groupEditing = ref(false)
const groupForm = ref({ id: '', name: '', code: '', remark: '', member_ids: [], role_ids: [] })
const allUsers = ref([])

async function loadAllUsers() {
  const res = await userApi.list({ page: 1, page_size: 1000 })
  allUsers.value = res.items || []
}

async function loadGroupList() {
  groups.value = await groupApi.list()
}

function openGroupCreate() {
  groupForm.value = { id: '', name: '', code: '', remark: '', member_ids: [], role_ids: [] }
  groupEditing.value = true
}

async function openGroupEdit(row) {
  const detail = await groupApi.get(row.id)
  groupForm.value = {
    id: detail.id, name: detail.name, code: detail.code || '', remark: detail.remark || '',
    member_ids: detail.member_ids || [], role_ids: detail.role_ids || [],
  }
  groupEditing.value = true
}

async function saveGroup() {
  errorMsg.value = ''
  try {
    const payload = { ...groupForm.value }
    if (!payload.id) delete payload.id
    if (payload.id) await groupApi.update(payload.id, payload)
    else await groupApi.create(payload)
    groupEditing.value = false
    await Promise.all([loadGroupList(), loadGroups()])
  } catch (err) { errorMsg.value = err.message }
}

function removeGroup(row) {
  askConfirm({
    title: '删除用户组',
    message: `确认删除用户组 ${row.name}？`,
    confirmText: '删除',
    action: async () => {
      try {
        await groupApi.remove(row.id)
        toast.success('用户组已删除')
        await Promise.all([loadGroupList(), loadGroups()])
      } catch (err) { errorMsg.value = err.message }
    },
  })
}

watch(tab, (v) => {
  if (v === 'groups' && !groups.value.length) loadGroupList()
})

onMounted(async () => {
  await Promise.all([loadRoles(), loadGroups(), loadAllUsers()])
  await loadUsers()
})
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h2 class="page-title">用户管理</h2>
        <p class="page-desc">维护系统用户与用户组，并为用户分配角色（推荐按用户组批量授权）</p>
      </div>
      <div class="head-actions">
        <button v-if="tab === 'users'" class="btn primary" @click="openCreate">+ 新建用户</button>
        <button v-else class="btn primary" @click="openGroupCreate">+ 新建用户组</button>
      </div>
    </header>

    <nav class="tabs">
      <button :class="['tab', { active: tab === 'users' }]" @click="tab = 'users'">用户</button>
      <button :class="['tab', { active: tab === 'groups' }]" @click="tab = 'groups'">用户组</button>
    </nav>

    <p v-if="errorMsg" class="err-bar">{{ errorMsg }}</p>

    <!-- 用户 -->
    <section v-if="tab === 'users'">
      <div class="filters">
        <input v-model="filters.keyword" class="input" placeholder="搜索用户名/昵称/邮箱" @keyup.enter="page = 1; loadUsers()" />
        <select v-model="filters.status" class="input" @change="page = 1; loadUsers()">
          <option value="">全部状态</option>
          <option value="active">正常</option>
          <option value="disabled">已停用</option>
          <option value="locked">已锁定</option>
        </select>
        <select v-model="filters.group_id" class="input" @change="page = 1; loadUsers()">
          <option value="">全部用户组</option>
          <option v-for="g in allGroups" :key="g.id" :value="g.id">{{ g.name }}</option>
        </select>
        <button class="btn" @click="page = 1; loadUsers()">查询</button>
        <button class="btn" @click="filters = { keyword: '', status: '', group_id: '' }; page = 1; loadUsers()">重置</button>
      </div>

      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>用户名</th><th>昵称</th><th>邮箱</th><th>角色</th><th>用户组</th>
              <th>状态</th><th>最后登录</th><th class="ta-r">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!users.length">
              <td colspan="8" class="empty">{{ loading ? '加载中…' : '暂无数据' }}</td>
            </tr>
            <tr v-for="u in users" :key="u.id">
              <td class="mono">{{ u.username }}<span v-if="u.is_system" class="tag sys">内置</span></td>
              <td>{{ u.nickname || '-' }}</td>
              <td>{{ u.email || '-' }}</td>
              <td>
                <span v-for="r in u.roles" :key="r.id" class="chip">{{ r.name }}</span>
                <span v-if="!u.roles?.length" class="dim">-</span>
              </td>
              <td>
                <span v-for="g in u.groups" :key="g.id" class="chip alt">{{ g.name }}</span>
                <span v-if="!u.groups?.length" class="dim">-</span>
              </td>
              <td>
                <span :class="['status', STATUS_META[u.status]?.cls]">{{ STATUS_META[u.status]?.label || u.status }}</span>
              </td>
              <td class="dim">{{ (u.last_login_at || '').replace('T', ' ').slice(0, 16) || '-' }}</td>
              <td class="ta-r ops">
                <button class="link" @click="openEdit(u)">编辑</button>
                <button class="link" @click="resetPwd(u)">修改密码</button>
                <button class="link" :disabled="u.is_system" @click="toggleStatus(u)">
                  {{ u.status === 'active' ? '停用' : '启用' }}
                </button>
                <button class="link danger" :disabled="u.is_system" @click="removeUser(u)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Pagination v-model:page="page" v-model:page-size="pageSize" :total="total" @change="loadUsers" />
    </section>

    <!-- 用户组 -->
    <section v-else>
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr><th>名称</th><th>标识</th><th>成员数</th><th>备注</th><th class="ta-r">操作</th></tr>
          </thead>
          <tbody>
            <tr v-if="!groups.length"><td colspan="5" class="empty">暂无数据</td></tr>
            <tr v-for="g in groups" :key="g.id">
              <td>{{ g.name }}<span v-if="g.is_system" class="tag sys">内置</span></td>
              <td class="mono dim">{{ g.code || '-' }}</td>
              <td>{{ g.member_count }}</td>
              <td class="dim">{{ g.remark || '-' }}</td>
              <td class="ta-r ops">
                <button class="link" @click="openGroupEdit(g)">编辑</button>
                <button class="link danger" :disabled="g.is_system" @click="removeGroup(g)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 用户弹窗（点击遮罩不关闭，需手动关闭或按 ESC） -->
    <div v-if="editing" class="mask">
      <div class="modal">
        <header class="modal-head">
          <h3 class="modal-title">{{ form.id ? '编辑用户' : '新建用户' }}</h3>
          <button class="modal-close" title="关闭" @click="editing = false">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div class="modal-body">
          <p class="sec-title">基本信息</p>
          <div class="grid">
            <label class="f"><span>用户名 <b>*</b></span>
              <input v-model="form.username" class="input" :disabled="!!form.id" placeholder="登录名" />
            </label>
            <label class="f"><span>昵称</span><input v-model="form.nickname" class="input" /></label>
            <label class="f"><span>邮箱</span><input v-model="form.email" class="input" /></label>
            <label class="f"><span>手机号</span><input v-model="form.phone" class="input" /></label>
            <label class="f"><span>{{ form.id ? '重设密码（留空不改）' : '初始密码（留空随机生成）' }}</span>
              <input v-model="form.password" class="input" type="password" autocomplete="new-password" />
            </label>
            <label class="f"><span>状态</span>
              <select v-model="form.status" class="input" :disabled="form.is_system" title="内置账号不可停用">
                <option value="active">正常</option><option value="disabled">停用</option>
              </select>
            </label>
            <label class="f span2"><span>备注</span>
              <textarea v-model="form.remark" class="input ta" rows="2" placeholder="选填" />
            </label>
          </div>

          <p class="sec-title">权限分配 <small>角色决定能做什么，用户组用于批量授权</small></p>
          <div class="grid">
            <label class="f"><span>角色 <em v-if="form.role_ids.length" class="cnt">{{ form.role_ids.length }}</em></span>
              <MultiSelect v-model="form.role_ids" :options="roleOptions" placeholder="选择角色" />
            </label>
            <label class="f"><span>用户组 <em v-if="form.group_ids.length" class="cnt">{{ form.group_ids.length }}</em></span>
              <MultiSelect v-model="form.group_ids" :options="groupOptions" placeholder="选择用户组" />
            </label>
          </div>
        </div>
        <p v-if="errorMsg" class="err-bar modal-err">{{ errorMsg }}</p>
        <footer class="modal-foot">
          <button class="btn" @click="editing = false">取消</button>
          <button class="btn primary" @click="saveUser">保存</button>
        </footer>
      </div>
    </div>

    <!-- 用户组弹窗（点击遮罩不关闭） -->
    <div v-if="groupEditing" class="mask">
      <div class="modal">
        <header class="modal-head">
          <h3 class="modal-title">{{ groupForm.id ? '编辑用户组' : '新建用户组' }}</h3>
          <button class="modal-close" title="关闭" @click="groupEditing = false">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div class="modal-body">
          <p class="sec-title">基本信息</p>
          <div class="grid">
            <label class="f"><span>名称 <b>*</b></span><input v-model="groupForm.name" class="input" /></label>
            <label class="f"><span>标识</span><input v-model="groupForm.code" class="input" /></label>
            <label class="f span2"><span>备注</span>
              <textarea v-model="groupForm.remark" class="input ta" rows="2" placeholder="选填" />
            </label>
          </div>

          <p class="sec-title">成员与角色 <small>组内成员自动继承组角色</small></p>
          <div class="grid">
            <label class="f"><span>成员 <em v-if="groupForm.member_ids.length" class="cnt">{{ groupForm.member_ids.length }}</em></span>
              <MultiSelect v-model="groupForm.member_ids" :options="userOptions" searchable placeholder="添加成员" />
            </label>
            <label class="f"><span>角色 <em v-if="groupForm.role_ids.length" class="cnt">{{ groupForm.role_ids.length }}</em></span>
              <MultiSelect v-model="groupForm.role_ids" :options="roleOptions" placeholder="分配角色" />
            </label>
          </div>
        </div>
        <p v-if="errorMsg" class="err-bar modal-err">{{ errorMsg }}</p>
        <footer class="modal-foot">
          <button class="btn" @click="groupEditing = false">取消</button>
          <button class="btn primary" @click="saveGroup">保存</button>
        </footer>
      </div>
    </div>

    <!-- 修改密码弹窗（点击遮罩不关闭） -->
    <div v-if="pwdEditing" class="mask">
      <div class="modal modal-sm">
        <header class="modal-head">
          <h3 class="modal-title">修改密码</h3>
          <button class="modal-close" title="关闭" @click="pwdEditing = false">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div class="modal-body">
          <p class="pwd-warn">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
            修改后该用户所有登录态立即失效，需使用新密码重新登录。
          </p>
          <div class="grid">
            <label class="f"><span>用户名</span><input class="input" :value="pwdForm.username" disabled /></label>
            <label class="f span2"><span>新密码 <b>*</b></span>
              <div class="pwd-input">
                <input v-model="pwdForm.password" class="input" type="text" autocomplete="off" placeholder="输入新密码，或点击随机生成" />
                <button class="btn gen" @click="genPassword">随机生成</button>
              </div>
            </label>
          </div>
        </div>
        <p v-if="pwdError" class="err-bar modal-err">{{ pwdError }}</p>
        <footer class="modal-foot">
          <button class="btn" @click="pwdEditing = false">取消</button>
          <button class="btn primary" @click="savePassword">确认修改</button>
        </footer>
      </div>
    </div>

    <!-- 密码展示（创建用户/修改密码成功后一次性显示，点击遮罩不关闭） -->
    <div v-if="pwdResult" class="mask">
      <div class="modal modal-sm">
        <header class="modal-head">
          <h3 class="modal-title">{{ pwdResult.title || '用户已创建' }}</h3>
          <button class="modal-close" title="关闭" @click="closePwdResult">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div class="modal-body">
          <p class="pwd-warn">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
            {{ pwdResult.tip || '初始密码仅显示一次，请复制保存并通知用户首次登录后修改。' }}
          </p>
          <div class="pwd-rows">
            <div class="pwd-row"><span>用户名</span><code>{{ pwdResult.username }}</code></div>
            <div class="pwd-row"><span>{{ pwdResult.pwdLabel || '初始密码' }}</span><code>{{ pwdResult.password }}</code></div>
          </div>
        </div>
        <footer class="modal-foot">
          <button class="btn" @click="copyTempPwd">{{ pwdCopied ? '已复制 ✓' : '复制密码' }}</button>
          <button class="btn primary" @click="closePwdResult">我已保存</button>
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
.head-actions { display: flex; gap: 8px; }
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
.modal-err { margin: 10px 20px 0; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.input {
  height: 32px; padding: 0 10px; font-size: 13px; font-family: var(--font);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg); color: var(--c-fg); outline: none; min-width: 140px;
}
.input:focus { border-color: var(--c-accent); }
.table-wrap { border: 1px solid var(--c-border); border-radius: var(--radius); overflow: auto; background: var(--c-panel); }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th {
  text-align: left; padding: 10px 12px; font-size: 12px; font-weight: 600; color: var(--c-secondary);
  border-bottom: 1px solid var(--c-border); background: var(--c-muted); white-space: nowrap;
}
.table td { padding: 10px 12px; border-bottom: 1px solid var(--c-border); vertical-align: middle; }
.table tr:last-child td { border-bottom: none; }
.ta-r { text-align: right; }
.dim { color: var(--c-secondary); }
.mono { font-family: ui-monospace, Consolas, monospace; }
.empty { text-align: center; color: var(--c-secondary); padding: 28px; }
.chip {
  display: inline-block; margin: 1px 4px 1px 0; padding: 1px 7px; font-size: 11.5px;
  border-radius: 10px; background: color-mix(in srgb, var(--c-accent) 14%, transparent);
  color: var(--c-accent); border: 1px solid color-mix(in srgb, var(--c-accent) 26%, transparent);
}
.chip.alt { background: transparent; color: var(--c-secondary); border-color: var(--c-border); }
.tag { margin-left: 6px; padding: 0 6px; font-size: 11px; border-radius: 4px; border: 1px solid var(--c-border); color: var(--c-secondary); }
.status { padding: 2px 8px; border-radius: 10px; font-size: 11.5px; font-weight: 600; }
.status.ok { color: var(--c-success); background: color-mix(in srgb, var(--c-success) 12%, transparent); }
.status.bad { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 12%, transparent); }
.status.warn { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 14%, transparent); }
.ops { white-space: nowrap; }
.link {
  border: none; background: none; color: var(--c-accent); cursor: pointer;
  font-size: 12.5px; font-family: var(--font); padding: 2px 6px;
}
.link:hover { text-decoration: underline; }
.link:disabled { color: var(--c-secondary); cursor: not-allowed; text-decoration: none; }
.link.danger { color: var(--c-danger); }
.btn {
  height: 32px; padding: 0 14px; font-size: 13px; font-weight: 600; font-family: var(--font);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg); color: var(--c-fg); cursor: pointer;
}
.btn:hover { background: var(--c-muted); }
.btn.primary { background: var(--c-btn-primary-bg, var(--c-accent)); border-color: transparent; color: #fff; }
.btn.primary:hover { background: var(--c-btn-primary-bg-hover, var(--c-accent)); filter: brightness(1.08); }
.mask {
  position: fixed; inset: 0; background: var(--c-overlay); display: flex;
  align-items: center; justify-content: center; z-index: 60; padding: 20px;
  animation: mask-in 160ms ease-out; backdrop-filter: blur(2px);
}
@keyframes mask-in { from { opacity: 0; } to { opacity: 1; } }
.modal {
  display: flex; flex-direction: column;
  width: 100%; max-width: 640px; max-height: 86vh;
  background: var(--c-panel); border: 1px solid var(--c-border);
  border-radius: 12px; box-shadow: 0 24px 64px rgba(0, 0, 0, 0.35);
  animation: modal-in 180ms cubic-bezier(0.16, 1, 0.3, 1);
}
.modal-sm { max-width: 420px; }
@keyframes modal-in {
  from { opacity: 0; transform: translateY(14px) scale(0.985); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; border-bottom: 1px solid var(--c-border);
}
.modal-title { font-size: 15px; font-weight: 700; }
.modal-close {
  display: grid; place-items: center; width: 26px; height: 26px;
  border: none; border-radius: 6px; background: transparent;
  color: var(--c-secondary); cursor: pointer;
}
.modal-close:hover { background: var(--c-muted); color: var(--c-fg); }
.modal-body { padding: 14px 20px 4px; overflow: auto; }
.sec-title {
  margin: 4px 0 10px; padding-left: 8px; font-size: 12.5px; font-weight: 700; color: var(--c-fg);
  border-left: 3px solid var(--c-accent); line-height: 1.2;
}
.sec-title small { margin-left: 8px; font-weight: 400; font-size: 11.5px; color: var(--c-secondary); }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 14px; }
.f { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--c-secondary); font-weight: 600; }
.f.span2 { grid-column: span 2; }
.f b { color: var(--c-danger); }
.f .cnt {
  display: inline-block; min-width: 16px; padding: 0 5px; margin-left: 4px; text-align: center;
  font-size: 10.5px; font-style: normal; line-height: 15px; border-radius: 8px;
  background: color-mix(in srgb, var(--c-accent) 16%, transparent); color: var(--c-accent);
}
.modal .input {
  height: 34px; padding: 0 10px; font-size: 13px; font-family: var(--font);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg); color: var(--c-fg); outline: none; min-width: 0; width: 100%;
}
.modal .input:focus { border-color: var(--c-accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c-accent) 16%, transparent); }
.modal .input:disabled { opacity: 0.6; cursor: not-allowed; }
.modal .ta { height: auto; min-height: 52px; padding: 7px 10px; resize: vertical; line-height: 1.5; }
.modal-foot {
  display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px;
  padding: 12px 20px; border-top: 1px solid var(--c-border); background: var(--c-muted);
  border-radius: 0 0 12px 12px;
}
/* 初始密码弹窗 */
.pwd-warn {
  display: flex; align-items: center; gap: 8px; margin-bottom: 14px; padding: 9px 12px;
  font-size: 12px; line-height: 1.5; color: var(--c-accent); font-weight: 600;
  border: 1px solid color-mix(in srgb, var(--c-accent) 30%, transparent);
  background: color-mix(in srgb, var(--c-accent) 9%, transparent); border-radius: var(--radius-sm);
}
.pwd-warn svg { flex: none; }
.pwd-rows { display: flex; flex-direction: column; gap: 8px; }
.pwd-row {
  display: flex; align-items: center; gap: 12px; font-size: 12.5px; color: var(--c-secondary);
}
.pwd-row span { width: 60px; flex: none; }
.pwd-row code {
  flex: 1; padding: 7px 12px; font-size: 13px; font-family: ui-monospace, Consolas, monospace;
  background: var(--c-bg); border: 1px dashed var(--c-border); border-radius: var(--radius-sm);
  color: var(--c-fg); user-select: all;
}
/* 修改密码弹窗：输入框 + 随机生成按钮同行 */
.pwd-input { display: flex; gap: 8px; }
.pwd-input .input { flex: 1; }
.pwd-input .gen { flex: none; white-space: nowrap; }
</style>
