<script setup>
import { ref, computed, onMounted, onBeforeUnmount, onActivated, watch } from 'vue'
import {
  fetchGraphSyncCategories, startGraphSync, fetchGraphSyncRun, fetchGraphSyncRuns,
  fetchGraphAlgorithms, startGraphAnalysis, fetchGraphAnalysisTask, fetchGraphAnalysisTasks,
  runInference, queryPropagation, queryImpact, querySimilar, queryPath,
  fetchSuggestions, reviewSuggestion,
} from '../../api'
import { useToast } from '../../composables/useToast'
import Pagination from '../common/Pagination.vue'
import EntityPicker from './EntityPicker.vue'

const toast = useToast()

/* ── Tabs：P0 迁入管理 / P1 计算任务 / P2 推理洞察 ── */
const tabs = [
  { key: 'sync', label: '迁入管理', soon: false },
  { key: 'compute', label: '计算任务', soon: false },
  { key: 'insight', label: '推理洞察', soon: false },
]
const activeTab = ref('sync')

/* ── 类别列表 ── */
const loading = ref(false)
const gdsAvailable = ref(true)
const graphProvider = ref('neo4j')
const categories = ref([])

async function loadCategories() {
  loading.value = true
  try {
    const data = await fetchGraphSyncCategories()
    gdsAvailable.value = !!data.gds_available
    graphProvider.value = data.graph_provider || 'neo4j'
    categories.value = data.items || []
  } catch (e) {
    toast.error('加载迁入类别失败：' + e.message)
  } finally {
    loading.value = false
  }
}

/* ── 预检（dry_run） ── */
const precheckingId = ref('')
async function precheck(cat) {
  precheckingId.value = cat.id
  try {
    const r = await startGraphSync(cat.id, { dryRun: true })
    toast.info(
      `「${cat.name}」预检：${r.ontology_count} 个本体 / ` +
      `${r.total_entities.toLocaleString()} 实体 / ${r.total_relations.toLocaleString()} 关系`
    )
  } catch (e) {
    toast.error(e.message)
  } finally {
    precheckingId.value = ''
  }
}

/* ── 启动迁入 + 进度轮询 ── */
const startingId = ref('')
// run_id → 类别行内进度（含 total，来自 categories 快照与 run 轮询）
const activeRuns = ref({})   // { [categoryId]: run }
const pollTimer = ref(null)

function startPolling() {
  stopPolling()
  pollTimer.value = setInterval(async () => {
    const ids = Object.keys(activeRuns.value)
    if (!ids.length) return
    for (const cid of ids) {
      const run = activeRuns.value[cid]
      try {
        const latest = await fetchGraphSyncRun(run.id)
        activeRuns.value[cid] = latest
        if (latest.status === 'done' || latest.status === 'failed') {
          delete activeRuns.value[cid]
          if (latest.status === 'done') {
            toast.success(`「${catName(cid)}」迁入完成：${latest.entity_count.toLocaleString()} 实体 / ${latest.relation_count.toLocaleString()} 关系`)
          } else {
            toast.error(`「${catName(cid)}」迁入失败：${latest.error || '未知错误'}`)
          }
          await loadCategories()
        }
      } catch { /* 单次轮询失败忽略，下轮重试 */ }
    }
    if (!Object.keys(activeRuns.value).length) stopPolling()
  }, 2000)
}

function stopPolling() {
  if (pollTimer.value) { clearInterval(pollTimer.value); pollTimer.value = null }
}

function catName(cid) {
  return (categories.value.find(c => c.id === cid) || {}).name || cid
}

async function runSync(cat) {
  startingId.value = cat.id
  try {
    const run = await startGraphSync(cat.id, { mode: 'full' })
    activeRuns.value[cat.id] = run
    startPolling()
    toast.info(`「${cat.name}」全量迁入已启动`)
  } catch (e) {
    toast.error(e.message)
  } finally {
    startingId.value = ''
  }
}

function progressOf(cat) {
  const run = activeRuns.value[cat.id]
  if (!run) return null
  const total = run.total_entities || 0
  const done = run.entity_count || 0
  return {
    status: run.status,
    percent: total ? Math.min(100, Math.round((done / total) * 100)) : 0,
    done,
    total,
    relations: run.relation_count || 0,
  }
}

/* 页面恢复（keep-alive）时若有进行中任务则恢复轮询 */
onActivated(() => {
  const hasRunning = categories.value.some(c => c.last_run && ['pending', 'running'].includes(c.last_run.status))
  if (hasRunning && !pollTimer.value) {
    for (const c of categories.value) {
      if (c.last_run && ['pending', 'running'].includes(c.last_run.status)) {
        activeRuns.value[c.id] = c.last_run
      }
    }
    startPolling()
  }
})

/* ── 迁入历史 ── */
const historyCatId = ref('')
const historyRuns = ref([])
const historyLoading = ref(false)
const historyPage = ref(1)
const historyPageSize = ref(8)
const pagedHistoryRuns = computed(() =>
  historyRuns.value.slice((historyPage.value - 1) * historyPageSize.value, historyPage.value * historyPageSize.value)
)

async function toggleHistory(cat) {
  if (historyCatId.value === cat.id) { historyCatId.value = ''; return }
  historyCatId.value = cat.id
  historyLoading.value = true
  historyPage.value = 1
  try {
    historyRuns.value = await fetchGraphSyncRuns(cat.id, 50)
  } catch {
    historyRuns.value = []
  } finally {
    historyLoading.value = false
  }
}

/* ─────────────────────── Tab2 计算任务（P1） ─────────────────────── */

const algorithms = ref([])
const computeCatId = ref('')
const computeForm = ref({ algorithm: '', top_n: 20, write_back: false, label_filter: '', similarity_cutoff: 0 })
const startingCompute = ref(false)
const computeTask = ref(null)          // 进行中任务（轮询）
const computeTimer = ref(null)
const computeHistory = ref([])
const resultTask = ref(null)           // 结果面板展示的任务

