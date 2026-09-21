<script setup>
// 评测任务：发起评测（配置快照留存）+ 任务列表（进度/指标均值/消融对比）
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fetchKbs } from '../../api'
import { useToast } from '../../composables/useToast'
import ModalDialog from '../common/ModalDialog.vue'
import Pagination from '../common/Pagination.vue'
import {
  fetchEvalRuns, createEvalRun, cancelEvalRun, deleteEvalRun, fetchEvalLlmOptions, fetchTestsets,
  exportEvalReport, downloadBlob, connectRunStream,
} from '../../api/eval'

const props = defineProps({ preselectTestsetId: { type: String, default: '' } })
const emit = defineEmits(['view-detail'])
const toast = useToast()

const runs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(true)
const kbs = ref([])
const llmOptions = ref([])
const testsets = ref([])

const dialog = ref({
  visible: false, loading: false,
  testset_id: '', kb_id: '', name: '',
  metrics: ['faithfulness', 'response_relevancy', 'context_precision', 'context_recall'],
  ablation: { BM25_ENABLED: true, QUERY_REWRITE_ENABLED: true, RERANK_ENABLED: true },
  llm_config_id: '',
})

const METRIC_OPTIONS = [
  { value: 'faithfulness', label: 'Faithfulness 忠实度' },
  { value: 'response_relevancy', label: 'Answer Relevancy 答案相关性' },
  { value: 'context_precision', label: 'Context Precision 上下文精确率' },
  { value: 'context_recall', label: 'Context Recall 上下文召回率' },
  { value: 'factual_correctness', label: 'Factual Correctness 事实正确性' },
  { value: 'noise_sensitivity', label: 'Noise Sensitivity 噪声敏感度' },
]
const ABLATION_LABELS = { BM25_ENABLED: 'BM25 关键词召回', QUERY_REWRITE_ENABLED: '查询改写', RERANK_ENABLED: 'Rerank 精排' }

const STATUS_LABEL = { pending: '排队中', running: '执行中', scoring: '评分中', done: '已完成', failed: '失败', cancelled: '已取消' }

// 采集/评分进行中的任务（scoring = 采集完正在跑 ragas 评分，需数分钟）
const isActive = (r) => r.status === 'running' || r.status === 'pending' || r.status === 'scoring'
const hasRunning = computed(() => runs.value.some(isActive))

let runStream = null
let tickTimer = null
const nowTick = ref(Date.now())   // 每秒跳一次，驱动进行中任务的「已运行」时长刷新

onMounted(async () => {
  await Promise.all([loadRuns(), loadOptions()])
  // 事件驱动刷新：后端广播进度/状态事件才拉列表，空闲零请求（保留手动刷新兜底）
  runStream = connectRunStream({ onEvent: () => loadRuns(true) })
  tickTimer = setInterval(() => { nowTick.value = Date.now() }, 1000)
})
onBeforeUnmount(() => { runStream?.close(); runStream = null; if (tickTimer) clearInterval(tickTimer) })
watch(page, loadRuns)

async function loadRuns(silent = false) {
  if (!silent) loading.value = true
  try {
    const res = await fetchEvalRuns({ page: page.value, page_size: pageSize })
    runs.value = res.items
    total.value = res.total
  } catch (e) { if (!silent) toast.error(e.message) }
  loading.value = false
}

async function loadOptions() {
  try {
    const [kbsList, llms] = await Promise.all([
      fetchKbs().catch(() => []),
      fetchEvalLlmOptions(),
    ])
    kbs.value = kbsList
    llmOptions.value = llms
  } catch (e) { /* 非阻塞 */ }
}

watch(() => props.preselectTestsetId, (id) => { if (id) openDialog(id) })

function openDialog(testsetId = '') {
  dialog.value = {
    ...dialog.value,
    visible: true,
    testset_id: testsetId || dialog.value.testset_id,
  }
  loadTestsetsForDialog()
}

async function loadTestsetsForDialog() {
  try {
    testsets.value = await fetchTestsets()
  } catch (e) { toast.error(e.message) }
}

async function submit() {
  const d = dialog.value
  if (!d.testset_id) { toast.error('请选择评测集'); return }
  if (!d.metrics.length) { toast.error('请至少选择一个指标'); return }
  d.loading = true
  try {
    await createEvalRun({
      testset_id: d.testset_id,
      kb_id: d.kb_id,
      name: d.name.trim(),
      metrics: d.metrics,
      ablation: d.ablation,
      llm_config_id: d.llm_config_id,
    })
    toast.success('评测任务已启动')
    d.visible = false
    page.value = 1
    await loadRuns()
  } catch (e) { toast.error(e.message) }
  d.loading = false
}

