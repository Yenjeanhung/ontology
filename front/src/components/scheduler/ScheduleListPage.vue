<script setup>
import { onMounted, onActivated, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchSchedules, toggleSchedule, runScheduleNow, deleteSchedule, fetchScheduleRuns, getWorkflowRun,
} from '../../api'
import { useToast } from '../../composables/useToast'
import ModalDialog from '../common/ModalDialog.vue'

const router = useRouter()
const toast = useToast()

const nameKw = ref('')
const wfKw = ref('')
const schedules = ref([])
const loading = ref(true)
const filteredSchedules = computed(() => {
  const n = nameKw.value.trim().toLowerCase()
  const w = wfKw.value.trim().toLowerCase()
  if (!n && !w) return schedules.value
  return schedules.value.filter(s =>
    (!n || s.name.toLowerCase().includes(n)) &&
    (!w || (s.workflow_name || '').toLowerCase().includes(w))
  )
})
const runsDialog = ref({ visible: false, id: null, name: '', runs: [], loading: false })
const runDialog = ref({ visible: false, id: null, name: '', loading: false })
const runningIds = ref(new Set())
const historyDetail = ref(null)

onMounted(loadAll)
onActivated(loadAll)

async function openRunDetail(run) {
  historyDetail.value = { id: run.id, loading: true }
  try {
    historyDetail.value = await getWorkflowRun(run.workflow_id, run.id)
  } catch (e) {
    toast.error(`加载详情失败: ${e.message}`)
    historyDetail.value = null
  }
}
function closeRunDetail() { historyDetail.value = null }

function fmtStatusText(s) {
  return s === 'succeeded' ? '成功' : s === 'failed' ? '失败' : s === 'cancelled' ? '已取消' : (s || '未知')
}
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  if (isNaN(d)) return String(ts)
  const p = n => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
function fmtMs(ms) {
  if (ms == null) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}
function statusIcon(s) {
  if (s === 'running') return '▶'
  if (s === 'succeeded') return '✓'
  if (s === 'failed') return '✗'
  if (s === 'skipped') return '⤼'
  return '·'
}

async function loadAll() {
  loading.value = true
  try { schedules.value = await fetchSchedules() } catch { toast.error('加载定时计划失败') }
  loading.value = false
}

async function refreshOne(id) {
  try {
    const all = await fetchSchedules()
    const idx = schedules.value.findIndex(x => x.id === id)
    const found = all.find(x => x.id === id)
    if (idx !== -1 && found) schedules.value[idx] = found
  } catch {}
}

async function toggle(s) {
  try {
    await toggleSchedule(s.id, !s.enabled)
    s.enabled = !s.enabled
    s.next_run_at = s.enabled ? s.next_run_at : null
    toast.success(s.enabled ? '已启用' : '已停用')
  } catch (e) { toast.error(`操作失败: ${e.message}`) }
}

function askRun(s) {
  runDialog.value = { visible: true, id: s.id, name: s.name, loading: false }
}
async function doRun() {
  const id = runDialog.value.id
  runDialog.value.visible = false
  runningIds.value.add(id)
  try {
    await runScheduleNow(id)
    toast.success('已开始执行')
    pollUntilDone(id)
  } catch (e) {
    toast.error(`执行失败: ${e.message}`)
    runningIds.value.delete(id)
  }
}
function pollUntilDone(id) {
  const interval = setInterval(async () => {
    await refreshOne(id)
    const s = schedules.value.find(x => x.id === id)
    if (!s || s.last_status !== 'running') {
      runningIds.value.delete(id)
      clearInterval(interval)
    }
  }, 2000)
}

function askDelete(s) {
  deleteDialog.value = { visible: true, id: s.id, name: s.name, loading: false }
}
const deleteDialog = ref({ visible: false, id: null, name: '', loading: false })
async function doDelete() {
  deleteDialog.value.loading = true
  try {
    await deleteSchedule(deleteDialog.value.id)
    toast.success('已删除')
    deleteDialog.value.visible = false
    await loadAll()
  } catch (e) { toast.error(`删除失败: ${e.message}`) }
  deleteDialog.value.loading = false
}