const computeCats = computed(() => categories.value.filter(c => (c.graph?.nodes || 0) > 0))
const selectedAlgo = computed(() => algorithms.value.find(a => a.key === computeForm.value.algorithm) || null)

watch(computeCatId, loadComputeHistory)

async function loadComputeHistory() {
  if (!computeCatId.value) { computeHistory.value = []; return }
  try {
    computeHistory.value = await fetchGraphAnalysisTasks(computeCatId.value, 50)
  } catch {
    computeHistory.value = []
  }
}

function pickAlgorithm(key) {
  computeForm.value.algorithm = key
  computeForm.value.similarity_cutoff = 0
}

async function runCompute() {
  if (!computeCatId.value || !computeForm.value.algorithm) return
  startingCompute.value = true
  try {
    const t = await startGraphAnalysis(computeCatId.value, computeForm.value)
    computeTask.value = t
    resultTask.value = null
    startComputePolling()
    toast.info('计算任务已启动')
  } catch (e) {
    toast.error(e.message)
  } finally {
    startingCompute.value = false
  }
}

function startComputePolling() {
  stopComputePolling()
  computeTimer.value = setInterval(async () => {
    if (!computeTask.value) return stopComputePolling()
    try {
      const latest = await fetchGraphAnalysisTask(computeTask.value.id)
      computeTask.value = latest
      if (latest.status === 'done' || latest.status === 'failed') {
        stopComputePolling()
        if (latest.status === 'done') {
          toast.success(`「${latest.algorithm_name}」计算完成（${(latest.stats?.elapsed_seconds ?? 0)}s）`)
          resultTask.value = latest
        } else {
          toast.error('计算失败：' + (latest.error || '未知错误'))
        }
        computeTask.value = null
        await loadComputeHistory()
      }
    } catch { /* 单次轮询失败忽略，下轮重试 */ }
  }, 2000)
}

function stopComputePolling() {
  if (computeTimer.value) { clearInterval(computeTimer.value); computeTimer.value = null }
}

function showResult(t) { resultTask.value = t }

function algoStatusClass(s) {
  return { done: 'ok', failed: 'bad', running: 'busy', pending: 'busy' }[s] || ''
}

/* 类别首次加载后默认选中第一个有图数据的类别 */
watch(computeCats, (list) => {
  if (!computeCatId.value && list.length) computeCatId.value = list[0].id
})

/* ─────────────────────── Tab3 推理洞察（P2） ─────────────────────── */

const insightCatId = ref('')
const startingInfer = ref(false)
const inferTask = ref(null)
const inferTimer = ref(null)
const inferSummary = ref(null)

/* 四个查询器的实体选择与结果 */
const propEntityId = ref('')
const propEntity = ref(null)      // {id, name, entity_type}
const propHops = ref(3)
const propResult = ref(null)
const propLoading = ref(false)
const impactEntityId = ref('')
const impactEntity = ref(null)
const impactResult = ref(null)
const impactLoading = ref(false)
const simEntityId = ref('')
const simEntity = ref(null)
const simResult = ref(null)
const simLoading = ref(false)
const pathSrcId = ref('')
const pathTgtId = ref('')
const pathSrc = ref(null)
const pathTgt = ref(null)
const pathResult = ref(null)
const pathLoading = ref(false)

/* 建议审核 */
const suggestions = ref([])
const sugLoading = ref(false)
const reviewingId = ref('')

watch(computeCats, (list) => {
  if (!insightCatId.value && list.length) insightCatId.value = list[0].id
})
watch(insightCatId, () => {
  propResult.value = impactResult.value = simResult.value = pathResult.value = null
  propEntityId.value = impactEntityId.value = simEntityId.value = pathSrcId.value = pathTgtId.value = ''
  propEntity.value = impactEntity.value = simEntity.value = pathSrc.value = pathTgt.value = null
  inferSummary.value = null
  loadSuggestions()
})

async function runInferenceNow() {
  if (!insightCatId.value) return
  startingInfer.value = true
  try {
    const t = await runInference(insightCatId.value)
    inferTask.value = t
    startInferPolling()
    toast.info('规则推理已启动')
  } catch (e) {
    toast.error(e.message)
  } finally {
    startingInfer.value = false
  }
}

function startInferPolling() {
  stopInferPolling()
  inferTimer.value = setInterval(async () => {
    if (!inferTask.value) return stopInferPolling()
    try {
      const latest = await fetchGraphAnalysisTask(inferTask.value.id)
      inferTask.value = latest.status === 'pending' || latest.status === 'running' ? latest : null
      if (!inferTask.value) {
        stopInferPolling()
        if (latest.status === 'done') {
          const s = latest.stats || {}
          inferSummary.value = s
          toast.success(`推理完成：候选 ${s.candidates ?? 0} 条，产出建议 ${latest.results?.created ?? 0} 条`)
          await loadSuggestions()
        } else {
          toast.error('推理失败：' + (latest.error || '未知错误'))
        }
      }
    } catch { /* 下轮重试 */ }
  }, 2000)
}

function stopInferPolling() {
  if (inferTimer.value) { clearInterval(inferTimer.value); inferTimer.value = null }
}

async function loadSuggestions() {
  if (!insightCatId.value) { suggestions.value = []; return }
  sugLoading.value = true
  try {
    suggestions.value = await fetchSuggestions(insightCatId.value, 'pending')
  } catch {
    suggestions.value = []
  } finally {
    sugLoading.value = false
  }
}

async function review(sug, action) {
  reviewingId.value = sug.id
  try {
    await reviewSuggestion(sug.id, action)
    toast.success(action === 'approve'
      ? `已批准：「${sug.source_entity_name}」-${sug.suggested_relation_type}->「${sug.target_entity_name}」`
      : '已拒绝（同类建议不再重推）')
    await loadSuggestions()
  } catch (e) {
    toast.error(e.message)
  } finally {
    reviewingId.value = ''
  }
}

