<script setup>
// 结果详情：指标卡片（含同评测集历史对比）+ 逐条结果（分数/详情/badcase标记）+ 批量回流
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useToast } from '../../composables/useToast'
import ModalDialog from '../common/ModalDialog.vue'
import Pagination from '../common/Pagination.vue'
import {
  fetchEvalRun, fetchEvalRunItems, fetchEvalRunItem, fetchEvalRuns,
  markBadcase, markBadcaseBatch, backflowBadcases, fetchTestsets,
} from '../../api/eval'

const props = defineProps({ runId: { type: String, default: '' } })
const emit = defineEmits(['back'])
const toast = useToast()

const run = ref(null)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filter = ref('all')
const loading = ref(true)
const expanded = ref(null)         // 展开的行（含 contexts 详情）
const expandedDetail = ref(null)
const selected = ref([])           // 勾选的 run_item id
const backflowDialog = ref({ visible: false, loading: false, target_testset_id: '' })
const testsets = ref([])
const prevMetrics = ref(null)      // 同评测集上一次 done 任务均值（对比）
let pollTimer = null

const REASON_LABEL = {
  retrieval_miss: '检索未召回', bad_ranking: '排序差', hallucination: '答案幻觉',
  off_topic: '答非所问', wrong_label: '标注错误', other: '其他',
}
const FILTERS = [
  { value: 'all', label: '全部' },
  { value: 'badcase', label: '已标记' },
  { value: 'error', label: '执行失败' },
  { value: 'low_score', label: '低分' },
]

const metricEntries = computed(() => Object.entries(run.value?.metrics || {}))
const canBackflow = computed(() => selected.value.length > 0)
const isDone = computed(() => run.value?.status === 'done')

onMounted(() => { if (props.runId) load() })
onBeforeUnmount(stopPoll)
watch(() => props.runId, () => { if (props.runId) load() })
watch([filter, page], () => { if (props.runId) loadItems() })
watch(isDone, v => { if (v) stopPoll(); else startPoll() })

function startPoll() {
  stopPoll()
  pollTimer = setInterval(() => { if (!isDone.value && props.runId) loadRun(true) }, 3000)
}
function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

async function load(silent = false) {
  if (!silent) loading.value = true
  await Promise.all([loadRun(true), loadItems(), loadPrevMetrics()])
  if (run.value?.status === 'running' || run.value?.status === 'pending') startPoll()
  loading.value = false
}

async function loadRun(silent = false) {
  if (!props.runId) return
  try {
    run.value = await fetchEvalRun(props.runId)
  } catch (e) { if (!silent) toast.error(e.message) }
}

async function loadItems() {
  if (!props.runId) { items.value = []; total.value = 0; return }
  try {
    const res = await fetchEvalRunItems(props.runId, {
      filter: filter.value, page: page.value, page_size: pageSize,
    })
    items.value = res.items
    total.value = res.total
    selected.value = selected.value.filter(id => res.items.some(it => it.id === id))
  } catch (e) { toast.error(e.message) }
}

async function loadPrevMetrics() {
  prevMetrics.value = null
  try {
    const res = await fetchEvalRuns({ testset_id: run.value?.testset_id || '', page: 1, page_size: 50 })
    const prev = res.items.find(r => r.status === 'done' && r.id !== props.runId && Object.keys(r.metrics || {}).length)
    prevMetrics.value = prev?.metrics || null
  } catch { /* 对比失败不阻塞 */ }
}

async function toggleExpand(it) {
  if (expanded.value === it.id) { expanded.value = null; return }
  expanded.value = it.id
  expandedDetail.value = null
  try {
    expandedDetail.value = await fetchEvalRunItem(props.runId, it.id)
  } catch (e) { toast.error(e.message) }
}

