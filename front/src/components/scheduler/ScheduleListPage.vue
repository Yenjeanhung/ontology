<script setup>
import { onMounted, onActivated, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchSchedules, toggleSchedule, runScheduleNow, deleteSchedule, fetchScheduleRuns,
} from '../../api'
import { useToast } from '../../composables/useToast'
import ModalDialog from '../common/ModalDialog.vue'

const router = useRouter()
const toast = useToast()

const schedules = ref([])
const loading = ref(true)
const runsDialog = ref({ visible: false, id: null, name: '', runs: [], loading: false })

onMounted(loadAll)
onActivated(loadAll)

async function loadAll() {
  loading.value = true
  try { schedules.value = await fetchSchedules() } catch { toast.error('加载定时计划失败') }
  loading.value = false
}

async function toggle(s) {
  try {
    await toggleSchedule(s.id, !s.enabled)
    s.enabled = !s.enabled
    s.next_run_at = s.enabled ? s.next_run_at : null
    toast.success(s.enabled ? '已启用' : '已停用')
  } catch (e) { toast.error(`操作失败: ${e.message}`) }
}

async function runNow(s) {
  try {
    const r = await runScheduleNow(s.id)
    toast.success(`已触发（状态：${r.status}）`)
    await loadAll()
  } catch (e) { toast.error(`执行失败: ${e.message}`) }
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
  if (s.last_status === 'succeeded') return { t: '成功', c: 'ok' }
  if (s.last_status === 'failed') return { t: '失败', c: 'err' }
  if (s.last_status === 'running') return { t: '运行中', c: 'run' }
  return { t: '暂无', c: 'none' }
}
function fmtTime(t) {
  if (!t) return '—'
  return String(t).replace('T', ' ').slice(0, 19)
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

    <div class="sc-table" v-if="!loading && schedules.length">
      <div class="sc-row sc-head">
        <span class="c-name">名称</span>
        <span class="c-wf">关联工作流</span>
        <span class="c-trig">触发器</span>
        <span class="c-next">下次运行</span>
        <span class="c-status">上次状态</span>
        <span class="c-fail">连续失败</span>
        <span class="c-actions">操作</span>
      </div>
      <div class="sc-row" v-for="s in schedules" :key="s.id">
        <span class="c-name" @click="router.push(`/schedules/${s.id}`)">{{ s.name }}</span>
        <span class="c-wf">{{ s.workflow_name }}</span>
        <span class="c-trig">{{ s.trigger_summary }}</span>
        <span class="c-next">{{ fmtTime(s.next_run_at) }}</span>
        <span class="c-status">
          <span class="tag" :class="statusTag(s).c">{{ statusTag(s).t }}</span>
        </span>
        <span class="c-fail">
          <span v-if="s.consecutive_failures > 0" class="fail-badge" :class="{ warn: s.consecutive_failures >= s.max_failures_alert }">
            {{ s.consecutive_failures }}
          </span>
          <span v-else class="muted">0</span>
        </span>
        <span class="c-actions">
          <span class="badge" :class="enabledLabel(s).c">{{ enabledLabel(s).t }}</span>
          <button class="btn sm ghost" @click="toggle(s)">{{ s.enabled ? '停用' : '启用' }}</button>
          <button class="btn sm ghost" @click="runNow(s)">立即执行</button>
          <button class="btn sm ghost" @click="viewRuns(s)">运行历史</button>
          <button class="btn sm ghost danger" @click="askDelete(s)">删除</button>
        </span>
      </div>
    </div>

    <div class="sc-empty" v-if="!loading && !schedules.length">
      暂无定时计划，点击右上角「新建计划」为工作流设置定时触发。
    </div>
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

    <ModalDialog v-model="runsDialog.visible" title="运行历史">
      <div class="runs">
        <div v-if="runsDialog.loading" class="runs-empty">加载中...</div>
        <div v-else-if="!runsDialog.runs.length" class="runs-empty">暂无运行记录</div>
        <div v-for="r in runsDialog.runs" :key="r.id" class="run-row">
          <span class="tag" :class="r.status === 'succeeded' ? 'ok' : r.status === 'failed' ? 'err' : 'run'">
            {{ r.status === 'succeeded' ? '成功' : r.status === 'failed' ? '失败' : r.status }}
          </span>
          <span class="run-id">{{ r.id }}</span>
          <span class="run-time">{{ fmtTime(r.started_at) }}</span>
          <span class="run-err" v-if="r.error">⚠ {{ r.error }}</span>
        </div>
      </div>
    </ModalDialog>
  </div>
</template>

<style scoped>
.sc-page { display: flex; flex-direction: column; gap: 18px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-head h3 { font-size: 18px; font-weight: 700; color: var(--c-fg); margin: 0 0 4px; }
.desc { font-size: 13px; color: var(--c-secondary); margin: 0; }
.head-actions { display: flex; gap: 8px; }

.sc-table { border: 1px solid var(--c-border); border-radius: 12px; overflow-x: auto; background: var(--c-panel); min-width: 1040px; }
.sc-row { display: grid; grid-template-columns: 1.6fr 1.4fr 1.2fr 1.5fr 0.8fr 0.7fr 2.8fr; align-items: center; gap: 10px; padding: 11px 16px; border-bottom: 1px solid var(--c-border); font-size: 13px; min-width: 1000px; }
.sc-row:last-child { border-bottom: 0; }
.sc-head { background: var(--c-muted); font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.sc-row:not(.sc-head):hover { background: var(--c-muted); }
.c-name { font-weight: 600; color: var(--c-fg); cursor: pointer; }
.c-name:hover { color: var(--c-accent); }
.c-wf, .c-trig, .c-next { color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.c-actions { display: flex; align-items: center; gap: 6px; flex-wrap: nowrap; }

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

.del-body { font-size: 13px; color: var(--c-fg); line-height: 1.6; }

.runs { display: flex; flex-direction: column; gap: 8px; max-height: 360px; overflow-y: auto; }
.runs-empty { text-align: center; color: var(--c-secondary); font-size: 13px; padding: 20px; }
.run-row { display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; padding: 8px 10px; border: 1px solid var(--c-border); border-radius: 8px; font-size: 12px; }
.run-id { color: var(--c-secondary); font-family: ui-monospace, monospace; }
.run-time { color: var(--c-secondary); }
.run-err { grid-column: 1 / -1; color: #e54545; font-size: 11px; }
</style>