async function doPropagation() {
  if (!propEntity.value) return
  propLoading.value = true
  try {
    propResult.value = await queryPropagation(insightCatId.value, propEntity.value.id, propHops.value)
    if (!propResult.value.chains.length) toast.info('该实体沿「导致」无可达传播链')
  } catch (e) {
    toast.error(e.message)
  } finally {
    propLoading.value = false
  }
}

async function doImpact() {
  if (!impactEntity.value) return
  impactLoading.value = true
  try {
    impactResult.value = await queryImpact(insightCatId.value, impactEntity.value.id)
  } catch (e) {
    toast.error(e.message)
  } finally {
    impactLoading.value = false
  }
}

async function doSimilar() {
  if (!simEntity.value) return
  simLoading.value = true
  try {
    simResult.value = await querySimilar(insightCatId.value, simEntity.value.id)
  } catch (e) {
    toast.error(e.message)
  } finally {
    simLoading.value = false
  }
}

async function doPath() {
  if (!pathSrc.value || !pathTgt.value) return
  pathLoading.value = true
  try {
    pathResult.value = await queryPath(insightCatId.value, pathSrc.value.id, pathTgt.value.id)
    if (!pathResult.value.chains.length) toast.info('两实体间未找到语义关系链')
  } catch (e) {
    toast.error(e.message)
  } finally {
    pathLoading.value = false
  }
}

/* 分层链路布局：BFS 定层级（起点层 0），同层纵向排布 → SVG 坐标 */
function makeChainLayout(nodes, edges, startId) {
  if (!nodes?.length) return null
  const adj = {}
  edges.forEach((e) => { (adj[e.source] ||= []).push(e.target) })
  const level = { [startId]: 0 }
  const queue = [startId]
  while (queue.length) {
    const u = queue.shift()
    for (const v of adj[u] || []) if (!(v in level)) { level[v] = level[u] + 1; queue.push(v) }
  }
  const maxL = Math.max(0, ...Object.values(level))
  const byLevel = {}
  nodes.forEach((n) => {
    const l = level[n.id] ?? maxL + 1
    (byLevel[l] ||= []).push(n)
  })
  const levels = Object.keys(byLevel).map(Number).sort((a, b) => a - b)
  const pos = {}
  levels.forEach((l, li) => byLevel[l].forEach((n, ni) => {
    pos[n.id] = { x: 90 + li * 210, y: 60 + ni * 82, node: n, level: l }
  }))
  const laid = edges.map((e, i) => ({
    ...e,
    id: i,
    x1: pos[e.source]?.x ?? 0, y1: pos[e.source]?.y ?? 0,
    x2: pos[e.target]?.x ?? 0, y2: pos[e.target]?.y ?? 0,
  })).filter((e) => pos[e.source] && pos[e.target])
  return {
    pts: Object.values(pos),
    edges: laid,
    w: 90 + levels.length * 210 + 60,
    h: 60 + Math.max(...levels.map((l) => byLevel[l].length)) * 82 + 50,
  }
}

const propLayout = computed(() =>
  propResult.value ? makeChainLayout(propResult.value.nodes, propResult.value.edges, propEntity.value?.id) : null)
const pathLayout = computed(() =>
  pathResult.value?.chains?.length
    ? makeChainLayout(pathResult.value.chains[0].nodes, pathResult.value.chains[0].edges, pathSrc.value?.id)
    : null)

/* ── 展示工具 ── */
function fmtNum(n) { return (n || 0).toLocaleString() }

function fmtTime(t) {
  if (!t) return '—'
  return String(t).replace('T', ' ').slice(0, 19)
}

function lastRunText(cat) {
  const r = cat.last_run
  if (!r) return '从未迁入'
  const stat = { done: '完成', failed: '失败', running: '进行中', pending: '排队中' }[r.status] || r.status
  return `${stat} · ${fmtTime(r.finished_at || r.created_at)}`
}

function runStatusClass(s) {
  return { done: 'ok', failed: 'bad', running: 'busy', pending: 'busy' }[s] || ''
}

