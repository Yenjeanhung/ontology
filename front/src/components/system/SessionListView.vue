<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { sessionApi } from '../../api/auth'
import { useEscClose } from '../../composables/useEscClose'
import ConfirmDialog from '../common/ConfirmDialog.vue'
import Pagination from '../common/Pagination.vue'

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
useEscClose(() => [[confirmDlg.value.visible, () => { confirmDlg.value.visible = false }]])

const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const stats = ref({ online_sessions: 0, online_users: 0, today_logins: 0, total_users: 0 })
const filters = ref({ keyword: '', status: '' })
const errorMsg = ref('')
const autoRefresh = ref(true)
let timer = null

const STATUS_META = {
  online: { label: '在线', cls: 'ok' },
  offline: { label: '已退出', cls: 'dim' },
  kicked: { label: '已被踢出', cls: 'bad' },
  expired: { label: '已过期', cls: 'warn' },
}

async function load() {
  errorMsg.value = ''
  try {
    const [res, st] = await Promise.all([
      sessionApi.list({ ...filters.value, page: page.value, page_size: pageSize.value }),
      sessionApi.stats(),
    ])
    list.value = res.items || []
    total.value = res.total || 0
    stats.value = st
  } catch (err) {
    errorMsg.value = err.message
  }
}

function kick(row) {
  askConfirm({
    title: '强制下线',
    message: `确认将 ${row.username} 的该会话强制下线？`,
    confirmText: '下线',
    action: async () => {
      try { await sessionApi.kick(row.id); await load() } catch (err) { errorMsg.value = err.message }
    },
  })
}

function kickUser(row) {
  askConfirm({
    title: '全部下线',
    message: `确认踢出 ${row.username} 的全部会话（${row.online_count} 个）？`,
    confirmText: '下线',
    action: async () => {
      try { await sessionApi.kickUser(row.user_id); await load() } catch (err) { errorMsg.value = err.message }
    },
  })
}

function kickAll() {
  askConfirm({
    title: '全局强制下线',
    message: '确认将除自己以外的所有在线会话强制下线？\n该操作会记录审计日志。',
    confirmText: '全部下线',
    action: async () => {
      try { await sessionApi.kickAll(); await load() } catch (err) { errorMsg.value = err.message }
    },
  })
}

function fmt(ts) {
  return (ts || '').replace('T', ' ').slice(0, 19) || '-'
}

function duration(row) {
  if (!row.last_active_at || !row.login_at) return '-'
  const ms = new Date(row.last_active_at) - new Date(row.login_at)
  if (Number.isNaN(ms) || ms < 0) return '-'
  const min = Math.floor(ms / 60000)
  if (min < 60) return `${min} 分钟`
  return `${Math.floor(min / 60)} 小时 ${min % 60} 分`
}