async function mark(it, isBadcase, reason = 'other') {
  try {
    await markBadcase(props.runId, it.id, { is_badcase: isBadcase, badcase_reason: reason })
    it.is_badcase = isBadcase
    it.badcase_reason = isBadcase ? reason : ''
    toast.success(isBadcase ? `已标记 Badcase（${REASON_LABEL[reason]}），可在结果详情回流到评测集` : '已取消标记')
    loadRun(true)
  } catch (e) { toast.error(e.message) }
}

async function markFromSelect(it, e) {
  const reason = e.target.value
  e.target.selectedIndex = 0
  if (reason) await mark(it, true, reason)
}

async function markSelected() {
  try {
    const res = await markBadcaseBatch(props.runId, selected.value, { is_badcase: true })
    toast.success(`已批量标记 ${res.marked} 条`)
    selected.value = []
    await loadItems()
  } catch (e) { toast.error(e.message) }
}

async function openBackflow() {
  try {
    testsets.value = await fetchTestsets()
  } catch (e) { toast.error(e.message) }
  backflowDialog.value = {
    visible: true, loading: false,
    target_testset_id: run.value?.testset_id || '',
  }
}

async function doBackflow() {
  const d = backflowDialog.value
  d.loading = true
  try {
    const res = await backflowBadcases(props.runId, {
      target_testset_id: d.target_testset_id,
      run_item_ids: selected.value,
    })
    toast.success(`回流完成：新增 ${res.flowed} 条${res.skipped.length ? `，重复跳过 ${res.skipped.length} 条` : ''}`)
    d.visible = false
    selected.value = []
  } catch (e) { toast.error(e.message) }
  d.loading = false
}

function scoreClass(v) { return v >= 0.8 ? 'good' : v >= 0.6 ? 'mid' : 'bad' }
function delta(cur, prev) {
  const d = (cur - prev).toFixed(3)
  const n = parseFloat(d)
  return { text: `${n >= 0 ? '+' : ''}${d}`, cls: n >= 0 ? 'up' : 'down' }
}
function fmtTime(ts) { return ts ? String(ts).replace('T', ' ').slice(5, 19) : '' }
</script>