onMounted(async () => {
  loadCategories()
  try {
    algorithms.value = await fetchGraphAlgorithms()
    if (!computeForm.value.algorithm && algorithms.value.length) {
      computeForm.value.algorithm = algorithms.value[0].key
    }
  } catch (e) {
    toast.error('加载算法列表失败：' + e.message)
  }
})
onBeforeUnmount(() => { stopPolling(); stopComputePolling(); stopInferPolling() })
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">图分析</h2>
        <span class="page-subtitle">把本体实体迁入 Neo4j 分析图，支撑图计算与图推理（数据流：PostgreSQL 权威 → 单向流入分析图）</span>
      </div>
    </div>

    <div class="tabs">
      <button
        v-for="t in tabs" :key="t.key"
        class="tab" :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >
        <span class="tab-label">{{ t.label }}</span>
        <span v-if="t.soon" class="soon-tag">P{{ t.key === 'compute' ? 1 : 2 }}</span>
      </button>
    </div>

    <!-- Tab1 迁入管理 -->
    <section v-show="activeTab === 'sync'" class="sync-section">
      <div class="status-bar">
        <span class="sum-chip" :class="gdsAvailable ? 'chip-ok' : 'chip-warn'">
          GDS {{ gdsAvailable ? '可用' : '不可用（迁入可用，图计算需安装插件）' }}
        </span>
        <span class="sum-chip">图存储 {{ graphProvider }}</span>
        <span class="sum-chip">类别 {{ categories.length }}</span>
        <button class="btn sm" :disabled="loading" @click="loadCategories">
          <span v-if="loading" class="spinner sm"></span> 刷新
        </button>
      </div>

      <div v-if="loading && !categories.length" class="loading-state">
        <span class="spinner"></span> 加载中...
      </div>

      <template v-else>
        <div v-if="!categories.length" class="tab-empty">
          还没有本体类别，请先到「本体管理」创建领域与本体
        </div>

        <div v-for="cat in categories" :key="cat.id" class="cat-card">
          <div class="cat-head">
            <div class="cat-title">
              <span class="cat-name">{{ cat.name }}</span>
              <span v-if="cat.is_system" class="type-tag">系统</span>
              <span v-if="cat.graph.stale" class="stale-tag" title="图内节点数与权威库不一致，建议重新迁入">已过期</span>
            </div>
            <div class="cat-desc">{{ cat.description || '—' }}</div>
          </div>

          <div class="cat-stats">
            <div class="stat">
              <div class="stat-num">{{ fmtNum(cat.entity_count) }}</div>
              <div class="stat-label">实体（权威库）</div>
            </div>
            <div class="stat">
              <div class="stat-num">{{ fmtNum(cat.relation_count) }}</div>
              <div class="stat-label">关系（权威库）</div>
            </div>
            <div class="stat">
              <div class="stat-num" :class="{ dim: !cat.graph.nodes }">{{ fmtNum(cat.graph.nodes) }}</div>
              <div class="stat-label">图内节点</div>
            </div>
            <div class="stat">
              <div class="stat-num" :class="{ dim: !cat.graph.relations }">{{ fmtNum(cat.graph.relations) }}</div>
              <div class="stat-label">图内关系</div>
            </div>
            <div class="stat">
              <div class="stat-num projection">
                <template v-if="cat.graph.projection">{{ fmtNum(cat.graph.projection.node_count) }}</template>
                <template v-else>—</template>
              </div>
              <div class="stat-label">
                GDS 投影{{ cat.graph.projection ? `（${fmtNum(cat.graph.projection.relationship_count)} 边）` : '（未建）' }}
              </div>
            </div>
            <div class="stat">
              <div class="stat-num run-status" :class="runStatusClass(cat.last_run && cat.last_run.status)">
                {{ lastRunText(cat) }}
              </div>
              <div class="stat-label">上次迁入</div>
            </div>
          </div>

          <!-- 进行中进度条 -->
          <div v-if="progressOf(cat)" class="prog-wrap">
            <div class="prog-info">
              <span v-if="progressOf(cat).status === 'running'" class="spinner sm"></span>
              {{ progressOf(cat).status === 'running' ? '迁入中' : '排队中' }}
              {{ fmtNum(progressOf(cat).done) }} / {{ fmtNum(progressOf(cat).total) }} 实体 ·
              {{ fmtNum(progressOf(cat).relations) }} 关系
            </div>
            <div class="prog-bar"><div class="prog-fill" :style="{ width: progressOf(cat).percent + '%' }"></div></div>
          </div>

          <div class="cat-actions">
            <button class="btn sm" :disabled="!!activeRuns[cat.id] || precheckingId === cat.id" @click="precheck(cat)">
              <span v-if="precheckingId === cat.id" class="spinner sm"></span> 预检
            </button>
            <button
              class="btn primary sm"
              :disabled="!cat.entity_count || !!activeRuns[cat.id] || startingId === cat.id || !gdsAvailable"
              :title="!gdsAvailable ? 'GDS 不可用' : (!cat.entity_count ? '该类别暂无实体' : '重建该类别分析图（清旧图后全量写入，约 1~2 分钟）')"
              @click="runSync(cat)"
            >
              <span v-if="startingId === cat.id" class="spinner sm"></span>
              {{ activeRuns[cat.id] ? '迁入中...' : '全量迁入' }}
            </button>
            <button class="btn sm ghost" @click="toggleHistory(cat)">
              {{ historyCatId === cat.id ? '收起历史' : '迁入历史' }}
            </button>
          </div>

          <!-- 历史列表 -->
          <div v-if="historyCatId === cat.id" class="history-box">
            <div v-if="historyLoading" class="loading-state"><span class="spinner sm"></span> 加载中...</div>
            <div v-else-if="!historyRuns.length" class="tab-empty">暂无迁入记录</div>
            <template v-else>
              <table class="history-table">
                <thead>
                  <tr><th>时间</th><th>模式</th><th>状态</th><th>实体</th><th>关系</th><th>耗时/错误</th></tr>
                </thead>
                <tbody>
                  <tr v-for="r in pagedHistoryRuns" :key="r.id">
                    <td>{{ fmtTime(r.created_at) }}</td>
                    <td>{{ r.mode === 'full' ? '全量' : r.mode }}</td>
                    <td><span class="run-dot" :class="runStatusClass(r.status)"></span>{{ r.status }}</td>
                    <td>{{ fmtNum(r.entity_count) }}</td>
                    <td>{{ fmtNum(r.relation_count) }}</td>
                    <td class="err-cell" :title="r.error || ''">
                      {{ r.status === 'failed' ? (r.error || '').slice(0, 60) : '' }}
                    </td>
                  </tr>
                </tbody>
              </table>
              <Pagination
                v-if="historyRuns.length > historyPageSize"
                v-model:page="historyPage" v-model:page-size="historyPageSize"
                :total="historyRuns.length"
              />
            </template>
          </div>
        </div>
      </template>
    </section>

    <!-- Tab2 计算任务（P1） -->
    <section v-show="activeTab === 'compute'" class="compute-section">
      <div class="status-bar">
        <label class="sel-label">分析类别</label>
        <select v-model="computeCatId" class="cat-select" :disabled="!computeCats.length">
          <option v-for="c in computeCats" :key="c.id" :value="c.id">
            {{ c.name }}（图内 {{ fmtNum(c.graph.nodes) }} 节点）
          </option>
        </select>
        <span v-if="!computeCats.length" class="hint-warn">
          暂无已迁入类别，请先在「迁入管理」执行全量迁入
        </span>
      </div>

      <!-- 算法卡片 -->
      <div class="algo-grid">
        <div
          v-for="a in algorithms" :key="a.key"
          class="algo-card" :class="{ selected: computeForm.algorithm === a.key }"
          @click="pickAlgorithm(a.key)"
        >
          <div class="algo-name">
            {{ a.name }}
            <span class="gds-tag" :class="{ off: !a.needs_gds }">{{ a.needs_gds ? 'GDS' : 'Cypher' }}</span>
          </div>
          <div class="algo-semantic">{{ a.semantic }}</div>
          <div class="algo-result">{{ a.result }}</div>
        </div>
      </div>

      <!-- 参数表单 -->
      <div v-if="selectedAlgo" class="form-card">
        <div class="form-row">
          <label>榜单行数（1~100）</label>
          <input v-model.number="computeForm.top_n" type="number" min="1" max="100" />
        </div>
        <div class="form-row">
          <label>本体类型过滤（如「故障模式」，留空 = 全部）</label>
          <input v-model="computeForm.label_filter" type="text" placeholder="留空计算全部类型" />
        </div>
        <div v-if="computeForm.algorithm === 'node_similarity'" class="form-row">
          <label>相似度下限（0~1，0 = 不过滤）</label>
          <input v-model.number="computeForm.similarity_cutoff" type="number" min="0" max="1" step="0.05" />
        </div>
        <label v-if="selectedAlgo.supports_write" class="chk-row">
          <input v-model="computeForm.write_back" type="checkbox" />
          写回分析图节点属性（{{ selectedAlgo.result }}；分值只写 Neo4j 派生数据，不动权威库）
        </label>
        <button
          class="btn primary sm" :disabled="!computeCatId || startingCompute || !!computeTask"
          @click="runCompute"
        >
          <span v-if="startingCompute" class="spinner sm"></span>
          {{ computeTask ? '计算中...' : '运行' }}
        </button>
      </div>

      <!-- 进行中任务 -->
      <div v-if="computeTask" class="prog-wrap">
        <div class="prog-info">
          <span v-if="computeTask.status === 'running'" class="spinner sm"></span>
          {{ computeTask.algorithm_name }} ·
          {{ computeTask.status === 'running' ? '计算中' : '排队中' }}（图内
          {{ fmtNum(computeTask.stats && computeTask.stats.graph_nodes) }} 节点）
        </div>
      </div>

      <!-- 运行历史 -->
      <div v-if="computeCatId" class="history-box">
        <table v-if="computeHistory.length" class="history-table">
          <thead>
            <tr><th>时间</th><th>算法</th><th>参数</th><th>状态</th><th>耗时</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="t in computeHistory" :key="t.id">
              <td>{{ fmtTime(t.created_at) }}</td>
              <td>{{ t.algorithm_name }}</td>
              <td class="params-cell">
                top{{ t.params.top_n }}{{ t.params.label_filter ? ` · ${t.params.label_filter}` : '' }}{{ t.params.write_back ? ' · 写回' : '' }}
              </td>
              <td><span class="run-dot" :class="algoStatusClass(t.status)"></span>{{ t.status }}</td>
              <td>{{ t.stats && t.stats.elapsed_seconds != null ? t.stats.elapsed_seconds + 's' : '—' }}</td>
              <td><button v-if="t.results" class="btn sm ghost" @click="showResult(t)">结果</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="tab-empty">暂无计算记录</div>
      </div>

      <!-- 结果面板 -->
      <div v-if="resultTask && resultTask.results" class="result-panel">
        <div class="result-head">
          <span>
            {{ resultTask.algorithm_name }} · {{ resultTask.results.rows.length }} 条
            <template v-if="resultTask.stats && resultTask.stats.written">
              · 已写回 {{ fmtNum(resultTask.stats.written) }} 节点属性
            </template>
          </span>
          <button class="btn sm ghost" @click="resultTask = null">收起</button>
        </div>

        <table v-if="resultTask.results.kind === 'ranking'" class="history-table">
          <thead>
            <tr><th>#</th><th>实体</th><th>类型</th><th>{{ resultTask.results.score_label }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in resultTask.results.rows" :key="r.id">
              <td>{{ i + 1 }}</td>
              <td>{{ r.name }}</td>
              <td>{{ r.entity_type }}</td>
              <td>{{ r.score }}</td>
            </tr>
          </tbody>
        </table>

        <table v-else-if="resultTask.results.kind === 'communities'" class="history-table">
          <thead><tr><th>社区</th><th>规模</th><th>成员（样例）</th></tr></thead>
          <tbody>
            <tr v-for="c in resultTask.results.rows" :key="c.community_id">
              <td>#{{ c.community_id }}</td>
              <td>{{ fmtNum(c.size) }}</td>
              <td class="members-cell">{{ c.members.map(m => m.name).join('、') }}</td>
            </tr>
          </tbody>
        </table>

        <table v-else-if="resultTask.results.kind === 'pairs'" class="history-table">
          <thead><tr><th>实体 A</th><th>类型</th><th>实体 B</th><th>类型</th><th>相似度</th></tr></thead>
          <tbody>
            <tr v-for="(r, i) in resultTask.results.rows" :key="i">
              <td>{{ r.source_name }}</td>
              <td>{{ r.source_type }}</td>
              <td>{{ r.target_name }}</td>
              <td>{{ r.target_type }}</td>
              <td>{{ r.similarity }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Tab3 推理洞察（P2） -->
    <section v-show="activeTab === 'insight'" class="insight-section">
      <!-- 顶栏：类别 + 规则推理 -->
      <div class="status-bar">
        <label class="sel-label">分析类别</label>
        <select v-model="insightCatId" class="cat-select" :disabled="!computeCats.length">
          <option v-for="c in computeCats" :key="c.id" :value="c.id">
            {{ c.name }}（图内 {{ fmtNum(c.graph.nodes) }} 节点）
          </option>
        </select>
        <button
          class="btn primary sm"
          :disabled="!insightCatId || startingInfer || !!inferTask"
          @click="runInferenceNow"
        >
          <span v-if="startingInfer" class="spinner sm"></span>
          {{ inferTask ? '推理中...' : '运行规则推理' }}
        </button>
        <span v-if="inferTask" class="prog-inline">
          <span class="spinner sm"></span>{{ inferTask.status === 'running' ? '推理中' : '排队中' }}
        </span>
        <span v-else-if="inferSummary" class="infer-summary">
          上次推理：候选 {{ fmtNum(inferSummary.candidates) }} ·
          拦截（白名单 {{ inferSummary.blocked_constraint || 0 }} / tombstone
          {{ inferSummary.blocked_tombstone || 0 }} / 待审重复 {{ inferSummary.blocked_pending || 0 }}）·
          产出 {{ fmtNum(inferSummary.created) }} 条 ·
          {{ inferSummary.elapsed_seconds }}s
        </span>
        <span v-if="!computeCats.length" class="hint-warn">
          暂无已迁入类别，请先在「迁入管理」执行全量迁入
        </span>
      </div>

      <!-- 查询器 -->
      <div class="query-grid">
        <!-- 传播链 -->
        <div class="query-card">
          <div class="query-title">故障往下会引发什么（传播链）</div>
          <div class="query-form">
            <EntityPicker :category-id="insightCatId" v-model="propEntityId" placeholder="选择故障模式…" @change="propEntity = $event" />
            <select v-model="propHops" class="cat-select sm">
              <option :value="1">1 跳</option><option :value="2">2 跳</option>
              <option :value="3">3 跳</option><option :value="5">5 跳</option>
            </select>
            <button class="btn sm" :disabled="!propEntity || propLoading" @click="doPropagation">
              <span v-if="propLoading" class="spinner sm"></span>查询
            </button>
          </div>

          <div v-if="propResult?.chains?.length" class="query-body">
            <div v-if="propLayout" class="chain-scroll">
              <svg :viewBox="`0 0 ${propLayout.w} ${propLayout.h}`" class="chain-svg">
                <defs>
                  <marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                    <path d="M0,0 L7,3 L0,6 z" fill="#64748b" />
                  </marker>
                </defs>
                <line v-for="e in propLayout.edges" :key="e.id"
                      :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2"
                      stroke="#64748b" stroke-width="1.2" marker-end="url(#arr)" />
                <text v-for="e in propLayout.edges" :key="'t' + e.id"
                      :x="(e.x1 + e.x2) / 2" :y="(e.y1 + e.y2) / 2 - 4"
                      class="svg-edge-label">{{ e.type }}</text>
                <g v-for="p in propLayout.pts" :key="p.node.id">
                  <circle :cx="p.x" :cy="p.y" r="17"
                          :class="['svg-node', { start: p.node.id === propEntity?.id }]" />
                  <text :x="p.x" :y="p.y + 5" class="svg-node-t">
                    {{ (p.node.name || '').slice(0, 6) }}
                  </text>
                  <text :x="p.x" :y="p.y + 34" class="svg-node-sub">{{ p.node.entity_type }}</text>
                </g>
              </svg>
            </div>
            <div class="chain-list">
              <div v-for="(c, i) in propResult.chains" :key="i" class="chain-item">
                <span class="chain-conf">{{ Math.round(c.confidence * 100) }}%</span>
                <span>{{ c.label }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 影响范围 -->
        <div class="query-card">
          <div class="query-title">部件出问题影响哪些系统 / 机型（影响范围）</div>
          <div class="query-form">
            <EntityPicker :category-id="insightCatId" v-model="impactEntityId" placeholder="选择部件…" @change="impactEntity = $event" />
            <button class="btn sm" :disabled="!impactEntity || impactLoading" @click="doImpact">
              <span v-if="impactLoading" class="spinner sm"></span>查询
            </button>
          </div>
          <div v-if="impactResult" class="query-body">
            <div v-if="impactResult.groups.upstream.length" class="impact-group">
              <div class="impact-head">上游系统 / 机型（组成向上 ∪ 装于链）</div>
              <div class="tag-row">
                <span v-for="u in impactResult.groups.upstream" :key="u.id" class="ent-tag">
                  {{ u.name }} <i>{{ u.entity_type }}</i>
                </span>
              </div>
            </div>
            <div v-if="impactResult.groups.faults.length" class="impact-group">
              <div class="impact-head">受影响的故障模式（发生于反向）</div>
              <div class="tag-row">
                <span v-for="f in impactResult.groups.faults" :key="f.id" class="ent-tag warn">
                  {{ f.name }} <i>{{ f.entity_type }}</i>
                </span>
              </div>
            </div>
            <div v-if="!impactResult.groups.upstream.length && !impactResult.groups.faults.length"
                 class="tab-empty">该实体暂无上游与关联故障</div>
          </div>
        </div>

        <!-- 相似实体 -->
        <div class="query-card">
          <div class="query-title">相似故障案例（排故参考）</div>
          <div class="query-form">
            <EntityPicker :category-id="insightCatId" v-model="simEntityId" placeholder="选择实体…" @change="simEntity = $event" />
            <button class="btn sm" :disabled="!simEntity || simLoading" @click="doSimilar">
              <span v-if="simLoading" class="spinner sm"></span>查询
            </button>
          </div>
          <div v-if="simResult" class="query-body">
            <div v-if="simResult.rows.length" class="chain-list">
              <div v-for="(r, i) in simResult.rows" :key="r.id" class="chain-item">
                <span class="chain-conf">{{ (r.similarity * 100).toFixed(1) }}%</span>
                <span>{{ r.name }}</span><i class="ent-i">{{ r.entity_type }}</i>
              </div>
            </div>
            <div v-else class="tab-empty">无相似实体（可先在「计算任务」跑一次节点相似度建立索引）</div>
          </div>
        </div>

        <!-- 两点路径 -->
        <div class="query-card">
          <div class="query-title">两实体的关系链（如 AD → 部件 → 故障）</div>
          <div class="query-form">
            <EntityPicker :category-id="insightCatId" v-model="pathSrcId" placeholder="起点实体…" @change="pathSrc = $event" />
            <EntityPicker :category-id="insightCatId" v-model="pathTgtId" placeholder="终点实体…" @change="pathTgt = $event" />
            <button class="btn sm" :disabled="!pathSrc || !pathTgt || pathLoading" @click="doPath">
              <span v-if="pathLoading" class="spinner sm"></span>查询
            </button>
          </div>
          <div v-if="pathResult" class="query-body">
            <div v-if="pathLayout" class="chain-scroll">
              <svg :viewBox="`0 0 ${pathLayout.w} ${pathLayout.h}`" class="chain-svg">
                <line v-for="e in pathLayout.edges" :key="e.id"
                      :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2"
                      stroke="#64748b" stroke-width="1.2" marker-end="url(#arr)" />
                <text v-for="e in pathLayout.edges" :key="'t' + e.id"
                      :x="(e.x1 + e.x2) / 2" :y="(e.y1 + e.y2) / 2 - 4"
                      class="svg-edge-label">{{ e.type }}</text>
                <g v-for="p in pathLayout.pts" :key="p.node.id">
                  <circle :cx="p.x" :cy="p.y" r="17" class="svg-node" />
                  <text :x="p.x" :y="p.y + 5" class="svg-node-t">
                    {{ (p.node.name || '').slice(0, 6) }}
                  </text>
                  <text :x="p.x" :y="p.y + 34" class="svg-node-sub">{{ p.node.entity_type }}</text>
                </g>
              </svg>
            </div>
            <div v-else class="tab-empty">未找到关系链</div>
          </div>
        </div>
      </div>

      <!-- 建议审核 -->
      <div class="sug-box">
        <div class="sug-head">
          <span>隐含关系建议（待审 {{ suggestions.length }}）</span>
          <button class="btn sm ghost" @click="loadSuggestions">刷新</button>
        </div>
        <div v-if="sugLoading" class="tab-empty">加载中…</div>
        <div v-else-if="!suggestions.length" class="tab-empty">
          暂无待审建议——点上方「运行规则推理」生成，或已全部处理完
        </div>
        <div v-else class="sug-list">
          <div v-for="s in suggestions" :key="s.id" class="sug-card">
            <div class="sug-main">
              <span class="sug-ent">{{ s.source_entity_name }}</span>
              <span class="sug-rel">—{{ s.suggested_relation_type }}→</span>
              <span class="sug-ent">{{ s.target_entity_name }}</span>
              <span class="sug-conf" :title="`规则置信度 ${s.confidence}`">
                {{ Math.round(s.confidence * 100) }}%
              </span>
            </div>
            <div class="sug-evidence">
              {{ s.reason }}
              <template v-if="s.evidence?.chain">｜证据：{{ s.evidence.chain.join(' → ') }}</template>
            </div>
            <div class="sug-actions">
              <button class="btn sm primary" :disabled="reviewingId === s.id"
                      @click="review(s, 'approve')">批准</button>
              <button class="btn sm danger" :disabled="reviewingId === s.id"
                      @click="review(s, 'reject')">拒绝</button>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }

.tabs { display: flex; align-items: center; gap: 2px; border-bottom: 1px solid var(--c-border); }
.tab {
  position: relative; display: inline-flex; align-items: center; gap: 8px;
  padding: 9px 14px; border: 0; background: transparent; cursor: pointer;
  font-size: 13px; font-family: var(--font); color: var(--c-secondary);
  border-bottom: 2px solid transparent; transition: color 150ms, border-color 150ms, background 150ms;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
}
.tab:hover { color: var(--c-fg); background: var(--c-muted); }
.tab.active { color: var(--c-accent); border-bottom-color: var(--c-accent); font-weight: 600; cursor: default; }

.btn.sm { padding: 4px 11px; font-size: 12px; }
.btn.ghost { background: transparent; }
.btn:disabled { cursor: not-allowed; }
.spinner.sm { width: 14px; height: 14px; border-width: 2px; }

.loading-state { padding: 40px; text-align: center; color: var(--c-secondary); }
.tab-empty { padding: 36px; text-align: center; color: var(--c-secondary); font-size: 13px; }
.type-tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); flex-shrink: 0; }

