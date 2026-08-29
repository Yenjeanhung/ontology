<script setup>
/** 人工节点待办中心：待处理列表 + 批量处理 + 详情抽屉 + 已处理回看。 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchHumanTasks, submitHumanDecision, batchDecideHumanTasks } from '../../api'
import { useToast } from '../../composables/useToast'
import { notifications } from '../../stores/notifications'
import HumanTaskForm from './HumanTaskForm.vue'

const router = useRouter()
const toast = useToast()

const STATUS_TABS = [
  { key: 'pending', label: '待处理' },
  { key: 'approved', label: '已通过' },
  { key: 'rejected', label: '已驳回' },
  { key: 'submitted', label: '已提交' },
  { key: 'cancelled', label: '已取消' },
]

const tasks = ref([])
const loading = ref(false)
const status = ref('pending')
const detail = ref(null)          // 当前打开的任务详情
const detailLoading = ref(false)
const submitting = ref(false)
const selected = ref(new Set())   // 批量选中的任务 id
const batchComment = ref('')
const batchOperator = ref(localStorage.getItem('ks_human_operator') || '')
const batchBusy = ref(false)

const DECISION_LABEL = { approved: '通过', rejected: '驳回', submitted: '已提交', pending: '待处理', cancelled: '已取消' }

const isPendingTab = computed(() => status.value === 'pending')
const batchableTasks = computed(() => tasks.value.filter(t => t.mode !== 'form'))
const selectedIds = computed(() => [...selected.value])
const allBatchableSelected = computed(
  () => batchableTasks.value.length > 0 && batchableTasks.value.every(t => selected.value.has(t.id)))

async function load(preserveSelection = false) {
  if (loading.value) return   // 轮询与手动刷新重叠时跳过，避免竞态
  loading.value = true
  try {
    tasks.value = await fetchHumanTasks({ status: status.value, limit: 100 })
    if (!preserveSelection) selected.value = new Set()
  } catch (e) {
    toast.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function switchStatus(key) { status.value = key; detail.value = null; load() }

function isOverdue(t) { return t.overdue }

function fmtTime(s) {
  if (!s) return '—'
  try {
    const d = new Date(s)
    return Number.isNaN(d.getTime()) ? s : d.toLocaleString('zh-CN', { hour12: false })
  } catch { return s }
}

function toggleSelect(id) {
  const s = new Set(selected.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selected.value = s
}
function toggleSelectAll() {
  if (allBatchableSelected.value) selected.value = new Set()
  else selected.value = new Set(batchableTasks.value.map(t => t.id))
}

async function openDetail(id) {
  const t = tasks.value.find(x => x.id === id)
  if (!t) return
  detail.value = t
}

// 单条处理（auto_resume=true：后端后台续跑）
async function handleSubmit(payload) {
  if (!detail.value) return
  submitting.value = true
  try {
    await submitHumanDecision(detail.value.id, { ...payload, auto_resume: true })
    toast.success('已提交，工作流继续执行')
    detail.value = null
    await load()
  } catch (e) {
    if (e.fieldErrors) {
      toast.error(`表单校验未通过：${Object.entries(e.fieldErrors).map(([k, v]) => `${k}: ${v}`).join('；')}`)
    } else {
      toast.error(e.message || '提交失败')
    }
  } finally {
    submitting.value = false
  }
}

// 批量处理
async function runBatch(decision) {
  if (!selectedIds.value.length) return
  if (decision === 'rejected' && !batchComment.value.trim()) {
    toast.info('批量驳回请填写理由')
    return
  }
  batchBusy.value = true
  try {
    const res = await batchDecideHumanTasks({
      task_ids: selectedIds.value,
      decision,
      comment: batchComment.value.trim(),
      operator: batchOperator.value.trim(),
      auto_resume: true,
    })
    const okN = (res.succeeded || []).length
    const failN = (res.failed || []).length
    if (okN) toast.success(`已处理 ${okN} 条${failN ? `，${failN} 条失败` : ''}`)
    if (failN) {
      const first = (res.failed || [])[0]
      toast.info(`失败示例：${first?.reason || '未知原因'}`)
    }
    batchComment.value = ''
    if (batchOperator.value.trim()) localStorage.setItem('ks_human_operator', batchOperator.value.trim())
    await load()
  } catch (e) {
    toast.error(e.message || '批量处理失败')
  } finally {
    batchBusy.value = false
  }
}

function gotoRun(t) {
  router.push(`/workflows/${t.workflow_id}`)
}

onMounted(load)

// 待办数由 SSE 推送变化时自动刷新列表（事件驱动，无轮询）
watch(
  () => notifications.value?.human_tasks ?? 0,
  (n, old) => { if (n !== old) load(true) },
)
</script>

<template>
  <div class="page">
    <header class="page-head">
      <h2>人工任务待办</h2>
      <div class="tabs">
        <button v-for="t in STATUS_TABS" :key="t.key" class="tab" :class="{ on: status === t.key }"
                @click="switchStatus(t.key)">{{ t.label }}</button>
      </div>
      <span class="spacer"></span>
      <button class="btn" @click="load" :disabled="loading">↻ 刷新</button>
    </header>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="!tasks.length" class="empty">
      {{ isPendingTab ? '暂无待处理的任务' : '该状态下没有任务' }}
    </div>

    <template v-else>
      <table class="tbl">
        <thead>
          <tr>
            <th v-if="isPendingTab" class="c-chk">
              <input type="checkbox" :checked="allBatchableSelected" @change="toggleSelectAll" title="全选审批类任务" />
            </th>
            <th>任务</th>
            <th class="c-wf">所属工作流</th>
            <th class="c-mode">模式</th>
            <th class="c-src">触发</th>
            <th class="c-time">创建时间</th>
            <th v-if="!isPendingTab" class="c-res">结果</th>
            <th class="c-op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id" :class="{ overdue: isOverdue(t) }">
            <td v-if="isPendingTab" class="c-chk">
              <input v-if="t.mode !== 'form'" type="checkbox" :checked="selected.has(t.id)" @change="toggleSelect(t.id)" />
              <span v-else class="no-batch" title="表单任务需逐条填写，不支持批量">—</span>
            </td>
            <td>
              <span class="t-title">👤 {{ t.node_title || t.node_id }}</span>
              <span class="t-id">#{{ t.id }}</span>
              <span v-if="isOverdue(t)" class="badge overdue">已超时</span>
            </td>
            <td class="c-wf">{{ t.workflow_name || '—' }}</td>
            <td class="c-mode">
              <span class="badge" :class="t.mode === 'form' ? 'form' : 'approve'">
                {{ t.mode === 'form' ? '表单' : '审批' }}
              </span>
            </td>
            <td class="c-src">{{ t.trigger_source === 'schedule' ? '定时' : '手动' }}</td>
            <td class="c-time">{{ fmtTime(t.created_at) }}</td>
            <td v-if="!isPendingTab" class="c-res">
              <span class="badge" :class="'r-' + t.status">{{ DECISION_LABEL[t.status] || t.status }}</span>
              <div v-if="t.operator" class="res-op">{{ t.operator }}</div>
              <div v-if="t.comment" class="res-cm" :title="t.comment">{{ t.comment }}</div>
            </td>
            <td class="c-op">
              <button v-if="isPendingTab" class="btn sm primary" @click="openDetail(t.id)">处理</button>
              <button v-else class="btn sm" @click="openDetail(t.id)">查看</button>
              <button class="btn sm" @click="gotoRun(t)">工作流</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 批量操作条 -->
      <div v-if="isPendingTab" class="batch-bar">
        <span class="bb-count">已选 {{ selectedIds.length }} 条</span>
        <span v-if="batchableTasks.length !== tasks.length" class="bb-tip">（表单任务不支持批量）</span>
        <span class="spacer"></span>
        <input type="text" v-model="batchComment" placeholder="统一处理意见（批量驳回必填）" class="bb-comment" />
        <input type="text" v-model="batchOperator" placeholder="处理人" class="bb-operator" />
        <button class="btn sm primary" :disabled="!selectedIds.length || batchBusy" @click="runBatch('approved')">批量通过</button>
        <button class="btn sm danger" :disabled="!selectedIds.length || batchBusy" @click="runBatch('rejected')">批量驳回</button>
      </div>
    </template>

    <!-- 详情 / 处理抽屉 -->
    <div v-if="detail" class="mask" @click.self="detail = null">
      <div class="drawer">
        <header class="drawer-head">
          <h3>👤 {{ detail.node_title || detail.node_id }}</h3>
          <span class="badge" :class="detail.mode === 'form' ? 'form' : 'approve'">
            {{ detail.mode === 'form' ? '表单' : '审批' }}
          </span>
          <span v-if="isOverdue(detail)" class="badge overdue">已超时</span>
          <span class="spacer"></span>
          <button class="btn sm" @click="gotoRun(detail)">打开工作流</button>
          <button class="icon-btn" @click="detail = null">×</button>
        </header>

        <div class="drawer-meta">
          <span>{{ detail.workflow_name || '—' }}</span>
          <span>创建：{{ fmtTime(detail.created_at) }}</span>
          <span v-if="detail.due_at">截止：{{ fmtTime(detail.due_at) }}</span>
          <span v-if="detail.assignee">指定：{{ detail.assignee }}</span>
        </div>

        <!-- 未处理：展示表单 -->
        <HumanTaskForm
          v-if="detail.status === 'pending'"
          :mode="detail.mode"
          :description="detail.description"
          :form-data="detail.form_data"
          :form-fields="(detail.form_schema || {}).form_fields || []"
          :decisions="(detail.form_schema || {}).decisions || []"
          :submit-text="(detail.form_schema || {}).submit_text || '提交'"
          :comment-label="(detail.form_schema || {}).comment?.label || '处理意见'"
          :comment-placeholder="(detail.form_schema || {}).comment?.placeholder || ''"
          :comment-required="!!detail.comment_required"
          :task-id="detail.id"
          :assignee="detail.assignee"
          :due-at="detail.due_at"
          :submitting="submitting"
          @submit="handleSubmit"
        />

        <!-- 已处理：回看（待审快照 + 填写内容 + 决策） -->
        <div v-else class="review">
          <div class="rv-block" v-if="detail.description">
            <div class="rv-k">处理说明</div>
            <div class="rv-v">{{ detail.description }}</div>
          </div>
          <div class="rv-block" v-if="Object.keys(detail.form_data || {}).length">
            <div class="rv-k">当时的待审内容</div>
            <div v-for="(v, k) in detail.form_data" :key="k" class="rv-row">
              <span class="rv-kk">{{ k }}</span>
              <pre class="rv-vv">{{ typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v) }}</pre>
            </div>
          </div>
          <div class="rv-block" v-if="Object.keys(detail.filled_data || {}).length">
            <div class="rv-k">填写内容</div>
            <div v-for="(v, k) in detail.filled_data" :key="k" class="rv-row">
              <span class="rv-kk">{{ k }}</span>
              <span class="rv-vv">{{ v }}</span>
            </div>
          </div>
          <div class="rv-block">
            <div class="rv-k">处理结果</div>
            <div class="rv-v">
              <span class="badge" :class="'r-' + detail.status">{{ DECISION_LABEL[detail.status] || detail.status }}</span>
              <span v-if="detail.operator"> · {{ detail.operator }}</span>
              <span v-if="detail.decided_at"> · {{ fmtTime(detail.decided_at) }}</span>
            </div>
            <div v-if="detail.comment" class="rv-cm">{{ detail.comment }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 18px 20px 60px; }
.page-head { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; flex-wrap: wrap; }
.page-head h2 { margin: 0; font-size: 17px; }
.spacer { flex: 1; }

.tabs { display: flex; gap: 4px; }
.tab {
  padding: 4px 12px; border-radius: 999px; cursor: pointer; font-size: 12px;
  border: 1px solid var(--c-border); background: transparent; color: var(--c-secondary);
}
.tab.on { background: var(--c-accent); color: #fff; border-color: var(--c-accent); }

.empty { padding: 40px; text-align: center; color: var(--c-secondary); font-size: 13px; }

.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th, .tbl td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--c-border); vertical-align: middle; }
.tbl th { font-size: 11.5px; color: var(--c-secondary); font-weight: 600; }
.tbl tr.overdue td { background: rgba(220, 38, 38, .06); }
.tbl tr:hover td { background: var(--c-muted); }

.c-chk { width: 34px; }
.c-wf { width: 150px; color: var(--c-secondary); }
.c-mode { width: 62px; }
.c-src { width: 52px; color: var(--c-secondary); }
.c-time { width: 150px; color: var(--c-secondary); font-size: 11.5px; }
.c-res { width: 200px; }
.c-op { width: 118px; white-space: nowrap; }

.t-title { font-weight: 600; }
.t-id { margin-left: 6px; font-family: ui-monospace, monospace; font-size: 10.5px; color: var(--c-secondary); }
.no-batch { color: var(--c-secondary); }

.badge {
  display: inline-block; padding: 1px 7px; border-radius: 999px;
  font-size: 10.5px; font-weight: 600; border: 1px solid transparent;
}
.badge.approve { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 14%, transparent); border-color: var(--c-accent); }
.badge.form { color: #7c3aed; background: color-mix(in srgb, #7c3aed 14%, transparent); border-color: #7c3aed; }
.badge.overdue { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 14%, transparent); border-color: var(--c-danger); margin-left: 6px; }
.badge.r-approved { color: var(--c-success); background: color-mix(in srgb, var(--c-success) 14%, transparent); border-color: var(--c-success); }
.badge.r-rejected { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 14%, transparent); border-color: var(--c-danger); }
.badge.r-submitted { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 14%, transparent); border-color: var(--c-accent); }
.badge.r-cancelled { color: var(--c-secondary); background: var(--c-muted); border-color: var(--c-border); }

.res-op { font-size: 11px; color: var(--c-secondary); margin-top: 2px; }
.res-cm {
  font-size: 11px; color: var(--c-secondary); margin-top: 2px; line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.batch-bar {
  display: flex; align-items: center; gap: 8px; margin-top: 12px; padding: 9px 12px;
  border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-panel);
  position: sticky; bottom: 12px;
}
.bb-count { font-size: 12px; font-weight: 600; }
.bb-tip { font-size: 11px; color: var(--c-secondary); }
.bb-comment { flex: 1; min-width: 160px; }
.bb-operator { width: 110px; }
.batch-bar input {
  padding: 4px 8px; border-radius: 5px; font-size: 12px; font-family: inherit;
  border: 1px solid var(--c-border-strong, #d8cdbb); background: var(--c-bg); color: var(--c-fg);
}

.btn { padding: 5px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; border: 1px solid var(--c-border); background: transparent; color: var(--c-fg); }
.btn:hover { background: var(--c-muted); }
.btn.sm { padding: 3px 9px; font-size: 11px; }
.btn.primary { background: var(--c-accent); color: #fff; border-color: transparent; }
.btn.danger { background: var(--c-danger); color: #fff; border-color: transparent; }
.btn:disabled { opacity: .55; cursor: not-allowed; }

.mask { position: fixed; inset: 0; background: rgba(0,0,0,.42); display: flex; justify-content: flex-end; z-index: 60; }
.drawer {
  width: 460px; max-width: 94vw; height: 100%; overflow-y: auto; padding: 16px 18px 40px;
  background: var(--c-bg); border-left: 1px solid var(--c-border);
}
.drawer-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.drawer-head h3 { margin: 0; font-size: 15px; }
.icon-btn {
  width: 26px; height: 26px; border-radius: 6px; cursor: pointer; font-size: 16px; line-height: 1;
  border: 1px solid var(--c-border); background: transparent; color: var(--c-secondary);
}
.drawer-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11px; color: var(--c-secondary); margin-bottom: 12px; }

.review { display: flex; flex-direction: column; gap: 12px; }
.rv-block { display: flex; flex-direction: column; gap: 5px; }
.rv-k { font-size: 11px; font-weight: 700; color: var(--c-accent); }
.rv-v { font-size: 12px; line-height: 1.6; }
.rv-row { display: grid; grid-template-columns: 110px 1fr; gap: 8px; align-items: start; }
.rv-kk { font-size: 11.5px; font-weight: 600; }
.rv-vv {
  margin: 0; font-size: 11.5px; line-height: 1.5; white-space: pre-wrap; word-break: break-word;
  font-family: ui-monospace, monospace; max-height: 200px; overflow-y: auto;
}
.rv-cm { font-size: 12px; color: var(--c-secondary); line-height: 1.6; margin-top: 4px; }
</style>