onMounted(() => {
  load()
  timer = setInterval(() => { if (autoRefresh.value) load() }, 15000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h2 class="page-title">上下线管理</h2>
        <p class="page-desc">查看当前与历史登录会话，可对单个会话或整个账号执行强制下线</p>
      </div>
      <div class="head-actions">
        <label class="switch"><input v-model="autoRefresh" type="checkbox" /> 自动刷新</label>
        <button class="btn" @click="load">刷新</button>
        <button class="btn danger" @click="kickAll">全局强制下线</button>
      </div>
    </header>

    <div class="stats">
      <div class="stat"><span class="stat-num ok">{{ stats.online_sessions }}</span><span class="stat-label">在线会话</span></div>
      <div class="stat"><span class="stat-num">{{ stats.online_users }}</span><span class="stat-label">在线用户</span></div>
      <div class="stat"><span class="stat-num">{{ stats.today_logins }}</span><span class="stat-label">今日登录</span></div>
      <div class="stat"><span class="stat-num">{{ stats.total_users }}</span><span class="stat-label">用户总数</span></div>
    </div>

    <p v-if="errorMsg" class="err-bar">{{ errorMsg }}</p>

    <div class="filters">
      <input v-model="filters.keyword" class="input" placeholder="搜索用户名 / IP" @keyup.enter="page = 1; load()" />
      <select v-model="filters.status" class="input" @change="page = 1; load()">
        <option value="">全部状态</option>
        <option value="online">在线</option>
        <option value="offline">已退出</option>
        <option value="kicked">已被踢出</option>
        <option value="expired">已过期</option>
      </select>
      <button class="btn" @click="page = 1; load()">查询</button>
    </div>

    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>用户</th><th>状态</th><th>IP</th><th>设备</th>
            <th>登录时间</th><th>最后活跃</th><th>在线时长</th><th class="ta-r">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!list.length"><td colspan="8" class="empty">暂无会话</td></tr>
          <tr v-for="s in list" :key="s.id">
            <td>
              <b>{{ s.username || '-' }}</b>
              <span v-if="s.status === 'online'" class="badge">当前 {{ s.online_count }} 个会话</span>
            </td>
            <td><span :class="['status', STATUS_META[s.status]?.cls]">{{ STATUS_META[s.status]?.label || s.status }}</span></td>
            <td class="mono">{{ s.ip || '-' }}</td>
            <td class="dim">{{ s.device || '-' }}</td>
            <td class="dim">{{ fmt(s.login_at) }}</td>
            <td class="dim">{{ fmt(s.last_active_at) }}</td>
            <td class="dim">{{ duration(s) }}</td>
            <td class="ta-r ops">
              <button class="link" :disabled="s.status !== 'online'" @click="kick(s)">强制下线</button>
              <button class="link danger" @click="kickUser(s)">全部下线</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <Pagination v-model:page="page" v-model:page-size="pageSize" :total="total" @change="load" />

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
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.page-title { font-size: 19px; font-weight: 700; }
.page-desc { margin-top: 4px; font-size: 12.5px; color: var(--c-secondary); }
.head-actions { display: flex; align-items: center; gap: 8px; }
.switch { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--c-secondary); }
.stats { display: flex; gap: 12px; margin: 16px 0; flex-wrap: wrap; }
.stat {
  flex: 1; min-width: 130px; padding: 12px 14px; border: 1px solid var(--c-border);
  border-radius: var(--radius); background: var(--c-panel); display: flex; flex-direction: column; gap: 2px;
}
.stat-num { font-size: 22px; font-weight: 700; }
.stat-num.ok { color: var(--c-success); }
.stat-label { font-size: 12px; color: var(--c-secondary); }
.err-bar {
  margin-bottom: 10px; padding: 8px 12px; font-size: 12.5px; color: var(--c-danger);
  border: 1px solid color-mix(in srgb, var(--c-danger) 30%, transparent);
  background: color-mix(in srgb, var(--c-danger) 10%, transparent); border-radius: var(--radius-sm);
}
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.input {
  height: 32px; padding: 0 10px; font-size: 13px; font-family: var(--font);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg); color: var(--c-fg); outline: none; min-width: 140px;
}
.input:focus { border-color: var(--c-accent); }
.btn {
  height: 32px; padding: 0 14px; font-size: 13px; font-weight: 600; font-family: var(--font);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg); color: var(--c-fg); cursor: pointer;
}
.btn:hover { background: var(--c-muted); }
.btn.danger { color: var(--c-danger); border-color: color-mix(in srgb, var(--c-danger) 40%, transparent); }
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
.badge { margin-left: 6px; font-size: 11px; color: var(--c-accent); }
.status { padding: 2px 8px; border-radius: 10px; font-size: 11.5px; font-weight: 600; }
.status.ok { color: var(--c-success); background: color-mix(in srgb, var(--c-success) 12%, transparent); }
.status.bad { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 12%, transparent); }
.status.warn { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 14%, transparent); }
.status.dim { color: var(--c-secondary); background: var(--c-muted); }
.ops { white-space: nowrap; }
.link { border: none; background: none; color: var(--c-accent); cursor: pointer; font-size: 12.5px; font-family: var(--font); padding: 2px 6px; }
.link:hover { text-decoration: underline; }
.link:disabled { color: var(--c-secondary); cursor: not-allowed; text-decoration: none; }
.link.danger { color: var(--c-danger); }
</style>
