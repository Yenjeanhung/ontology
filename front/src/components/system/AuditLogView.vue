<script setup>
import { onMounted, ref } from 'vue'
import { auditApi } from '../../api/auth'
import { useEscClose } from '../../composables/useEscClose'
import { useToast } from '../../composables/useToast'
import ConfirmDialog from '../common/ConfirmDialog.vue'
import Pagination from '../common/Pagination.vue'

const toast = useToast()

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
  [detail.value != null, () => { detail.value = null }],
])

const tab = ref('ops')
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = ref({ user_id: '', module: '', action: '', result: '', keyword: '', start: '', end: '' })
const errorMsg = ref('')
const detail = ref(null)

const MODULES = ['kb', 'file', 'ontology', 'entity', 'graph', 'vector', 'agent', 'workflow', 'schedule', 'query', 'config', 'system']
const MODULE_NAMES = {
  kb: '知识库', file: '文件', ontology: '本体', entity: '实体', graph: '图谱', vector: '向量',
  agent: '智能体', workflow: '工作流', schedule: '定时', query: '问答', config: '配置', system: '系统',
}

async function load() {
  errorMsg.value = ''
  try {
    const res = tab.value === 'ops'
      ? await auditApi.logs({ ...filters.value, page: page.value, page_size: pageSize.value })
      : await auditApi.authLogs({ ...filters.value, page: page.value, page_size: pageSize.value })
    rows.value = res.items || []
    total.value = res.total || 0
  } catch (err) {
    errorMsg.value = err.message
  }
}

async function openDetail(row) {
  if (tab.value !== 'ops') return
  try { detail.value = await auditApi.log(row.id) } catch (err) { errorMsg.value = err.message }
}

async function exportCsv() {
  try { await auditApi.exportCsv(filters.value) } catch (err) { errorMsg.value = err.message }
}

function archive() {
  askConfirm({
    title: '按策略清理',
    message: '确认按保留策略清理超期日志？\n该操作不可恢复。',
    confirmText: '清理',
    action: async () => {
      try {
        const res = await auditApi.archive()
        toast.success(`清理完成：操作日志 ${res.audit_logs} 条，认证日志 ${res.auth_logs} 条`)
        await load()
      } catch (err) { errorMsg.value = err.message }
    },
  })
}

function fmt(ts) {
  return (ts || '').replace('T', ' ').slice(0, 19) || '-'
}