<template>
  <div class="rd-wrap" v-if="run">
    <div class="card head">
      <div class="bar">
        <button class="btn" @click="emit('back')">← 返回任务列表</button>
        <strong>{{ run.name }}</strong>
        <span class="tag" :class="run.status">{{ run.status }}</span>
        <span class="sub" v-if="run.total">{{ run.done }}/{{ run.total }}</span>
        <span class="spacer"></span>
        <button class="btn" @click="load()">刷新</button>
      </div>

      <!-- 指标卡片（与同评测集历史 run 对比） -->
      <div class="metric-cards" v-if="metricEntries.length">
        <div v-for="[k, v] in metricEntries" :key="k" class="metric-card">
          <div class="metric-name">{{ k }}</div>
          <div class="metric-val" :class="scoreClass(v)">{{ v.toFixed(3) }}</div>
          <div v-if="prevMetrics && prevMetrics[k] != null" class="metric-delta" :class="delta(v, prevMetrics[k]).cls">
            {{ delta(v, prevMetrics[k]).text }}
          </div>
        </div>
      </div>
      <div v-else class="sub">
        {{ run.status === 'done' ? '无指标结果（评分失败或全部条目执行失败）' : '评测进行中，完成后展示指标…' }}
      </div>
      <div v-if="run.error" class="err-banner">{{ run.error }}</div>
    </div>

    <div class="card">
      <div class="bar">
        <label v-for="f in FILTERS" :key="f.value" class="radio">
          <input type="radio" :value="f.value" v-model="filter" /> {{ f.label }}
        </label>
        <span class="spacer"></span>
        <span class="sub" v-if="selected.length">已选 {{ selected.length }} 条</span>
        <button class="btn" :disabled="!canBackflow" @click="markSelected">批量标记Badcase</button>
        <button class="btn primary" :disabled="!canBackflow" @click="openBackflow">回流到评测集</button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">暂无结果</div>
      <table v-else class="tbl">
        <thead>
          <tr>
            <th style="width:32px"><input type="checkbox" :checked="selected.length && selected.length === items.length"
              @change="e => selected = e.target.checked ? items.map(i => i.id) : []" /></th>
            <th style="width:40%">问题 / 答案</th>
            <th>指标得分</th><th>耗时</th><th>Badcase</th><th style="width:80px"></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="it in items" :key="it.id">
            <tr :class="{ marked: it.is_badcase, errored: it.error }">
              <td><input type="checkbox" :checked="selected.includes(it.id)"
                @change="e => { e.target.checked ? selected.push(it.id) : selected = selected.filter(x => x !== it.id) }" /></td>
              <td>
                <div class="q" :title="it.question">{{ it.question }}</div>
                <div class="a" :title="it.answer">{{ it.answer || (it.error ? `⚠ ${it.error}` : '…') }}</div>
              </td>
              <td>
                <template v-if="Object.keys(it.metric_scores).length">
                  <span v-for="(v, k) in it.metric_scores" :key="k" class="score-chip" :class="scoreClass(v)">
                    {{ k.replace('relevancy', '_rel').slice(0, 14) }} {{ v ?? '—' }}
                  </span>
                </template>
                <span v-else class="dim">—</span>
              </td>
              <td class="dim">{{ it.latency_s }}s</td>
              <td>
                <template v-if="it.is_badcase">
                  <span class="tag flow">{{ REASON_LABEL[it.badcase_reason] || it.badcase_reason }}</span>
                  <button class="btn sm" @click="mark(it, false)">取消</button>
                </template>
                <select v-else class="ipt sel" @change="e => markFromSelect(it, e)">
                  <option value="">标记为…</option>
                  <option v-for="(label, key) in REASON_LABEL" :key="key" :value="key">{{ label }}</option>
                </select>
              </td>
              <td><button class="btn sm" @click="toggleExpand(it)">{{ expanded === it.id ? '收起' : '详情' }}</button></td>
            </tr>
            <tr v-if="expanded === it.id" class="expand-row">
              <td></td>
              <td colspan="5">
                <div v-if="!expandedDetail" class="sub">加载中…</div>
                <template v-else>
                  <div class="sec"><strong>标准答案（reference）</strong><p>{{ expandedDetail.reference || '（无标注）' }}</p></div>
                  <div class="sec">
                    <strong>检索上下文（{{ expandedDetail.contexts?.length || 0 }} 条）</strong>
                    <div v-for="(c, i) in expandedDetail.contexts" :key="i" class="ctx">
                      <span class="ctx-path">{{ expandedDetail.retrieval_paths?.[i] || 'n/a' }}</span>
                      <span class="ctx-text">{{ (c || '').slice(0, 200) }}{{ (c || '').length > 200 ? '…' : '' }}</span>
                    </div>
                    <div v-if="!expandedDetail.contexts?.length" class="dim">（空——检索未召回了任何内容）</div>
                  </div>
                </template>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <Pagination v-model:page="page" :total="total" :page-size="pageSize" />
    </div>

    <!-- 回流确认弹窗 -->
    <ModalDialog v-model="backflowDialog.visible" title="Badcase 回流" size="sm">
      <p class="sub">将已标记的 {{ selected.length }} 条 Badcase 保存为评测集条目（来源标记「Badcase回流」，可溯源到本条结果）。重复问题自动跳过。</p>
      <div class="form">
        <label>目标评测集</label>
        <select v-model="backflowDialog.target_testset_id" class="ipt">
          <option v-for="s in testsets" :key="s.id" :value="s.id">{{ s.name }}（{{ s.item_count }} 条）</option>
        </select>
      </div>
      <template #footer>
        <button class="btn" @click="backflowDialog.visible = false">取消</button>
        <button class="btn primary" :disabled="backflowDialog.loading" @click="doBackflow">回流</button>
      </template>
    </ModalDialog>
  </div>
  <div v-else class="empty">加载中…</div>