async function viewRuns(s) {
  runsDialog.value = { visible: true, id: s.id, name: s.name, runs: [], loading: true }
  try { runsDialog.value.runs = await fetchScheduleRuns(s.id) } catch { runsDialog.value.runs = [] }
  runsDialog.value.loading = false
}

function statusTag(s) {
  if (runningIds.value.has(s.id) || s.last_status === 'running') return { t: '运行中', c: 'run' }
  if (s.last_status === 'succeeded') return { t: '成功', c: 'ok' }
  if (s.last_status === 'failed') return { t: '失败', c: 'err' }
  return { t: '暂无', c: 'none' }
}
function enabledLabel(s) {
  if (s.enabled) return { t: '启用', c: 'ok' }
  if (s.trigger === 'once' && s.last_status) return { t: '已完成', c: 'none' }
  return { t: '停用', c: 'none' }
}
</script>

<template>
  <div class="sc-page">
    <div class="page-head">
      <div>
        <h3>定时管理</h3>
        <p class="desc">为已有工作流配置定时/周期触发，到点自动执行，失败会在右上角消息中心提示。</p>
      </div>
      <div class="head-actions">
        <button class="btn primary" @click="router.push('/schedules/new')">＋ 新建计划</button>
      </div>
    </div>

    <div class="sc-toolbar">
      <div class="sc-search-group">
        <div class="sc-search">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.35-3.35"/></svg>
          <input v-model="nameKw" type="text" placeholder="搜索定时名称" />
        </div>
        <div class="sc-search">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.35-3.35"/></svg>
          <input v-model="wfKw" type="text" placeholder="搜索关联工作流" />
        </div>
      </div>
      <span class="sc-count" v-if="nameKw || wfKw">共 {{ filteredSchedules.length }} 条</span>
    </div>

    <div class="sc-table" v-if="!loading && schedules.length">
      <div class="sc-row sc-head">
        <span class="c-name">名称</span>
        <span class="c-wf">关联工作流</span>
        <span class="c-trig">触发器</span>
        <span class="c-next">下次运行</span>
        <span class="c-fail">连续失败</span>
        <span class="c-run-status">状态</span>
        <span class="c-actions">操作</span>
      </div>
      <div class="sc-row" v-for="s in filteredSchedules" :key="s.id">
        <span class="c-name" @click="router.push(`/schedules/${s.id}`)">{{ s.name }}</span>
        <span class="c-wf">{{ s.workflow_name }}</span>
        <span class="c-trig">{{ s.trigger_summary }}</span>
        <span class="c-next">{{ fmtTime(s.next_run_at) }}</span>
        <span class="c-fail">
          <span v-if="s.consecutive_failures > 0" class="fail-badge" :class="{ warn: s.consecutive_failures >= s.max_failures_alert }">
            {{ s.consecutive_failures }}
          </span>
          <span v-else class="muted">0</span>
        </span>
        <span class="c-run-status">
          <span class="tag" :class="statusTag(s).c">{{ statusTag(s).t }}</span>
        </span>
        <span class="c-actions">
          <span class="badge" :class="enabledLabel(s).c">{{ enabledLabel(s).t }}</span>
          <button class="btn sm ghost" @click="toggle(s)">{{ s.enabled ? '停用' : '启用' }}</button>
          <button class="btn sm ghost" :disabled="runningIds.has(s.id)" @click="askRun(s)">
            <span v-if="runningIds.has(s.id)" class="run-spinner"></span>
            <span v-if="runningIds.has(s.id)">执行中...</span>
            <span v-else>立即执行</span>
          </button>
          <button class="btn sm ghost" @click="viewRuns(s)">运行历史</button>
          <button class="btn sm ghost danger" @click="askDelete(s)">删除</button>
        </span>
      </div>
    </div>

    <div class="sc-empty" v-if="!loading && !schedules.length">
      暂无定时计划，点击右上角「新建计划」为工作流设置定时触发。
    </div>
    <div class="sc-empty" v-if="!loading && schedules.length && !filteredSchedules.length">未找到匹配的定时计划。</div>
    <div class="sc-empty" v-if="loading">加载中...</div>

    <ModalDialog
      v-model="deleteDialog.visible"
      title="删除定时计划"
      confirm-text="删除"
      confirm-variant="danger"
      :confirm-loading="deleteDialog.loading"
      @confirm="doDelete"
    >
      <div class="del-body">确定删除计划「{{ deleteDialog.name }}」吗？该操作不可撤销。</div>
    </ModalDialog>

    <ModalDialog
      v-model="runDialog.visible"
      title="立即执行计划"
      confirm-text="立即执行"
      @confirm="doRun"
    >
      <div class="del-body">确定立即执行计划「{{ runDialog.name }}」吗？</div>
    </ModalDialog>

    <ModalDialog v-model="runsDialog.visible" title="运行历史">
      <div class="runs">
        <div v-if="runsDialog.loading" class="runs-empty">加载中...</div>
        <div v-else-if="!runsDialog.runs.length" class="runs-empty">暂无运行记录</div>
        <div v-for="r in runsDialog.runs" :key="r.id" class="run-row">
          <span class="tag" :class="r.status === 'succeeded' ? 'ok' : r.status === 'failed' ? 'err' : 'run'">
            {{ r.status === 'succeeded' ? '成功' : r.status === 'failed' ? '失败' : r.status }}
          </span>
          <span class="run-type t-schedule">定时</span>
          <span class="run-id">{{ r.id }}</span>
          <span class="run-time">{{ fmtTime(r.started_at) }}</span>
          <span class="run-err" v-if="r.error">{{ r.error }}</span>
          <span class="run-actions"><button class="btn xs" @click="openRunDetail(r)">详情</button></span>
        </div>
      </div>
    </ModalDialog>

    <!-- 运行详情抽屉 -->
    <div class="run-drawer-mask" v-if="historyDetail" @click.self="closeRunDetail">
      <div class="run-drawer">
        <div class="rd-head">
          <span class="rd-title">运行详情 · {{ historyDetail.id }}</span>
          <span class="rd-spacer"></span>
          <button class="btn sm" @click="closeRunDetail">关闭</button>
        </div>
        <div class="rd-body" v-if="historyDetail.loading">加载中...</div>
        <div class="rd-body" v-else>
          <div class="rd-meta">
            <span class="run-badge" :class="'b-' + historyDetail.status">{{ fmtStatusText(historyDetail.status) }}</span>
            <span>开始：{{ fmtTime(historyDetail.started_at) }}</span>
            <span v-if="historyDetail.duration_ms != null">耗时：{{ fmtMs(historyDetail.duration_ms) }}</span>
          </div>
          <div class="rd-io">
            <div class="rd-io-title">输入</div>
            <pre class="log-pre">{{ JSON.stringify(historyDetail.inputs ?? {}, null, 2) }}</pre>
          </div>
          <div class="rd-io">
            <div class="rd-io-title">输出</div>
            <pre class="log-pre" :class="{ 'log-pre-err': historyDetail.error }">{{ JSON.stringify(historyDetail.outputs ?? (historyDetail.error || {}), null, 2) }}</pre>
          </div>
          <div v-if="historyDetail.error" class="rd-io">
            <div class="rd-io-title err">运行错误</div>
            <pre class="log-pre log-pre-err">{{ historyDetail.error }}</pre>
          </div>
          <div class="rd-nodes-title">节点输出（{{ Object.keys(historyDetail.node_states || {}).length }}）</div>
          <div v-for="(st, nid) in (historyDetail.node_states || {})" :key="nid" class="log-card" :class="'stc-' + (st.status || 'running')">
            <div class="log-card-head">
              <span class="log-status">{{ statusIcon(st.status || 'running') }}</span>
              <span class="log-title">{{ st.title || nid }}</span>
              <span class="log-nodeid">{{ nid }}</span>
              <span class="log-dur" v-if="st.duration_ms != null">{{ st.duration_ms }}ms</span>
            </div>
            <div class="log-card-detail-open">
              <div class="log-pane">
                <div class="log-pane-title">输入</div>
                <pre class="log-pre">{{ st.input ? JSON.stringify(st.input, null, 2) : '（无）' }}</pre>
              </div>
              <div class="log-pane">
                <div class="log-pane-title">输出</div>
                <pre class="log-pre" :class="{ 'log-pre-err': st.error }">{{ st.output != null ? JSON.stringify(st.output, null, 2) : (st.error || '（无）') }}</pre>
              </div>
            </div>
          </div>
          <div class="rd-empty" v-if="!Object.keys(historyDetail.node_states || {}).length">该运行无节点状态记录</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sc-page { display: flex; flex-direction: column; gap: 18px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-head h3 { font-size: 18px; font-weight: 700; color: var(--c-fg); margin: 0 0 4px; }
.desc { font-size: 13px; color: var(--c-secondary); margin: 0; }
.head-actions { display: flex; gap: 8px; }

.sc-table { border: 1px solid var(--c-border); border-radius: 12px; overflow-x: auto; background: var(--c-panel); min-width: 1020px; }
.sc-row { display: grid; grid-template-columns: 1.5fr 1.3fr 1.1fr 1.4fr 0.7fr 0.8fr 2.6fr; align-items: center; gap: 10px; padding: 11px 16px; border-bottom: 1px solid var(--c-border); font-size: 13px; min-width: 980px; }
.sc-row:last-child { border-bottom: 0; }
.sc-head { background: var(--c-muted); font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.sc-row:not(.sc-head):hover { background: var(--c-muted); }
.c-name { font-weight: 600; color: var(--c-fg); cursor: pointer; }
.c-name:hover { color: var(--c-accent); }
.c-wf, .c-trig, .c-next { color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.c-actions { display: flex; align-items: center; justify-content: center; gap: 6px; flex-wrap: nowrap; }
.c-actions .btn:disabled { opacity: .55; cursor: not-allowed; }
.run-spinner {
  display: inline-block;
  width: 10px; height: 10px;
  border: 2px solid currentColor;
  border-bottom-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-right: 4px;
  vertical-align: middle;
}
@keyframes spin { to { transform: rotate(360deg); } }

.tag { display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.tag.ok { background: rgba(34,160,90,.13); color: #2ea66a; }
.tag.err { background: rgba(229,69,69,.13); color: #e54545; }
.tag.run { background: rgba(45,120,212,.13); color: #2d78d4; }
.tag.none { background: var(--c-muted); color: var(--c-secondary); }

.badge { display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; border: 1px solid var(--c-border); }
.badge.ok { color: #2ea66a; border-color: rgba(46,166,106,.4); }
.badge.none { color: var(--c-secondary); }

.fail-badge { display: inline-flex; align-items: center; justify-content: center; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; font-size: 11px; font-weight: 600; background: var(--c-muted); color: var(--c-secondary); }
.fail-badge.warn { background: rgba(229,69,69,.15); color: #e54545; }
.muted { color: var(--c-secondary); }

.sc-empty { text-align: center; padding: 60px 16px; color: var(--c-secondary); font-size: 13px; }

.sc-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.sc-toolbar .right { display: flex; align-items: center; gap: 10px; }
.sc-toolbar .left { display: flex; align-items: center; gap: 10px; }
.sc-toolbar .count { color: var(--c-secondary); font-size: 13px; }
.sc-search-group { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sc-search { display: flex; align-items: center; gap: 8px; background: var(--c-bg); border: 1px solid var(--c-border); border-radius: 9px; padding: 8px 12px; width: 200px; color: var(--c-secondary); }
.sc-search input { border: none; outline: none; background: transparent; color: var(--c-text); font-size: 13px; width: 100%; }
.sc-count { color: var(--c-secondary); font-size: 13px; }

.del-body { font-size: 13px; color: var(--c-fg); line-height: 1.6; }

.runs { display: flex; flex-direction: column; gap: 8px; max-height: 360px; overflow-y: auto; }
.runs-empty { text-align: center; color: var(--c-secondary); font-size: 13px; padding: 20px; }
.run-row { display: grid; grid-template-columns: auto auto 1fr auto auto auto; gap: 10px; align-items: center; padding: 8px 10px; border: 1px solid var(--c-border); border-radius: 8px; font-size: 12px; }
.run-id { color: var(--c-secondary); font-family: ui-monospace, monospace; }
.run-time { color: var(--c-secondary); }
.run-err { grid-column: 1 / -1; color: #e54545; font-size: 11px; }
.run-type { font-size: 10.5px; font-weight: 600; padding: 1px 7px; border-radius: 999px; }
.run-type.t-schedule { background: color-mix(in srgb, var(--c-warn, #d98b00) 18%, transparent); color: var(--c-warn, #d98b00); }

/* 运行详情抽屉 */
.run-drawer-mask { position: fixed; inset: 0; z-index: 300; background: rgba(0,0,0,.35); display: flex; justify-content: flex-end; }
.run-drawer { width: 560px; max-width: 92vw; height: 100%; display: flex; flex-direction: column; background: var(--c-panel); border-left: 1px solid var(--c-border); box-shadow: -12px 0 32px rgba(0,0,0,.18); }
.rd-head { display: flex; align-items: center; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--c-border); flex-shrink: 0; }
.rd-title { font-size: 13px; font-weight: 700; }
.rd-spacer { flex: 1; }
.rd-body { flex: 1; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 14px; }
.rd-meta { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--c-secondary); flex-wrap: wrap; }
.rd-io-title { font-size: 11px; font-weight: 700; color: var(--c-secondary); margin-bottom: 5px; letter-spacing: .5px; }
.rd-io-title.err { color: var(--c-danger); }
.rd-io .log-pre { max-height: 200px; border: 1px solid var(--c-border); border-radius: 6px; padding: 8px; background: var(--c-muted); }
.rd-nodes-title { font-size: 11px; font-weight: 700; color: var(--c-secondary); border-top: 1px solid var(--c-border); padding-top: 10px; letter-spacing: .5px; }
.rd-empty { font-size: 12px; color: var(--c-secondary); text-align: center; padding: 12px; }
.run-badge { font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 999px; }
.run-badge.b-succeeded { background: color-mix(in srgb, var(--c-success) 18%, transparent); color: var(--c-success); }
.run-badge.b-failed { background: color-mix(in srgb, var(--c-danger) 18%, transparent); color: var(--c-danger); }
.run-badge.b-running { background: color-mix(in srgb, var(--c-accent) 18%, transparent); color: var(--c-accent); }
.run-badge.b-cancelled, .run-badge.b-skipped { background: var(--c-muted); color: var(--c-secondary); }
.log-pre { margin: 0; font-family: ui-monospace, monospace; font-size: 11px; line-height: 1.55; white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow-y: auto; color: var(--c-fg); }
.log-pre-err { color: var(--c-danger); }
.log-card { border: 1px solid var(--c-border); border-radius: 8px; overflow: hidden; background: var(--c-panel); flex-shrink: 0; }
.log-card.stc-running { border-color: color-mix(in srgb, var(--c-accent) 55%, transparent); }
.log-card.stc-succeeded { border-left: 3px solid var(--c-success); }
.log-card.stc-failed { border-left: 3px solid var(--c-danger); }
.log-card.stc-skipped { opacity: .55; }
.log-card-head { display: flex; align-items: center; gap: 8px; padding: 6px 10px; cursor: pointer; font-family: ui-monospace, monospace; font-size: 12px; background: var(--c-muted); }
.log-nodeid { font-size: 10px; color: var(--c-secondary); opacity: .7; }
.log-pane { flex: 1; min-width: 0; padding: 8px 10px; }
.log-pane + .log-pane { border-left: 1px solid var(--c-border); }
.log-pane-title { font-size: 10.5px; font-weight: 700; color: var(--c-secondary); margin-bottom: 4px; font-family: var(--font); letter-spacing: .5px; }
.log-card-detail-open { display: flex; gap: 0; border-top: 1px solid var(--c-border); background: var(--c-muted); }
</style>