function pretty(raw) {
  if (!raw) return null
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h2 class="page-title">操作日志</h2>
        <p class="page-desc">记录登录、退出、被踢等认证事件与各类业务操作，含变更前后快照</p>
      </div>
      <div class="head-actions">
        <button class="btn" @click="exportCsv">导出 CSV</button>
        <button class="btn danger" @click="archive">按策略清理</button>
      </div>
    </header>

    <nav class="tabs">
      <button :class="['tab', { active: tab === 'ops' }]" @click="tab = 'ops'; page = 1; load()">操作日志</button>
      <button :class="['tab', { active: tab === 'auth' }]" @click="tab = 'auth'; page = 1; load()">认证日志</button>
    </nav>

    <p v-if="errorMsg" class="err-bar">{{ errorMsg }}</p>

    <div class="filters">
      <input v-model="filters.keyword" class="input" placeholder="搜索用户 / 目标 / 路径" @keyup.enter="page = 1; load()" />
      <select v-if="tab === 'ops'" v-model="filters.module" class="input" @change="page = 1; load()">
        <option value="">全部模块</option>
        <option v-for="m in MODULES" :key="m" :value="m">{{ MODULE_NAMES[m] || m }}</option>
      </select>
      <input v-model="filters.start" class="input" type="datetime-local" @change="page = 1; load()" />
      <input v-model="filters.end" class="input" type="datetime-local" @change="page = 1; load()" />
      <select v-model="filters.result" class="input" @change="page = 1; load()">
        <option value="">全部结果</option><option value="success">成功</option><option value="fail">失败</option>
      </select>
      <button class="btn" @click="page = 1; load()">查询</button>
      <button class="btn" @click="filters = { user_id: '', module: '', action: '', result: '', keyword: '', start: '', end: '' }; page = 1; load()">重置</button>
    </div>

    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr v-if="tab === 'ops'">
            <th>时间</th><th>用户</th><th>模块</th><th>动作</th><th>目标</th>
            <th>请求</th><th>IP</th><th>耗时</th><th>结果</th><th class="ta-r">详情</th>
          </tr>
          <tr v-else>
            <th>时间</th><th>用户</th><th>事件</th><th>结果</th><th>说明</th><th>IP</th><th class="ta-r">UA</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length"><td :colspan="tab === 'ops' ? 10 : 7" class="empty">暂无日志</td></tr>
          <template v-if="tab === 'ops'">
            <tr v-for="r in rows" :key="r.id">
              <td class="dim nowrap">{{ fmt(r.created_at) }}</td>
              <td>{{ r.username || '-' }}</td>
              <td>{{ MODULE_NAMES[r.module] || r.module || '-' }}</td>
              <td>{{ r.action_label || r.action }}</td>
              <td class="target">{{ r.target_name || '-' }}</td>
              <td class="mono dim">{{ r.method }} {{ r.path }}</td>
              <td class="mono dim">{{ r.ip || '-' }}</td>
              <td class="dim">{{ r.duration_ms }} ms</td>
              <td><span :class="['status', r.result === 'success' ? 'ok' : 'bad']">{{ r.result === 'success' ? '成功' : '失败' }}</span></td>
              <td class="ta-r"><button class="link" @click="openDetail(r)">查看</button></td>
            </tr>
          </template>
          <template v-else>
            <tr v-for="r in rows" :key="r.id">
              <td class="dim nowrap">{{ fmt(r.created_at) }}</td>
              <td>{{ r.username || '-' }}</td>
              <td>{{ r.action_label || r.action }}</td>
              <td><span :class="['status', r.result === 'success' ? 'ok' : 'bad']">{{ r.result === 'success' ? '成功' : '失败' }}</span></td>
              <td class="dim">{{ r.reason || '-' }}</td>
              <td class="mono dim">{{ r.ip || '-' }}</td>
              <td class="ta-r dim ua">{{ (r.user_agent || '').slice(0, 40) || '-' }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <Pagination v-model:page="page" v-model:page-size="pageSize" :total="total" @change="load" />

    <!-- 日志详情弹窗（点击遮罩不关闭，需手动关闭或按 ESC） -->
    <div v-if="detail" class="mask">
      <div class="modal">
        <h3 class="modal-title">日志详情</h3>
        <div class="kv">
          <div><span>时间</span><b>{{ fmt(detail.created_at) }}</b></div>
          <div><span>用户</span><b>{{ detail.username || '-' }}</b></div>
          <div><span>动作</span><b>{{ detail.action_label || detail.action }}</b></div>
          <div><span>目标</span><b>{{ detail.target_type }} {{ detail.target_name || '' }}</b></div>
          <div><span>请求</span><b class="mono">{{ detail.method }} {{ detail.path }}</b></div>
          <div><span>状态码</span><b>{{ detail.status_code }}</b></div>
        </div>
        <div v-if="detail.params" class="block"><h4>请求参数</h4><pre>{{ pretty(detail.params) }}</pre></div>
        <div v-if="detail.before_value" class="block"><h4>变更前</h4><pre>{{ pretty(detail.before_value) }}</pre></div>
        <div v-if="detail.after_value" class="block"><h4>变更后</h4><pre>{{ pretty(detail.after_value) }}</pre></div>
        <div v-if="detail.error_msg" class="block"><h4>错误信息</h4><pre>{{ detail.error_msg }}</pre></div>
        <footer class="modal-foot"><button class="btn" @click="detail = null">关闭</button></footer>
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
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.page-title { font-size: 19px; font-weight: 700; }
.page-desc { margin-top: 4px; font-size: 12.5px; color: var(--c-secondary); }
.head-actions { display: flex; gap: 8px; }
.tabs { display: flex; gap: 4px; margin: 16px 0 12px; border-bottom: 1px solid var(--c-border); }
.tab { padding: 8px 16px; font-size: 13px; font-weight: 600; border: none; background: transparent; color: var(--c-secondary); cursor: pointer; border-bottom: 2px solid transparent; font-family: var(--font); }
.tab.active { color: var(--c-accent); border-bottom-color: var(--c-accent); }
.err-bar { margin-bottom: 10px; padding: 8px 12px; font-size: 12.5px; color: var(--c-danger); border: 1px solid color-mix(in srgb, var(--c-danger) 30%, transparent); background: color-mix(in srgb, var(--c-danger) 10%, transparent); border-radius: var(--radius-sm); }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.input { height: 32px; padding: 0 10px; font-size: 13px; font-family: var(--font); border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg); color: var(--c-fg); outline: none; min-width: 140px; }
.input:focus { border-color: var(--c-accent); }
.btn { height: 32px; padding: 0 14px; font-size: 13px; font-weight: 600; font-family: var(--font); border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg); color: var(--c-fg); cursor: pointer; }
.btn:hover { background: var(--c-muted); }
.btn.danger { color: var(--c-danger); border-color: color-mix(in srgb, var(--c-danger) 40%, transparent); }
.table-wrap { border: 1px solid var(--c-border); border-radius: var(--radius); overflow: auto; background: var(--c-panel); }
.table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.table th { text-align: left; padding: 10px 12px; font-size: 12px; font-weight: 600; color: var(--c-secondary); border-bottom: 1px solid var(--c-border); background: var(--c-muted); white-space: nowrap; }
.table td { padding: 9px 12px; border-bottom: 1px solid var(--c-border); vertical-align: middle; }
.table tr:last-child td { border-bottom: none; }
.ta-r { text-align: right; }
.dim { color: var(--c-secondary); }
.nowrap { white-space: nowrap; }
.mono { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; }
.target { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ua { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { text-align: center; color: var(--c-secondary); padding: 28px; }
.status { padding: 2px 8px; border-radius: 10px; font-size: 11.5px; font-weight: 600; }
.status.ok { color: var(--c-success); background: color-mix(in srgb, var(--c-success) 12%, transparent); }
.status.bad { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 12%, transparent); }
.link { border: none; background: none; color: var(--c-accent); cursor: pointer; font-size: 12.5px; font-family: var(--font); }
.link:hover { text-decoration: underline; }
.mask { position: fixed; inset: 0; background: var(--c-overlay); display: flex; align-items: center; justify-content: center; z-index: 60; padding: 20px; }
.modal { width: 100%; max-width: 680px; max-height: 86vh; overflow: auto; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 12px; padding: 20px 22px; }
.modal-title { font-size: 15px; font-weight: 700; margin-bottom: 14px; }
.kv { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; font-size: 12.5px; }
.kv span { display: block; color: var(--c-secondary); font-size: 11.5px; }
.kv b { font-weight: 600; word-break: break-all; }
.block { margin-top: 14px; }
.block h4 { font-size: 12.5px; color: var(--c-secondary); margin-bottom: 5px; font-weight: 600; }
pre {
  margin: 0; padding: 10px 12px; font-size: 11.5px; line-height: 1.55; max-height: 240px; overflow: auto;
  background: var(--c-bg); border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; word-break: break-all;
}
.modal-foot { display: flex; justify-content: flex-end; margin-top: 18px; }
</style>