.sync-section { display: flex; flex-direction: column; gap: 14px; }
.status-bar { display: flex; align-items: center; gap: 10px; }
.sum-chip { font-size: 12px; padding: 3px 10px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); }
.sum-chip.chip-ok { color: #10b981; }
.sum-chip.chip-warn { color: #f59e0b; }

.cat-card {
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  padding: 16px 18px;
  background: var(--c-panel);
  display: flex; flex-direction: column; gap: 12px;
}
.cat-title { display: flex; align-items: center; gap: 8px; }
.cat-name { font-size: 15px; font-weight: 600; }
.stale-tag {
  font-size: 11px; padding: 1px 8px; border-radius: 999px;
  background: rgba(245, 158, 11, .15); color: #f59e0b;
}
.cat-desc { font-size: 12px; color: var(--c-secondary); margin-top: 4px; }

.cat-stats { display: flex; gap: 26px; flex-wrap: wrap; }
.stat { min-width: 96px; }
.stat-num { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--c-fg); }
.stat-num.dim { opacity: .4; }
.stat-num.run-status { font-size: 12.5px; font-weight: 500; }
.stat-num.run-status.ok { color: #10b981; }
.stat-num.run-status.bad { color: #ef4444; }
.stat-num.run-status.busy { color: #38bdf8; }
.stat-label { font-size: 11.5px; color: var(--c-secondary); margin-top: 2px; }

.prog-wrap { display: flex; flex-direction: column; gap: 6px; }
.prog-info { font-size: 12.5px; display: flex; align-items: center; gap: 6px; color: var(--c-secondary); }
.prog-bar { height: 6px; border-radius: 4px; background: var(--c-muted); overflow: hidden; }
.prog-fill { height: 100%; background: #38bdf8; border-radius: 4px; transition: width .4s ease; }

.cat-actions { display: flex; gap: 10px; align-items: center; }

.history-box { border-top: 1px dashed var(--c-border); padding-top: 10px; }
.history-table { width: 100%; font-size: 12.5px; border-collapse: collapse; }
.history-table th { text-align: left; color: var(--c-secondary); font-weight: 500; padding: 4px 10px 6px 0; }
.history-table td { padding: 5px 10px 5px 0; border-top: 1px solid var(--c-border); color: var(--c-fg); }
.run-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
.run-dot.ok { background: #10b981; }
.run-dot.bad { background: #ef4444; }
.run-dot.busy { background: #38bdf8; }
.err-cell { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #ef4444; }

.soon-tag {
  font-size: 10px; padding: 0 6px; border-radius: 999px; margin-left: 6px;
  background: rgba(148, 163, 184, .18); color: #94a3b8;
}

/* ── Tab2 计算任务 ── */
.compute-section { display: flex; flex-direction: column; gap: 14px; }
.sel-label { font-size: 12px; color: var(--c-secondary); }
.cat-select {
  padding: 4px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 12.5px; font-family: var(--font);
}
.hint-warn { font-size: 12px; color: #f59e0b; }

.algo-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.algo-card {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  padding: 12px 14px; cursor: pointer; background: var(--c-panel);
  display: flex; flex-direction: column; gap: 6px; transition: border-color 150ms;
}
.algo-card:hover { border-color: var(--c-accent); }
.algo-card.selected { border-color: var(--c-accent); box-shadow: 0 0 0 1px var(--c-accent) inset; }
.algo-name { font-size: 13.5px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
.gds-tag {
  font-size: 10px; padding: 0 6px; border-radius: 999px; flex-shrink: 0;
  background: rgba(56, 189, 248, .15); color: #38bdf8;
}
.gds-tag.off { background: rgba(148, 163, 184, .15); color: #94a3b8; }
.algo-semantic { font-size: 12px; color: var(--c-fg); }
.algo-result { font-size: 11px; color: var(--c-secondary); }

.form-card {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  padding: 14px 16px; background: var(--c-panel);
  display: flex; flex-direction: column; gap: 10px; align-items: flex-start;
}
.form-row { display: flex; align-items: center; gap: 10px; }
.form-row label { font-size: 12px; color: var(--c-secondary); min-width: 220px; }
.form-row input {
  padding: 4px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg, var(--c-panel)); color: var(--c-fg);
  font-size: 12.5px; font-family: var(--font); width: 180px;
}
.chk-row { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--c-secondary); }
.params-cell { max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.result-panel {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  padding: 12px 16px; background: var(--c-panel); display: flex; flex-direction: column; gap: 10px;
}
.result-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; font-weight: 600; color: var(--c-fg);
}
.members-cell { max-width: 520px; }

/* ── Tab3 推理洞察 ── */
.insight-section { display: flex; flex-direction: column; gap: 14px; }
.prog-inline { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-secondary); }
.infer-summary { font-size: 11.5px; color: var(--c-secondary); }

.query-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(430px, 1fr)); gap: 12px; }
.query-card {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); padding: 12px 14px;
  display: flex; flex-direction: column; gap: 10px;
}
.query-title { font-size: 13px; font-weight: 600; color: var(--c-fg); }
.query-form { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cat-select.sm { padding: 4px 6px; font-size: 12px; }
.query-body { display: flex; flex-direction: column; gap: 10px; }

.chain-scroll { overflow-x: auto; border: 1px solid var(--c-border); border-radius: var(--radius-sm); }
.chain-svg { display: block; min-width: 100%; height: auto; }
.svg-node { fill: #1e293b; stroke: #38bdf8; stroke-width: 1.4; }
.svg-node.start { fill: #0c4a6e; stroke: #7dd3fc; stroke-width: 2; }
.svg-node-t { fill: var(--c-fg); font-size: 11px; text-anchor: middle; }
.svg-node-sub { fill: var(--c-secondary); font-size: 9.5px; text-anchor: middle; }
.svg-edge-label { fill: var(--c-secondary); font-size: 9.5px; text-anchor: middle; }

.chain-list { display: flex; flex-direction: column; gap: 4px; max-height: 220px; overflow-y: auto; }
.chain-item {
  display: flex; align-items: center; gap: 8px; padding: 4px 8px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  font-size: 12px; color: var(--c-fg);
}
.chain-conf {
  flex-shrink: 0; font-size: 11px; padding: 0 6px; border-radius: 999px;
  background: rgba(56, 189, 248, .14); color: #38bdf8; min-width: 38px; text-align: center;
}
.ent-i { color: var(--c-secondary); font-style: normal; font-size: 11px; }

.impact-group { display: flex; flex-direction: column; gap: 6px; }
.impact-head { font-size: 11.5px; color: var(--c-secondary); }
.tag-row { display: flex; flex-wrap: wrap; gap: 6px; }
.ent-tag {
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px;
  border: 1px solid var(--c-border); border-radius: 999px; font-size: 12px; color: var(--c-fg);
}
.ent-tag i { font-style: normal; font-size: 10px; color: var(--c-secondary); }
.ent-tag.warn { border-color: rgba(245, 158, 11, .45); }

.sug-box {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); padding: 12px 14px; display: flex; flex-direction: column; gap: 10px;
}
.sug-head { display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 600; }
.sug-list { display: flex; flex-direction: column; gap: 8px; max-height: 420px; overflow-y: auto; }
.sug-card {
  display: flex; flex-direction: column; gap: 6px; padding: 10px 12px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg, transparent);
}
.sug-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.sug-ent { font-weight: 600; color: var(--c-fg); }
.sug-rel { color: #38bdf8; font-size: 12px; }
.sug-conf {
  margin-left: auto; font-size: 11px; padding: 1px 8px; border-radius: 999px;
  background: rgba(56, 189, 248, .14); color: #38bdf8;
}
.sug-evidence { font-size: 11.5px; color: var(--c-secondary); }
.sug-actions { display: flex; gap: 8px; }
</style>