</template>

<style scoped>
.rd-wrap { display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius); padding: 12px; overflow: auto; }
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.spacer { flex: 1; }
.sub { font-size: 12px; color: var(--c-secondary); }
.dim { color: var(--c-secondary); font-size: 12px; }
.empty { padding: 40px 0; text-align: center; color: var(--c-secondary); }
.metric-cards { display: flex; gap: 12px; flex-wrap: wrap; }
.metric-card { min-width: 130px; padding: 10px 14px; border: 1px solid var(--c-border); border-radius: var(--radius); }
.metric-name { font-size: 11px; color: var(--c-secondary); }
.metric-val { font-size: 20px; font-weight: 600; margin-top: 2px; }
.metric-val.good { color: var(--c-accent); }
.metric-val.mid { color: #d48806; }
.metric-val.bad { color: var(--c-danger); }
.metric-delta { font-size: 11px; margin-top: 2px; }
.metric-delta.up { color: var(--c-accent); }
.metric-delta.down { color: var(--c-danger); }
.err-banner { margin-top: 8px; padding: 6px 10px; background: rgba(224, 82, 82, 0.08); border-radius: 6px; color: var(--c-danger); font-size: 12px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; padding: 8px; border-bottom: 1px solid var(--c-border); color: var(--c-secondary); font-weight: 500; white-space: nowrap; }
.tbl td { padding: 8px; border-bottom: 1px solid var(--c-border); vertical-align: top; }
tr.marked td { background: rgba(240, 160, 20, 0.05); }
tr.errored td { background: rgba(224, 82, 82, 0.04); }
.q { font-weight: 500; }
.a { font-size: 12px; color: var(--c-secondary); margin-top: 2px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; max-width: 480px; }
.score-chip { display: inline-block; font-size: 11px; margin: 1px 4px 1px 0; padding: 1px 6px; border-radius: 4px; }
.score-chip.good { background: color-mix(in srgb, var(--c-accent) 10%, transparent); color: var(--c-accent); }
.score-chip.mid { background: rgba(240, 160, 20, 0.12); color: #d48806; }
.score-chip.bad { background: rgba(224, 82, 82, 0.1); color: var(--c-danger); }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; }
.tag.done { background: color-mix(in srgb, var(--c-accent) 12%, transparent); color: var(--c-accent); }
.tag.running { background: rgba(24, 144, 255, 0.12); color: #1890ff; }
.tag.pending { background: rgba(24, 144, 255, 0.08); color: #69b1ff; }
.tag.failed { background: rgba(224, 82, 82, 0.12); color: var(--c-danger); }
.tag.cancelled { background: var(--c-muted); color: var(--c-secondary); }
.tag.flow { background: rgba(240, 160, 20, 0.15); color: #d48806; }
.btn.sm { padding: 3px 8px; margin-left: 4px; }
.ipt { padding: 4px 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); font-size: 12px; background: var(--c-panel); color: var(--c-fg); }
.ipt.sel { min-width: 100px; }
.radio { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; cursor: pointer; }
.expand-row td { background: var(--c-muted); }
.sec { margin-bottom: 10px; }
.sec strong { font-size: 12px; color: var(--c-secondary); }
.sec p { margin: 4px 0 0; font-size: 12px; white-space: pre-wrap; }
.ctx { margin: 4px 0 0; font-size: 12px; padding: 4px 8px; background: var(--c-muted); border: 1px solid var(--c-border); border-radius: 4px; }
.ctx-path { display: inline-block; font-size: 10px; padding: 0 6px; margin-right: 6px; border-radius: 8px; background: rgba(24, 144, 255, 0.1); color: #1890ff; }
.form { display: flex; flex-direction: column; gap: 6px; }
.form label { font-size: 12px; color: var(--c-secondary); margin-top: 4px; }
</style>