async function doCancel(r) {
  try {
    await cancelEvalRun(r.id)
    toast.success('已取消（当前条目跑完后停止）')
    await loadRuns(true)
  } catch (e) { toast.error(e.message) }
}

async function doDelete(r) {
  if (!confirm(`确认删除任务「${r.name}」？其全部评测结果将一并删除，不可恢复。`)) return
  try {
    await deleteEvalRun(r.id)
    toast.success('已删除')
    await loadRuns(true)
  } catch (e) { toast.error(e.message) }
}

async function downloadReport(r) {
  try {
    const blob = await exportEvalReport(r.id)
    downloadBlob(blob, `eval_report_${r.name}.csv`)
  } catch (e) { toast.error(e.message) }
}

function fmtTime(ts) {
  if (!ts) return ''
  return String(ts).replace('T', ' ').slice(5, 19)
}
function fmtDuration(s) {
  s = Math.round(s)
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} 分 ${s % 60} 秒`
  return `${Math.floor(m / 60)} 时 ${m % 60} 分`
}
// 耗时：完成/失败/取消 = started_at→finished_at；进行中 = started_at→现在（随 tick 跳动）
function durationText(r) {
  if (!r.started_at) return ''
  const start = new Date(r.started_at).getTime()
  const end = r.finished_at ? new Date(r.finished_at).getTime()
    : (isActive(r) ? nowTick.value : 0)
  if (!end || end <= start) return ''
  return fmtDuration((end - start) / 1000)
}
function pct(r) { return r.total ? Math.round((r.done / r.total) * 100) : 0 }
function scoreClass(v) { return v >= 0.8 ? 'good' : v >= 0.6 ? 'mid' : 'bad' }
function ablationText(r) {
  const a = r.config?.ablation || {}
  const off = Object.entries(a).filter(([, v]) => !v).map(([k]) => ABLATION_LABELS[k] || k)
  return off.length ? `关闭：${off.join('、')}` : '全开'
}
</script>

<template>
  <div class="run-wrap">
    <div class="card">
      <div class="bar">
        <button class="btn primary" @click="openDialog()">+ 发起评测</button>
        <button class="btn" :disabled="loading" @click="loadRuns(true)">刷新</button>
        <span v-if="hasRunning" class="hint">评测执行中，进度实时推送</span>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!runs.length" class="empty">暂无评测任务，点击「发起评测」开始</div>
      <table v-else class="tbl">
        <thead>
          <tr>
            <th>任务</th><th>评测集</th><th>状态 / 进度</th>
            <th>指标均值</th><th>消融配置</th><th>时间 / 耗时</th><th style="width:215px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id">
            <td>
              <strong>{{ r.name }}</strong>
              <div class="sub" v-if="r.config?.llm_config_id">
                评估模型：{{ llmOptions.find(l => l.id === r.config.llm_config_id)?.name || r.config.llm_config_id }}
              </div>
              <div class="sub" v-else>评估模型：跟随生效配置</div>
            </td>
            <td>{{ r.testset_name || r.testset_id }}</td>
            <td>
              <span class="tag" :class="r.status">{{ STATUS_LABEL[r.status] || r.status }}</span>
              <div class="progress" v-if="isActive(r)">
                <div class="progress-inner" :style="{ width: pct(r) + '%' }"></div>
              </div>
              <div class="sub" v-if="r.total">{{ r.done }}/{{ r.total }}<template v-if="r.failed_count"> · 失败 {{ r.failed_count }}</template></div>
              <div class="sub" v-if="r.status === 'scoring'">ragas 评分中（逐指标打 LLM，需数分钟）…</div>
            </td>
            <td>
              <template v-if="Object.keys(r.metrics || {}).length">
                <span v-for="(v, k) in r.metrics" :key="k" class="score-chip" :class="scoreClass(v)">{{ k }} {{ v }}</span>
              </template>
              <span v-else class="dim">—</span>
            </td>
            <td class="sub">{{ ablationText(r) }}</td>
            <td class="dim">
              {{ fmtTime(r.created_at) }}
              <div v-if="durationText(r)" class="sub">
                {{ r.finished_at ? `耗时 ${durationText(r)}` : `已运行 ${durationText(r)}` }}
              </div>
            </td>
            <td>
              <button class="btn sm" :disabled="r.status !== 'done'" @click="emit('view-detail', r.id)">结果</button>
              <button class="btn sm" :disabled="r.status !== 'done'" @click="downloadReport(r)">报告</button>
              <button v-if="isActive(r)" class="btn sm danger" @click="doCancel(r)">取消</button>
              <button v-else class="btn sm danger" @click="doDelete(r)">删除</button>
              <span v-if="r.error" class="err" :title="r.error">{{ r.error.slice(0, 18) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <Pagination v-model:page="page" :total="total" :page-size="pageSize" />
    </div>

    <!-- 发起评测弹窗 -->
    <ModalDialog v-model="dialog.visible" title="发起评测" size="lg">
      <div class="form">
        <label>评测集 <em>*</em></label>
        <select v-model="dialog.testset_id" class="ipt">
          <option value="">请选择…</option>
          <option v-for="s in testsets" :key="s.id" :value="s.id">{{ s.name }}（{{ s.item_count }} 条）</option>
        </select>
        <label>知识库</label>
        <select v-model="dialog.kb_id" class="ipt">
          <option value="">用评测集默认知识库</option>
          <option v-for="k in kbs" :key="k.id" :value="k.id">{{ k.name }}</option>
        </select>
        <label>任务名（可选）</label>
        <input v-model="dialog.name" class="ipt" placeholder="缺省：评测集名·时间" />
        <label>评测指标 <em>*</em></label>
        <div class="checks">
          <label v-for="m in METRIC_OPTIONS" :key="m.value" class="check">
            <input type="checkbox" :value="m.value" v-model="dialog.metrics" />
            {{ m.label }}
          </label>
        </div>
        <label>消融开关（跑分时临时关闭，不影响线上问答）</label>
        <div class="checks">
          <label v-for="(label, key) in ABLATION_LABELS" :key="key" class="check">
            <input type="checkbox" v-model="dialog.ablation[key]" /> {{ label }}
          </label>
        </div>
        <label>评估模型（ragas 评分用 LLM）</label>
        <select v-model="dialog.llm_config_id" class="ipt">
          <option value="">跟随生效配置</option>
          <option v-for="l in llmOptions" :key="l.id" :value="l.id">
            {{ l.name }}（{{ l.model }}）{{ l.is_active ? ' · 生效中' : '' }}
          </option>
        </select>
      </div>
      <template #footer>
        <button class="btn" @click="dialog.visible = false">取消</button>
        <button class="btn primary" :disabled="dialog.loading" @click="submit">启动评测</button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.run-wrap { min-height: 0; }
.card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius); padding: 12px; overflow: auto; }
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.hint { font-size: 12px; color: var(--c-secondary); }
.sub { font-size: 12px; color: var(--c-secondary); margin-top: 2px; }
.dim { color: var(--c-secondary); font-size: 12px; }
.empty { padding: 40px 0; text-align: center; color: var(--c-secondary); }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; padding: 8px; border-bottom: 1px solid var(--c-border); color: var(--c-secondary); font-weight: 500; white-space: nowrap; }
.tbl td { padding: 8px; border-bottom: 1px solid var(--c-border); vertical-align: top; }
.btn.sm { padding: 3px 8px; margin-right: 4px; }
.btn.danger { color: var(--c-danger) !important; }
.ipt { padding: 5px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); font-size: 12px; width: 100%; box-sizing: border-box; background: var(--c-panel); color: var(--c-fg); }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; }
.tag.done { background: color-mix(in srgb, var(--c-accent) 12%, transparent); color: var(--c-accent); }
.tag.running { background: rgba(24, 144, 255, 0.12); color: #1890ff; }
.tag.scoring { background: rgba(114, 46, 209, 0.12); color: #9254de; }
.tag.pending { background: rgba(24, 144, 255, 0.08); color: #69b1ff; }
.tag.failed { background: rgba(224, 82, 82, 0.12); color: var(--c-danger); }
.tag.cancelled { background: var(--c-muted); color: var(--c-secondary); }
.progress { width: 140px; height: 4px; background: var(--c-muted); border-radius: 2px; margin-top: 6px; }
.progress-inner { height: 100%; background: var(--c-accent); border-radius: 2px; transition: width 0.5s; }
.score-chip { display: inline-block; font-size: 11px; margin: 1px 6px 1px 0; padding: 1px 6px; border-radius: 4px; }
.score-chip.good { background: color-mix(in srgb, var(--c-accent) 10%, transparent); color: var(--c-accent); }
.score-chip.mid { background: rgba(240, 160, 20, 0.12); color: #d48806; }
.score-chip.bad { background: rgba(224, 82, 82, 0.1); color: var(--c-danger); }
.err { color: var(--c-danger); font-size: 11px; margin-left: 4px; cursor: help; }
.form { display: flex; flex-direction: column; gap: 6px; }
.form label { font-size: 12px; color: var(--c-secondary); margin-top: 6px; }
.form label em { color: var(--c-danger); font-style: normal; }
.checks { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
.check { display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 0 !important; font-weight: normal; cursor: pointer; }
</style>
