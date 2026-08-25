<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, reactive, markRaw } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import WorkflowNode from './WorkflowNode.vue'
import {
  getWorkflow, updateWorkflow, fetchWorkflowPalette, runWorkflowStream,
  fetchEntities, fetchEntityServices, fetchWorkflowRuns, getWorkflowRun, deleteWorkflowRun,
} from '../../api'
import { useToast } from '../../composables/useToast'
import PythonEditor from './PythonEditor.vue'
import { TYPE_META } from './nodeMeta.js'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const wfId = route.params.workflowId
// 自定义节点组件映射（Vue Flow 按节点 type 渲染 WorkflowNode）
// markRaw：组件对象不进响应式代理（Vue Flow 内部会 h() 渲染，代理化组件触发性能 warning）
const nodeTypes = markRaw(Object.fromEntries(Object.keys(TYPE_META).map(t => [t, WorkflowNode])))
const { screenToFlowCoordinate, updateNode } = useVueFlow()

const DEFAULT_CONFIG = {
  start: { inputs: [] },
  end: { outputs: [] },
  agent: { agent_id: '', kb_id: '', skill_ids: [], query_template: '{{start.input}}' },
  service: { kb_id: '', entity_id: '', service_id: '', params: {} },
  llm: { system_prompt: '', prompt_template: '', structured_outputs: [] },
  condition: { operator: '==', left: '', right: '' },
  code: {
    code_text: 'def run(params, entity, context):\n    # params：本节点「参数(JSON)」中定义的数据（已渲染变量）\n    # var(节点id, 字段)：左侧变量拖拽后自动插入的安全读取函数\n    return {"summary": var("llm1", "text"), "params": params}',
    params: {},
    structured_outputs: [],
  },
}

// 各节点类型的默认输出字段（新节点预填，之后可手动增删）
const OUTPUT_FIELDS_DEFAULT = {
  start: [],
  end: [],
  agent: ['answer', 'chunks', 'entities'],
  service: ['success', 'data', 'error'],
  llm: ['text'],
  condition: ['result'],
  code: ['success', 'data', 'error'],
}
// 各类型固定输出（与后端 FIXED_OUTPUTS 对齐）：锁定显示、不可删除
const FIXED_OUTPUTS = {
  agent: ['answer', 'chunks', 'entities', 'subgraph'],
  service: ['success', 'data', 'error', 'stdout', 'duration_ms'],
  llm: ['text'],
  condition: ['result'],
  code: ['success', 'data', 'error', 'stdout', 'duration_ms'],
}
function isFixedField(type, f) { return (FIXED_OUTPUTS[type] || []).includes(f) }

// 把某个节点的可用输出拆分为「固定输出 / 自定义输出」，供下游节点分组展示
function groupedOutputFieldsOf(nodeLike) {
  const all = outputFieldsOf(nodeLike)
  const fixed = all.filter(f => isFixedField(nodeLike.type, f))
  const custom = all.filter(f => !isFixedField(nodeLike.type, f))
  return { fixed, custom }
}

// 结构化输出表顶部展示的固定字段（锁定行）
const FIXED_STRUCT_FIELDS = [
  { name: 'answer', type: 'string', desc: '完整回答文本（固定）' },
  { name: 'chunks', type: 'array', desc: '引用来源分片（固定）' },
  { name: 'entities', type: 'array', desc: '识别实体（固定）' },
]
const FIXED_STRUCT_FIELDS_LLM = [
  { name: 'text', type: 'string', desc: '模型生成文本（固定）' },
]
const FIXED_STRUCT_FIELDS_CODE = [
  { name: 'data', type: 'object', desc: 'return 返回的完整字典（固定）' },
  { name: 'success', type: 'boolean', desc: '是否执行成功（固定）' },
  { name: 'error', type: 'string', desc: '错误信息（固定）' },
  { name: 'stdout', type: 'string', desc: '标准输出日志（固定）' },
  { name: 'duration_ms', type: 'number', desc: '执行耗时（毫秒，固定）' },
]
// 常见固定输出字段的说明（用于 hover 提示）
const FIELD_DESC = {
  answer: '完整回答文本',
  chunks: '引用来源分片',
  entities: '识别实体',
  subgraph: '子图结果',
  success: '是否执行成功',
  data: '返回数据',
  error: '错误信息',
  stdout: '标准输出',
  duration_ms: '耗时（毫秒）',
  text: '模型生成文本',
  result: '判断结果',
}
// 节点声明输出变量 = 固定输出 ∪ output_fields ∪ 结构化输出自定义字段
// 配置了结构化输出的节点：非固定字段以 structured_outputs 为准（output_fields 里的历史残留会被剔除）
function outputFieldsOf(nodeLike) {
  const t = nodeLike.type
  const cfg = nodeLike.data?.config ?? nodeLike.config ?? {}
  const fixed = FIXED_OUTPUTS[t] || []
  let fields = Array.isArray(cfg.output_fields)
    ? [...cfg.output_fields]
    : [...(OUTPUT_FIELDS_DEFAULT[t] || [])]
  const struct = cfg.structured_outputs
  if ((t === 'agent' || t === 'llm' || t === 'code') && Array.isArray(struct)) {
    const structNames = struct.map(s => s?.name).filter(Boolean)
    fields = fields.filter(f => fixed.includes(f) || structNames.includes(f))
    for (const n of structNames) if (!fields.includes(n)) fields.push(n)
  }
  for (const f of fixed) if (!fields.includes(f)) fields.push(f)
  return fields
}

// 获取某个字段的说明（用于变量 hover 提示）
function fieldDesc(nodeLike, field) {
  const cfg = nodeLike.data?.config ?? nodeLike.config ?? {}
  const t = nodeLike.type
  // 配置了结构化输出的节点：自定义字段以 description 为准
  if ((t === 'agent' || t === 'llm' || t === 'code') && Array.isArray(cfg.structured_outputs)) {
    const s = cfg.structured_outputs.find(s => s.name === field)
    if (s?.description) return s.description
  }
  // 开始节点输入变量：label / description
  if (t === 'start' && Array.isArray(cfg.inputs)) {
    const inp = cfg.inputs.find(i => i.name === field)
    if (inp?.description) return inp.description
    if (inp?.label) return inp.label
  }
  // 固定字段说明
  const fixed = FIXED_STRUCT_FIELDS.find(x => x.name === field)
  if (fixed) return fixed.desc.replace('（固定）', '')
  if (FIELD_DESC[field]) return FIELD_DESC[field]
  return ''
}

// ── 自定义变量提示（替代原生 title，即时显示 + 可美化）──
const tooltip = ref({ visible: false, x: 0, y: 0, title: '', desc: '' })
let tooltipHideTimer = null

function positionTooltip(x, y) {
  const pad = 12
  const w = 280
  const h = 72
  let nx = x + pad
  let ny = y + pad
  if (nx + w > window.innerWidth) nx = Math.max(8, x - w - pad)
  if (ny + h > window.innerHeight) ny = Math.max(8, y - h - pad)
  return { x: nx, y: ny }
}

function showVarTooltip(e, nodeLike, field) {
  const desc = fieldDesc(nodeLike, field)
  if (!desc) return
  clearTimeout(tooltipHideTimer)
  const pos = positionTooltip(e.clientX, e.clientY)
  tooltip.value = {
    visible: true,
    x: pos.x,
    y: pos.y,
    title: `${nodeLike.id}.${field}`,
    desc
  }
}
function moveVarTooltip(e) {
  if (!tooltip.value.visible) return
  const pos = positionTooltip(e.clientX, e.clientY)
  tooltip.value.x = pos.x
  tooltip.value.y = pos.y
}
function hideVarTooltip() {
  tooltipHideTimer = setTimeout(() => { tooltip.value.visible = false }, 120)
}

const wfName = ref('')
const wfDesc = ref('')
const nodes = ref([])
const edges = ref([])
const selectedNodeId = ref(null)
const saving = ref(false)
const saved = ref(false)

const palette = ref({ node_types: [], kbs: [], skills: [], agents: [] })

// 实体服务节点：实体/服务下拉数据
const svcEntities = ref([])
const svcServices = ref([])

let nodeSeq = 0
let edgeSeq = 0

const selectedNode = computed(() => nodes.value.find(n => n.id === selectedNodeId.value) || null)
const selectedType = computed(() => selectedNode.value?.type || '')
const selectedConfig = computed(() => selectedNode.value?.data?.config || {})
// 沿连线反向收集「真正能流到当前节点」的祖先节点（含开始；不含自己）
const upstreamNodes = computed(() => {
  if (!selectedNodeId.value) return []
  const parents = new Map()
  for (const e of edges.value) {
    if (!parents.has(e.target)) parents.set(e.target, new Set())
    parents.get(e.target).add(e.source)
  }
  const seen = new Set()
  const queue = [...(parents.get(selectedNodeId.value) || [])]
  while (queue.length) {
    const id = queue.shift()
    if (seen.has(id)) continue
    seen.add(id)
    for (const p of parents.get(id) || []) queue.push(p)
  }
  return nodes.value.filter(n => seen.has(n.id))
})

// 结束节点输出映射的 key-value 行（与 config.outputs 双向同步）
const endRows = ref([])
function syncEndRows() {
  const outs = selectedConfig.value?.outputs
  endRows.value = Array.isArray(outs)
    ? outs.map(o => ({ name: o.name || '', value: o.value ?? '' }))
    : []
}
function flushEndRows() {
  if (!selectedNode.value) return
  selectedNode.value.data.config.outputs = endRows.value
    .filter(r => r.name.trim())
    .map(r => ({ name: r.name.trim(), value: r.value }))
}
function addEndRow(name = '', value = '') {
  endRows.value.push({ name, value })
  flushEndRows()
}
function removeEndRow(i) {
  endRows.value.splice(i, 1)
  flushEndRows()
}
// 从「可用变量」点选：追加一行并自动命名（去重）
function appendEndRowFromVar(id, field) {
  let name = field
  let n = 2
  while (endRows.value.some(r => r.name === name)) name = `${field}_${n++}`
  addEndRow(name, varRef(id, field))
}

// 实体服务节点选中的实体 → 服务列表
const enabledSkills = computed(() => palette.value.skills || [])

onMounted(async () => {
  try {
    const [wf, pal] = await Promise.all([getWorkflow(wfId), fetchWorkflowPalette()])
    wfName.value = wf.name
    wfDesc.value = wf.description || ''
    palette.value = pal
    const def = wf.definition || { nodes: [], edges: [] }
    nodes.value = (def.nodes || []).map(toFlowNode)
    // 防御：历史 id 撞车 bug 可能保存过重复 id 的节点 → 去重（保留最后一个，即后加的），
    // 并丢弃指向不存在节点的悬空连线，避免 Vue Flow 渲染异常（节点消失）
    const seenIds = new Set()
    nodes.value = nodes.value.filter(n => {
      if (seenIds.has(n.id)) return false
      seenIds.add(n.id)
      return true
    })
    edges.value = (def.edges || []).map(toFlowEdge).filter(e => seenIds.has(e.source) && seenIds.has(e.target))
    // 序号起点取已有 id 中的最大序号（而非节点数），避免删除过节点后 id 撞车导致新节点覆盖旧节点
    const maxNodeSeq = Math.max(0, ...nodes.value.map(n => parseInt(String(n.id).replace(/^n/, ''), 10) || 0))
    const maxEdgeSeq = Math.max(0, ...edges.value.map(e => parseInt(String(e.id).replace(/^e/, ''), 10) || 0))
    nodeSeq = maxNodeSeq
    edgeSeq = maxEdgeSeq
    // 恢复最近一次运行的状态与日志（刷新页面后不丢）
    await restoreLastRun()
    // 预拉历史运行列表（不阻塞）
    refreshHistory()
  } catch (err) {
    toast.error(`加载失败: ${err.message}`)
  }
})

// 拉取最近一条运行记录，回放节点状态/输出/日志卡片
async function restoreLastRun() {
  try {
    const runs = await fetchWorkflowRuns(wfId)
    if (!runs?.length) return
    const last = runs[0]
    const run = await getWorkflowRun(wfId, last.id)
    const states = run.node_states || {}
    const hasAny = Object.values(states).some(s => s && s.status && s.status !== 'running')
    if (!hasAny) return

    logs.value = [{ kind: 'meta', text: `上次运行 · run ${run.id} · ${run.status === 'succeeded' ? '成功' : run.status === 'failed' ? '失败' : run.status} · ${run.duration_ms}ms` }]
    for (const n of nodesInRunOrder(states)) {
      const st = states[n.id]
      if (!st) continue
      n.data.status = st.status
      n.data.output = st.output ?? null
      if (st.duration_ms != null) n.data.elapsedText = fmtElapsed(st.duration_ms)
      logs.value.push({
        kind: 'node', node_id: n.id,
        title: st.title || n.data?.title || n.id,
        status: st.status,
        input: st.input,
        output: st.output ?? null,
        summary: st.summary,
        error: st.error,
        duration_ms: st.duration_ms,
      })
    }
    if (logs.value.length > 1) consoleCollapsed.value = false
  } catch { /* 静默：恢复失败不影响编辑 */ }
}

function toFlowNode(n) {
  return {
    id: n.id,
    type: n.type,
    position: n.position || { x: 0, y: 0 },
    data: { nodeType: n.type, title: n.title, config: n.config || {}, status: '' },
  }
}
function toFlowEdge(e) {
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.handle && e.handle !== 'default' ? e.handle : undefined,
  }
}

// 按有向边拓扑排序（用于无 started_at 的历史运行回显兜底）
function topoSortNodes(nodeList, edgeList) {
  const inDegree = {}
  const adj = {}
  for (const n of nodeList) { inDegree[n.id] = 0; adj[n.id] = [] }
  for (const e of edgeList) {
    if (adj[e.source]) adj[e.source].push(e.target)
    if (inDegree[e.target] != null) inDegree[e.target]++
  }
  const queue = nodeList.filter(n => inDegree[n.id] === 0).map(n => n.id)
  const result = []
  while (queue.length) {
    const id = queue.shift()
    const n = nodeList.find(x => x.id === id)
    if (n) result.push(n)
    for (const next of adj[id] || []) {
      inDegree[next]--
      if (inDegree[next] === 0) queue.push(next)
    }
  }
  const seen = new Set(result.map(n => n.id))
  for (const n of nodeList) if (!seen.has(n.id)) result.push(n)
  return result
}

// 历史运行节点排序：优先按后端 started_at，无则按拓扑序
function nodesInRunOrder(states) {
  const hasTimes = nodes.value.some(n => states[n.id]?.started_at)
  if (!hasTimes) return topoSortNodes(nodes.value, edges.value)
  return [...nodes.value]
    .filter(n => states[n.id]?.started_at)
    .sort((a, b) => new Date(states[a.id].started_at) - new Date(states[b.id].started_at))
}

function fromFlowNode(n) {
  return { id: n.id, type: n.type, title: n.data?.title || TYPE_META[n.type]?.name, position: n.position, config: n.data?.config || {} }
}
function fromFlowEdge(e) {
  return { id: e.id, source: e.source, target: e.target, handle: e.sourceHandle || 'default' }
}

// ───── 画布交互 ─────
function addNodeAt(type, position) {
  let id = `n${++nodeSeq}`
  // 兜底：id 已存在（历史数据手工编辑等）则继续顺延，绝不生成重复 id
  while (nodes.value.some(n => n.id === id)) id = `n${++nodeSeq}`
  const config = JSON.parse(JSON.stringify(DEFAULT_CONFIG[type]))
  config.output_fields = [...(OUTPUT_FIELDS_DEFAULT[type] || [])]
  const pos = position || { x: 100 + (nodeSeq % 4) * 220, y: 80 + (nodeSeq % 3) * 160 }
  nodes.value.push({
    id,
    type,
    position: pos,
    data: {
      nodeType: type,
      title: TYPE_META[type].name,
      config,
      status: '',
    },
  })
  selectNode(id)
}
function addNode(type) { addNodeAt(type, null) }
function onDragStart(event, type) {
  if (event.dataTransfer) {
    event.dataTransfer.setData('application/vueflow', type)
    event.dataTransfer.effectAllowed = 'move'
  }
}
function onDrop(event) {
  const type = event.dataTransfer?.getData('application/vueflow')
  if (!type) return
  const position = screenToFlowCoordinate({ x: event.clientX, y: event.clientY })
  addNodeAt(type, position)
}
function onDragOver(event) {
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}
function selectNode(id) {
  selectedNodeId.value = id
  if (id) {
    const n = nodes.value.find(x => x.id === id)
    if (n?.type === 'service') loadSvc(n.data.config)
  }
}
function onNodeClick({ node }) { selectNode(node.id) }
function onPaneClick() { selectedNodeId.value = null }
function onConnect(conn) {
  const { source, target } = conn
  // 拦截非法连线：自环 / start 入边 / end 出边 / 成环 / 重复边
  if (source === target) { toast.error('节点不能连接自己'); return }
  const srcNode = nodes.value.find(n => n.id === source)
  const tgtNode = nodes.value.find(n => n.id === target)
  if (tgtNode?.type === 'start') { toast.error('「开始」节点之前不能再连接其他节点'); return }
  if (srcNode?.type === 'end') { toast.error('「结束」节点之后不能再连接其他节点'); return }
  if (edges.value.some(e => e.source === source && e.target === target)) { toast.error('这两个节点之间已存在连线'); return }
  // 成环检测：从 target 沿下游走，若能回到 source 则成环
  const children = new Map()
  for (const e of edges.value) {
    if (!children.has(e.source)) children.set(e.source, new Set())
    children.get(e.source).add(e.target)
  }
  const stack = [target]
  const seen = new Set()
  while (stack.length) {
    const cur = stack.pop()
    if (cur === source) { toast.error('不能形成循环连线'); return }
    if (seen.has(cur)) continue
    seen.add(cur)
    for (const c of children.get(cur) || []) stack.push(c)
  }
  let id = `e${++edgeSeq}`
  while (edges.value.some(e => e.id === id)) id = `e${++edgeSeq}`
  edges.value.push({
    id,
    source,
    target,
    sourceHandle: conn.sourceHandle || undefined,
  })
}

// ── 一键整理：DAG 分层布局（按拓扑层级排布，同层纵向对齐） ──
function autoLayout() {
  if (!nodes.value.length) return
  // 入度与邻接（按当前 edges 计算）
  const indeg = new Map(nodes.value.map(n => [n.id, 0]))
  const children = new Map(nodes.value.map(n => [n.id, []]))
  for (const e of edges.value) {
    if (indeg.has(e.target)) indeg.set(e.target, (indeg.get(e.target) || 0) + 1)
    if (children.has(e.source)) children.get(e.source).push(e.target)
  }
  // Kahn 分层：同层节点放同一列
  const layers = []
  let queue = [...indeg.entries()].filter(([, d]) => d === 0).map(([id]) => id)
  const done = new Set()
  while (queue.length) {
    layers.push([...queue])
    for (const id of queue) done.add(id)
    const next = []
    for (const id of queue) {
      for (const c of children.get(id) || []) {
        indeg.set(c, (indeg.get(c) || 0) - 1)
        if (indeg.get(c) === 0 && !done.has(c)) next.push(c)
      }
    }
    queue = [...new Set(next)]
  }
  // 有环时剩余节点兜底放最后一列
  const rest = nodes.value.filter(n => !done.has(n.id)).map(n => n.id)
  if (rest.length) layers.push(rest)
  // 布局参数：列距 260，行距 90；start 固定在第一列
  const COL_GAP = 260, ROW_GAP = 96
  layers.forEach((layer, li) => {
    layer.forEach((id, ri) => {
      const n = nodes.value.find(x => x.id === id)
      if (n) n.position = { x: 40 + li * COL_GAP, y: 60 + ri * ROW_GAP - (layer.length - 1) * ROW_GAP / 2 }
    })
  })
  toast.success('已整理布局')
}

function deleteSelected() {
  if (selectedNodeId.value) deleteNodeById(selectedNodeId.value)
}
function deleteNodeById(id) {
  // 删除前：清理下游对被删节点的变量引用（输出映射 / 条件 / 模板 / 参数）
  const ref = new RegExp(`\\{\\{\\s*${id}\\.[\\w.\\[\\]-]+\\s*\\}\\}`, 'g')
  const refShort = new RegExp(`\\{\\{\\s*${id}\\s*\\.`, 'g')
  let cleaned = []
  for (const n of nodes.value) {
    if (n.id === id) continue
    const cfg = n.data?.config
    if (!cfg) continue
    // 结束节点：删引用它的输出映射行
    if (Array.isArray(cfg.outputs)) {
      const before = cfg.outputs.length
      cfg.outputs = cfg.outputs.filter(r => !(typeof r.value === 'string' && (ref.test(r.value) || r.value.includes(`{{${id}.`))))
      ref.lastIndex = 0
      if (cfg.outputs.length !== before) cleaned.push(`${n.data?.title || n.id} 的输出映射`)
    }
    // 字符串型配置：问题模板 / prompt / 条件左右值
    for (const k of ['query_template', 'prompt_template', 'left', 'right']) {
      if (typeof cfg[k] === 'string' && cfg[k].includes(`{{${id}.`)) {
        cfg[k] = cfg[k].replace(refShort, '')
        cleaned.push(`${n.data?.title || n.id} 的 ${k}`)
      }
    }
    refShort.lastIndex = 0
  }
  nodes.value = nodes.value.filter(n => n.id !== id)
  edges.value = edges.value.filter(e => e.source !== id && e.target !== id)
  if (selectedNodeId.value === id) selectedNodeId.value = null
  closeContextMenu()
  if (cleaned.length) toast.info(`已删除节点 ${id}，并清理了：${cleaned.join('、')}`)
}
function deleteEdgeById(id) {
  edges.value = edges.value.filter(e => e.id !== id)
  closeContextMenu()
}
function onNodeContextMenu({ event, node }) {
  event.preventDefault()
  contextMenu.visible = true
  contextMenu.x = event.clientX
  contextMenu.y = event.clientY
  contextMenu.type = 'node'
  contextMenu.id = node.id
}
function onEdgeContextMenu({ event, edge }) {
  event.preventDefault()
  contextMenu.visible = true
  contextMenu.x = event.clientX
  contextMenu.y = event.clientY
  contextMenu.type = 'edge'
  contextMenu.id = edge.id
}
function closeContextMenu() { contextMenu.visible = false }
function clearLogs() { logs.value = [] }

// ───── 运行历史（保留最近 N 次） ─────
const KEEP_RUNS = 10
const logTab = ref('current')           // 'current' | 'history'
const runHistory = ref([])              // 列表（摘要）
const loadingHistory = ref(false)
const historyDetail = ref(null)         // 正在查看详情的 run（全量）
const loadingDetail = ref(false)
const deletingRunId = ref('')

async function refreshHistory() {
  loadingHistory.value = true
  try {
    runHistory.value = await fetchWorkflowRuns(wfId)
  } catch {
    runHistory.value = []
  } finally {
    loadingHistory.value = false
  }
}

async function openRunDetail(runId) {
  loadingDetail.value = true
  historyDetail.value = { id: runId, loading: true }
  try {
    historyDetail.value = await getWorkflowRun(wfId, runId)
  } catch (e) {
    toast.error(`加载详情失败: ${e.message}`)
    historyDetail.value = null
  } finally {
    loadingDetail.value = false
  }
}
function closeRunDetail() { historyDetail.value = null }

// 把一条历史 run 的 node_states 回放到画布（节点染色 + 抽开启节点输出面板）
async function replayRun(runId) {
  try {
    const run = await getWorkflowRun(wfId, runId)
    const states = run.node_states || {}
    let count = 0
    const orderedNodes = nodesInRunOrder(states)
    for (const n of orderedNodes) {
      const st = states[n.id]
      if (!st || !st.status || st.status === 'running') continue
      n.data.status = st.status
      n.data.output = st.output ?? null
      if (st.duration_ms != null) n.data.elapsedText = fmtElapsed(st.duration_ms)
      count++
    }
    if (count) {
      consoleCollapsed.value = false
      logTab.value = 'current'
      clearLogs()
      logs.value = [{ kind: 'meta', text: `回放历史运行 · run ${run.id} · ${fmtStatusText(run.status)} · ${run.duration_ms}ms` }]
      for (const n of orderedNodes) {
        const st = states[n.id]
        if (!st || !st.status) continue
        logs.value.push({
          kind: 'node', node_id: n.id,
          title: st.title || n.data?.title || n.id,
          status: st.status, input: st.input, output: st.output,
          summary: st.summary, error: st.error, duration_ms: st.duration_ms,
        })
      }
      toast.success(`已回放 ${count} 个节点状态`)
    } else {
      toast.info('该运行无节点状态可回放')
    }
    closeRunDetail()
  } catch (e) {
    toast.error(`回放失败: ${e.message}`)
  }
}

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

async function deleteRun(runId) {
  if (!confirm('确定删除这条运行记录？删除后无法恢复。')) return
  deletingRunId.value = runId
  try {
    await deleteWorkflowRun(wfId, runId)
    runHistory.value = runHistory.value.filter(r => r.id !== runId)
    if (historyDetail.value?.id === runId) historyDetail.value = null
    toast.success('已删除')
  } catch (e) {
    toast.error(`删除失败: ${e.message}`)
  } finally {
    deletingRunId.value = ''
  }
}

// 进入历史 Tab 时自动拉取
watch(logTab, (t) => { if (t === 'history') refreshHistory() })

// ───── 实体服务下拉 ─────
async function loadSvc(cfg) {
  svcEntities.value = []
  svcServices.value = []
  if (cfg.kb_id) {
    try {
      const r = await fetchEntities({ kb_id: cfg.kb_id, page_size: 200 })
      svcEntities.value = r.items || []
    } catch {}
  }
  if (cfg.entity_id) {
    try { svcServices.value = await fetchEntityServices(cfg.entity_id) } catch {}
  }
}
async function onSvcKbChange() {
  selectedConfig.value.entity_id = ''
  selectedConfig.value.service_id = ''
  await loadSvc(selectedConfig.value)
}
async function onSvcEntityChange() {
  svcServices.value = []
  try { svcServices.value = await fetchEntityServices(selectedConfig.value.entity_id) } catch {}
}

// ───── 变量引用 ─────
function jsonText(field) {
  const v = selectedConfig.value?.[field]
  return JSON.stringify(v ?? (field === 'params' ? {} : []), null, 2)
}
function setJson(field, text) {
  if (!selectedNode.value) return
  try { selectedNode.value.data.config[field] = JSON.parse(text) } catch {}
}
function varRef(id, field) { return `{{${id}.${field}}}` }
const varRefPlaceholder = '{{节点.字段}}'
function varPyRef(id, field) { return `var("${id}", "${field}")` }
function onVarDragStart(ev, id, field) {
  ev.dataTransfer.setData('text/plain', varPyRef(id, field))
  ev.dataTransfer.setData('application/x-wf-var', JSON.stringify({ node: id, field }))
  ev.dataTransfer.effectAllowed = 'copy'
}
function copyText(text) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(() => toast.success('已复制 ' + text)).catch(() => {})
  } else {
    toast.info(text)
  }
}
// 把变量引用插入到指定配置字段的光标处 / 追加到末尾
function insertRefInto(field, ref) {
  const cfg = selectedConfig.value
  if (cfg == null) return
  cfg[field] = (cfg[field] || '') + ref
  toast.success('已插入 ' + ref)
}

// ── 输出变量手动管理：回车添加 / 点 × 删除（固定字段除外） ──
const outputFieldInput = ref('')
// 展示列表 = 固定字段（在前，锁定）∪ 手动字段
function displayOutputFields(nodeLike) {
  const type = nodeLike.type
  const manual = outputFieldsOf(nodeLike).filter(f => !isFixedField(type, f))
  return [...(FIXED_OUTPUTS[type] || []), ...manual]
}
function addOutputField() {
  const raw = outputFieldInput.value.trim()
  if (!raw || !selectedNode.value) return
  const cfg = selectedNode.value.data.config
  if (!Array.isArray(cfg.output_fields)) cfg.output_fields = [...(FIXED_OUTPUTS[selectedNode.value.type] || [])]
  for (const f of raw.split(/[\s,，]+/).filter(Boolean)) {
    if (!cfg.output_fields.includes(f)) cfg.output_fields.push(f)
  }
  outputFieldInput.value = ''
}
function removeOutputField(i) {
  const cfg = selectedNode.value?.data?.config
  if (!cfg) return
  if (!Array.isArray(cfg.output_fields)) cfg.output_fields = [...(FIXED_OUTPUTS[selectedNode.value.type] || [])]
  // 按显示列表定位：固定字段不可删（前端隐藏 ×，这里再兜底拦截）
  const shown = displayOutputFields(selectedNode.value)
  const target = shown[i]
  if (target == null || isFixedField(selectedNode.value.type, target)) return
  const idx = cfg.output_fields.indexOf(target)
  if (idx >= 0) cfg.output_fields.splice(idx, 1)
}
function toggleSkill(id) {
  const cfg = selectedConfig.value
  if (!cfg) return
  const arr = cfg.skill_ids || (cfg.skill_ids = [])
  const i = arr.indexOf(id)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(id)
}
function insertVar(field) {
  const token = `{{${selectedNodeId.value}.}}`
  const cfg = selectedConfig.value
  cfg[field] = (cfg[field] || '') + token
}

// 保存前校验：扫描所有 {{节点.字段}} 引用——节点不存在或字段不在其可用输出集 → 报错拦下
function validateVarRefs() {
  const VAR = /\{\{\s*([\w-]+)\.([\w.\[\]-]*)\s*\}\}/g
  const fieldsByNode = new Map(nodes.value.map(n => [n.id, outputFieldsOf(n)]))
  const nameOf = new Map(nodes.value.map(n => [n.id, n.data?.title || n.id]))
  const errors = []
  for (const n of nodes.value) {
    const cfg = n.data?.config
    if (!cfg) continue
    const scan = (where, val) => {
      if (typeof val !== 'string') return
      let m
      VAR.lastIndex = 0
      while ((m = VAR.exec(val)) !== null) {
        const [, refId, refField] = m
        const root = refField.split('.')[0]
        if (!fieldsByNode.has(refId)) {
          errors.push(`节点「${nameOf.get(n.id)}」${where} 引用了不存在的节点 {{${refId}.${refField}}}`)
        } else if (root && !fieldsByNode.get(refId).includes(root)) {
          errors.push(`节点「${nameOf.get(n.id)}」${where} 引用了 {{${refId}.${root}}}，但「${nameOf.get(refId)}」没有输出字段 ${root}`)
        }
      }
    }
    scan('问题模板', cfg.query_template)
    scan('Prompt', cfg.prompt_template)
    scan('条件', cfg.left)
    scan('条件', cfg.right)
    if (Array.isArray(cfg.outputs)) cfg.outputs.forEach((r, i) => scan(`输出映射第${i + 1}行`, r?.value))
    if (cfg.params && typeof cfg.params === 'object') {
      for (const [k, v] of Object.entries(cfg.params)) scan(`参数 ${k}`, String(v))
    }
  }
  if (errors.length) {
    toast.error(errors[0] + (errors.length > 1 ? `（共 ${errors.length} 处引用问题）` : ''))
    return false
  }
  return true
}

// ───── 保存 ─────
async function save() {
  if (!wfName.value.trim()) { toast.error('名称不能为空'); return }
  // 保存前先同步一次结构化输出字段到 output_fields，避免运行/保存时遗漏
  if (selectedNode.value && (selectedNode.value.type === 'agent' || selectedNode.value.type === 'llm' || selectedNode.value.type === 'code')) {
    flushStructRows()
  }
  if (!validateStructRows()) return
  if (!validateVarRefs()) return
  saving.value = true
  const definition = {
    nodes: nodes.value.map(fromFlowNode),
    edges: edges.value.map(fromFlowEdge),
  }
  try {
    await updateWorkflow(wfId, { name: wfName.value, description: wfDesc.value, definition })
    saved.value = true
    toast.success('已保存')
    setTimeout(() => { saved.value = false }, 2000)
  } catch (err) {
    toast.error(`保存失败: ${err.message}`)
  }
  saving.value = false
}

// ───── 运行 ─────
const running = ref(false)
const logs = ref([])          // {kind, text, node_id, title, status, summary, error, output, duration_ms}
const expandedLog = ref(-1)
const runModal = ref(false)
const runInputs = reactive({})
const consoleCollapsed = ref(true)
const contextMenu = reactive({ visible: false, x: 0, y: 0, type: '', id: '' })
// 运行中节点：node_id -> 起始时间戳（用于节点/日志的实时计时）
const runningSince = reactive({})
const nowTick = ref(Date.now())
let tickTimer = null
function fmtElapsed(ms) {
  if (ms == null) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}m${s % 60}s`
}
const startInputs = computed(() => {
  const s = nodes.value.find(n => n.type === 'start')
  const inputs = s?.data?.config?.inputs
  return Array.isArray(inputs) ? inputs : []
})

function openRunModal() {
  Object.keys(runInputs).forEach(k => delete runInputs[k])
  for (const it of startInputs.value) runInputs[it.name] = it.default ?? ''
  runModal.value = true
}

function setStatus(nodeId, status, durationMs = null) {
  const n = nodes.value.find(x => x.id === nodeId)
  if (n) {
    n.data.status = status
    if (status === 'running') {
      n.data.elapsedText = '0s'
      n.data.step = ''
      n.data.steps = []
    }
    // succeeded/failed：定格最终耗时（durationMs 优先，缺省用本地计时）
    else if (status === 'succeeded' || status === 'failed') {
      n.data.elapsedText = fmtElapsed(durationMs ?? (runningSince[nodeId] ? Date.now() - runningSince[nodeId] : null))
      n.data.step = ''
      n.data.steps = []
    }
    else {
      n.data.elapsedText = ''
      n.data.step = ''
      n.data.steps = []
    }
  }
  if (status === 'running') runningSince[nodeId] = Date.now()
  else delete runningSince[nodeId]
}
function clearStatus() {
  nodes.value.forEach(n => { n.data.status = ''; n.data.output = null; n.data.step = ''; n.data.steps = [] })
  Object.keys(runningSince).forEach(k => delete runningSince[k])
  logs.value = []
}

async function startRun() {
  runModal.value = false
  clearStatus()
  running.value = true
  expandedLog.value = -1
  consoleCollapsed.value = false
  try {
    await runWorkflowStream(wfId, { ...runInputs }, {
      onStarted(d) { logs.value.push({ kind: 'meta', text: `工作流开始 · run ${d.run_id}` }) },
      onNodeStarted(d) {
        setStatus(d.node_id, 'running')
        logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'running', input: d.input })
      },
      onNodeProgress(d) {
        // 心跳：更新对应 running 日志行的已运行时长
        const line = [...logs.value].reverse().find(l => l.kind === 'node' && l.node_id === d.node_id && l.status === 'running')
        if (line) line.elapsed_ms = d.elapsed_ms
        // 流式执行：把实时输出 / 当前步骤挂载到节点上，节点卡片可动态渲染
        if (d.output || d.step) {
          updateNode(d.node_id, (node) => {
            if (!node.data) node.data = {}
            if (d.output) node.data.output = d.output
            if (d.step) {
              node.data.step = d.step
              const arr = [...(node.data.steps || [])]
              if (arr[arr.length - 1] !== d.step) arr.push(d.step)
              node.data.steps = arr
            }
          })
        }
      },
      onNodeFinished(d) {
        setStatus(d.node_id, 'succeeded', d.duration_ms)
        // 输出注入节点 data：卡片上直接展示 answer 摘要 + 自定义字段（count 等）
        const n = nodes.value.find(x => x.id === d.node_id)
        if (n) n.data.output = d.output
        // 把之前 running 的日志行更新为 succeeded，避免任务结束后仍显示闪烁的运行态
        const line = [...logs.value].reverse().find(l => l.kind === 'node' && l.node_id === d.node_id && l.status === 'running')
        if (line) {
          Object.assign(line, { status: 'succeeded', summary: d.summary, output: d.output, duration_ms: d.duration_ms })
        } else {
          logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'succeeded', summary: d.summary, output: d.output, duration_ms: d.duration_ms })
        }
      },
      onNodeFailed(d) {
        setStatus(d.node_id, 'failed', d.duration_ms)
        const line = [...logs.value].reverse().find(l => l.kind === 'node' && l.node_id === d.node_id && l.status === 'running')
        if (line) {
          Object.assign(line, { status: 'failed', error: d.error, duration_ms: d.duration_ms })
        } else {
          logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'failed', error: d.error, duration_ms: d.duration_ms })
        }
      },
      onNodeSkipped(d) {
        setStatus(d.node_id, 'skipped')
        const line = [...logs.value].reverse().find(l => l.kind === 'node' && l.node_id === d.node_id && l.status === 'running')
        if (line) {
          Object.assign(line, { status: 'skipped' })
        } else {
          logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'skipped' })
        }
      },
      onFinished(d) {
        logs.value.push({ kind: 'meta', text: `工作流${d.status === 'failed' ? '失败' : '完成'} · 耗时 ${d.duration_ms}ms` })
        // 运行结束后刷新历史列表（不阻塞 UI）
        refreshHistory()
      },
    })
  } catch (err) {
    toast.error(`运行失败: ${err.message}`)
  }
  running.value = false
}

function statusIcon(s) {
  if (s === 'running') return '▶'
  if (s === 'succeeded') return '✓'
  if (s === 'failed') return '✗'
  if (s === 'skipped') return '⤼'
  return '·'
}
function nodeTypeOf(log) {
  const n = nodes.value.find(n => n.id === log.node_id)
  return n?.type || n?.data?.nodeType || ''
}
function logDetail(l) {
  if (l.error) return l.error
  const o = l.output
  if (o == null) return ''
  if (typeof o === 'string') return o
  try { return JSON.stringify(o, null, 2) } catch { return String(o) }
}
function toggleLog(i) {
  expandedLog.value = expandedLog.value === i ? -1 : i
}

// 切换节点时同步实体服务下拉 / 结束节点 key-value 行 / 智能体自定义输出行
watch(selectedNodeId, (id) => {
  if (id) {
    const n = nodes.value.find(x => x.id === id)
    if (n?.type === 'service') loadSvc(n.data.config)
    if (n?.type === 'end') syncEndRows()
    if (n?.type === 'agent' || n?.type === 'llm' || n?.type === 'code') { syncStructRows() }
  }
})

// 右键菜单：点击空白处关闭 + 运行中计时 tick
onMounted(() => window.addEventListener('click', closeContextMenu))
onMounted(() => { tickTimer = setInterval(() => { nowTick.value = Date.now() }, 1000) })
onBeforeUnmount(() => {
  window.removeEventListener('click', closeContextMenu)
  clearInterval(tickTimer)
  endDrawerResize()
  endConsoleResize()
})

// ── 日志控制台高度拖拽（顶边缘把手，120–60% 视口，记忆） ──
const CONSOLE_H_KEY = 'knowsource.workflow.consoleHeight'
const consoleHeight = ref(parseInt(localStorage.getItem(CONSOLE_H_KEY) || '', 10) || 220)
let cResizing = false
let cStartY = 0      // 按下时的鼠标 y
let cStartH = 0      // 按下时的控制台高度
function startConsoleResize(e) {
  cResizing = true
  cStartY = e.clientY
  cStartH = consoleHeight.value
  e.preventDefault()
  window.addEventListener('pointermove', onConsoleResize)
  window.addEventListener('pointerup', endConsoleResize)
  document.body.style.cursor = 'row-resize'
  document.body.style.userSelect = 'none'
}
function onConsoleResize(e) {
  if (!cResizing) return
  // 增量式：按位移差调整（向上拖 y 变小 → 高度增加），手柄跟手、无跳变
  const delta = cStartY - e.clientY
  const h = Math.min(Math.round(window.innerHeight * 0.6), Math.max(120, cStartH + delta))
  consoleHeight.value = h
}
function endConsoleResize() {
  if (!cResizing) return
  cResizing = false
  window.removeEventListener('pointermove', onConsoleResize)
  window.removeEventListener('pointerup', endConsoleResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  localStorage.setItem(CONSOLE_H_KEY, String(consoleHeight.value))
}

// ── 抽屉宽度拖拽（左边缘把手，280–560px，记忆到 localStorage） ──
const DRAWER_W_KEY = 'knowsource.workflow.drawerWidth'
const drawerWidth = ref(parseInt(localStorage.getItem(DRAWER_W_KEY) || '', 10) || 340)
const drawerCollapsed = ref(false)
let resizing = false
function startDrawerResize(e) {
  resizing = true
  e.preventDefault()
  window.addEventListener('pointermove', onDrawerResize)
  window.addEventListener('pointerup', endDrawerResize)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
function onDrawerResize(e) {
  if (!resizing) return
  // 抽屉贴右侧：宽度 = 视口右边界 - 指针 x（减去主区右 padding 的近似余量）
  const w = Math.min(560, Math.max(280, window.innerWidth - e.clientX - 40))
  drawerWidth.value = w
}
function endDrawerResize() {
  if (!resizing) return
  resizing = false
  window.removeEventListener('pointermove', onDrawerResize)
  window.removeEventListener('pointerup', endDrawerResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  localStorage.setItem(DRAWER_W_KEY, String(drawerWidth.value))
}
function nodeElapsed(nid) {
  const since = runningSince[nid]
  return since == null ? null : nowTick.value - since
}
// 智能体节点结构化输出（structured_outputs：[{name,type,description}]）行编辑
const structRows = ref([])
function syncStructRows() {
  const arr = selectedConfig.value?.structured_outputs
  structRows.value = Array.isArray(arr)
    ? arr.map(f => ({ name: f.name || '', type: f.type || 'string', description: f.description || '' }))
    : []
}
function flushStructRows() {
  if (!selectedNode.value) return
  const cfg = selectedNode.value.data.config
  cfg.structured_outputs = structRows.value
    .filter(r => r.name.trim())
    .map(r => ({ name: r.name.trim(), type: r.type, description: r.description.trim() }))
  // 同步：结构化自定义字段并入 output_fields（固定字段始终保留），下游可用变量即时更新
  if (!Array.isArray(cfg.output_fields)) cfg.output_fields = [...(FIXED_OUTPUTS[selectedNode.value.type] || [])]
  const fixed = FIXED_OUTPUTS[selectedNode.value.type] || []
  cfg.output_fields = [
    ...fixed,
    ...cfg.output_fields.filter(f => !fixed.includes(f) && cfg.structured_outputs.some(s => s.name === f)),
    ...cfg.structured_outputs.map(s => s.name).filter(n => !cfg.output_fields.includes(n) && !fixed.includes(n)),
  ]
}
// 保存前校验：agent/llm 节点结构化输出里字段名填了但说明为空 → 提示（说明是大模型识别字段的关键）
function validateStructRows() {
  for (const n of nodes.value) {
    if (n.type !== 'agent' && n.type !== 'llm') continue
    for (const f of n.data.config?.structured_outputs || []) {
      if (f.name && !f.description) {
        toast.error(`节点「${n.data.title || n.id}」结构化输出字段 ${f.name} 缺少说明（大模型靠它识别输出）`)
        return false
      }
    }
  }
  return true
}

watch(nowTick, () => {
  for (const nid of Object.keys(runningSince)) {
    const n = nodes.value.find(x => x.id === nid)
    if (n && n.data.status === 'running') n.data.elapsedText = fmtElapsed(nodeElapsed(nid))
  }
})
</script>

<template>
  <div class="wf-editor">
    <!-- 工具栏 -->
    <div class="wf-toolbar">
      <button class="btn" @click="router.push('/workflows')">← 返回</button>
      <input class="wf-name" v-model="wfName" placeholder="工作流名称">
      <span v-if="saved" class="wf-saved">已保存</span>
      <div class="spacer"></div>
      <button class="btn" @click="autoLayout" title="按拓扑层级自动排列节点">一键整理</button>
      <button class="btn" @click="consoleCollapsed = !consoleCollapsed">日志</button>
      <button class="btn" @click="save" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</button>
      <button class="btn primary" @click="openRunModal" :disabled="running">▶ 运行</button>
    </div>

    <div class="wf-body">
      <!-- 左：节点面板 -->
      <aside class="wf-palette">
        <div class="pal-group">节点</div>
        <button v-for="t in palette.node_types" :key="t.type" class="pal-item" draggable="true" @dragstart="onDragStart($event, t.type)" @click="addNode(t.type)">
          <span class="pal-ico" :style="{ background: TYPE_META[t.type]?.color || '#64748b' }" v-html="TYPE_META[t.type]?.icon"></span>
          <span class="pal-name">{{ t.name }}</span>
        </button>
        <div class="pal-hint">点击节点加入画布，拖拽连线组装。变量用 <code v-pre>{{节点.字段}}</code> 引用上游输出。</div>
      </aside>

      <!-- 中：画布 -->
      <div class="wf-canvas-wrap">
        <VueFlow
          v-model:nodes="nodes"
          v-model:edges="edges"
          :node-types="nodeTypes"
          :min-zoom="0.3"
          :max-zoom="1.8"
          :connection-radius="80"
          @node-click="onNodeClick"
          @pane-click="onPaneClick"
          @node-context-menu="onNodeContextMenu"
          @edge-context-menu="onEdgeContextMenu"
          @connect="onConnect"
          @drop="onDrop"
          @dragover="onDragOver"
        >
          <Background :gap="20" />
          <Controls />
        </VueFlow>
      </div>

      <!-- 右：配置抽屉 -->
      <aside class="wf-drawer" :class="{ collapsed: drawerCollapsed }" :style="{ width: drawerCollapsed ? '28px' : drawerWidth + 'px' }">
        <div class="drawer-resizer" v-if="!drawerCollapsed" title="拖拽调节宽度" @pointerdown="startDrawerResize"></div>
        <button type="button" class="drawer-toggle" :title="drawerCollapsed ? '展开配置' : '收起配置'" @click="drawerCollapsed = !drawerCollapsed">
          <svg v-if="drawerCollapsed" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 5 9 12 15 19" />
            <polyline points="21 5 15 12 21 19" />
          </svg>
          <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="9 5 15 12 9 19" />
            <polyline points="3 5 9 12 3 19" />
          </svg>
        </button>
        <template v-if="!drawerCollapsed && selectedNode">
          <div class="dr-head">
            <span class="dr-ico" :style="{ background: TYPE_META[selectedType]?.color || '#64748b' }" v-html="TYPE_META[selectedType]?.icon"></span>
            <div class="dr-head-t">
              <div class="dr-title">{{ TYPE_META[selectedType]?.name }}</div>
              <div class="dr-sub">{{ selectedNodeId }}</div>
            </div>
            <button class="btn sm" @click="deleteSelected">删除</button>
          </div>

          <div class="dr-body">
            <!-- ═══ 分区一：智能体配置（节点名称 + 该类型专属配置） ═══ -->
            <div class="section-title">{{ TYPE_META[selectedType]?.name }}配置</div>
            <div class="field">
              <label>节点名称</label>
              <input type="text" v-model="selectedNode.data.title">
            </div>

            <!-- 开始 -->
            <template v-if="selectedType === 'start'">
              <div class="field">
                <label>输入变量（JSON）</label>
                <textarea :value="jsonText('inputs')" @input="setJson('inputs', $event.target.value)" rows="6" placeholder='[{"name":"question","label":"问题","type":"text","required":true}]'></textarea>
              </div>
            </template>

            <!-- 结束 -->
            <template v-else-if="selectedType === 'end'">
              <div class="field">
                <template v-if="upstreamNodes.some(n => outputFieldsOf(n).length)">
                  <label>可用变量（点击直接添加为输出）</label>
                  <template v-for="n in upstreamNodes" :key="n.id">
                    <div v-if="outputFieldsOf(n).length" class="upstream-node">
                      <div class="up-node-name">{{ n.data?.title || n.type }} · <code>{{ n.id }}</code></div>
                      <div v-if="groupedOutputFieldsOf(n).fixed.length" class="var-group">
                        <div class="var-group-title">固定输出</div>
                        <div class="var-chips">
                          <button v-for="f in groupedOutputFieldsOf(n).fixed" :key="'f-' + f" type="button" class="var-chip var-chip-fixed" @mouseenter="showVarTooltip($event, n, f)" @mousemove="moveVarTooltip" @mouseleave="hideVarTooltip" @click="appendEndRowFromVar(n.id, f)">{{ n.id }}.{{ f }}</button>
                        </div>
                      </div>
                      <div v-if="groupedOutputFieldsOf(n).custom.length" class="var-group">
                        <div class="var-group-title">自定义输出</div>
                        <div class="var-chips">
                          <button v-for="f in groupedOutputFieldsOf(n).custom" :key="'c-' + f" type="button" class="var-chip" @mouseenter="showVarTooltip($event, n, f)" @mousemove="moveVarTooltip" @mouseleave="hideVarTooltip" @click="appendEndRowFromVar(n.id, f)">{{ n.id }}.{{ f }}</button>
                        </div>
                      </div>
                    </div>
                  </template>
                </template>
                <span class="hint" v-else>暂无可用变量：上游节点还未声明输出（开始节点需先配置输入变量）</span>
              </div>
              <div class="field">
                <label>输出映射（最终返回的结果）</label>
                <div class="end-rows">
                  <div v-for="(r, i) in endRows" :key="i" class="end-row">
                    <input type="text" v-model="r.name" placeholder="字段名" class="er-name" @change="flushEndRows">
                    <input type="text" v-model="r.value" placeholder="值或 {{n1.answer}}" class="er-value" @change="flushEndRows">
                    <button type="button" class="btn sm" @click="removeEndRow(i)">×</button>
                  </div>
                  <button type="button" class="btn sm" @click="addEndRow()">＋ 添加一行</button>
                </div>
                <span class="hint">点击上方变量会自动加一行；也可手动编辑「字段名 / 值」，值支持 <code v-pre>{{节点.字段}}</code></span>
              </div>
            </template>

            <!-- 智能体 -->
            <template v-else-if="selectedType === 'agent'">
              <div class="field">
                <label>绑定方式</label>
                <select v-model="selectedConfig.agent_id">
                  <option value="">内置智能体（需选知识库）</option>
                  <option v-for="a in palette.agents" :key="a.id" :value="a.id">{{ a.name }}</option>
                </select>
              </div>
              <template v-if="!selectedConfig.agent_id">
                <div class="field">
                  <label>知识库 <span class="req">*</span></label>
                  <select v-model="selectedConfig.kb_id">
                    <option value="" disabled>请选择知识库</option>
                    <option v-for="kb in palette.kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
                  </select>
                </div>
                <div class="field">
                  <label>技能（可多选）</label>
                  <div class="skill-chips">
                    <button v-for="s in enabledSkills" :key="s.id" type="button" class="skill-chip" :class="{ active: selectedConfig.skill_ids?.includes(s.id) }" @click="toggleSkill(s.id)">{{ s.name }}</button>
                  </div>
                </div>
              </template>
            </template>

            <!-- 实体服务 -->
            <template v-else-if="selectedType === 'service'">
              <div class="field">
                <label>知识库（用于筛选实体）</label>
                <select v-model="selectedConfig.kb_id" @change="onSvcKbChange">
                  <option value="">（不限定）</option>
                  <option v-for="kb in palette.kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
                </select>
              </div>
              <div class="field">
                <label>实体 <span class="req">*</span></label>
                <select v-model="selectedConfig.entity_id" @change="onSvcEntityChange">
                  <option value="">请选择实体</option>
                  <option v-for="e in svcEntities" :key="e.id" :value="e.id">{{ e.name }}（{{ e.entity_type }}）</option>
                </select>
              </div>
              <div class="field">
                <label>服务 <span class="req">*</span></label>
                <select v-model="selectedConfig.service_id">
                  <option value="">请选择服务</option>
                  <option v-for="s in svcServices" :key="s.id" :value="s.id">{{ s.name }}</option>
                </select>
              </div>
              <div class="field">
                <label>参数（JSON，可用变量）</label>
                <textarea :value="jsonText('params')" @input="setJson('params', $event.target.value)" rows="4" placeholder='{"ticker":"HUAWEI"}'></textarea>
              </div>
            </template>

            <!-- 大模型 -->
            <template v-else-if="selectedType === 'llm'">
              <div class="field">
                <label>System Prompt</label>
                <textarea v-model="selectedConfig.system_prompt" rows="3"></textarea>
              </div>
              <div class="field">
                <label>Prompt 模板</label>
                <p class="field-hint">最终发送给大模型的用户消息，支持插入上游节点变量（<code v-pre>{{节点.字段}}</code>）。留空则仅发送 System Prompt。</p>
                <textarea v-model="selectedConfig.prompt_template" rows="4"></textarea>
                <span class="var-btn" @click="insertVar('prompt_template')">⊕ 插入变量</span>
              </div>
            </template>

            <!-- 条件分支 -->
            <template v-else-if="selectedType === 'condition'">
              <div class="field">
                <label>判断方式</label>
                <select v-model="selectedConfig.operator">
                  <option value="==">==</option>
                  <option value="!=">!=</option>
                  <option value="contains">包含</option>
                  <option value="not_contains">不包含</option>
                  <option value="empty">为空</option>
                  <option value="not_empty">非空</option>
                  <option value="gt">&gt;</option>
                  <option value="lt">&lt;</option>
                </select>
              </div>
              <div class="field">
                <label>左值</label>
                <input type="text" v-model="selectedConfig.left" placeholder="{{service_1.success}}">
              </div>
              <div class="field" v-if="!['empty', 'not_empty'].includes(selectedConfig.operator)">
                <label>右值</label>
                <input type="text" v-model="selectedConfig.right" placeholder="true">
              </div>
              <div class="hint">两个出口：<b style="color:var(--c-success)">true</b> / <b style="color:var(--c-danger)">false</b>（拖拽连线时从对应圆点拉出）</div>
            </template>

            <!-- 代码 -->
            <template v-else-if="selectedType === 'code'">
              <div class="field">
                <label>代码（沙箱 Python）</label>
                <p class="field-hint">函数签名为 <code v-pre>def run(params, entity, context) -> dict:</code>。返回 dict 即本节点的 <code>data</code> 输出。左侧变量可拖拽到编辑器，自动插入 <code v-pre>var("节点id", "字段")</code>，无需手写 context。</p>
                <PythonEditor v-model="selectedConfig.code_text" :height="280" :max-length="50000" />
              </div>
            </template>

            <!-- ═══ 分区二：变量配置（输入引用 / 参数插值 / 输出声明） ═══ -->
            <template v-if="selectedType !== 'start' && selectedType !== 'end'">
              <div class="section-title">变量配置</div>

              <!-- 输入变量：沿连线可流入本节点的上游输出（智能体用「⊕ 插入变量」，不显示此块） -->
              <div class="field" v-if="selectedType !== 'agent'">
                <template v-if="upstreamNodes.some(n => outputFieldsOf(n).length)">
                  <label>输入变量（上游，点击复制 / 拖拽到代码中）</label>
                  <template v-for="n in upstreamNodes" :key="n.id">
                    <div v-if="outputFieldsOf(n).length" class="upstream-node">
                      <div class="up-node-name">{{ n.data?.title || n.type }} · <code>{{ n.id }}</code></div>
                      <div v-if="groupedOutputFieldsOf(n).fixed.length" class="var-group">
                        <div class="var-group-title">固定输出</div>
                        <div class="var-chips">
                          <button v-for="f in groupedOutputFieldsOf(n).fixed" :key="'f-' + f" type="button" class="var-chip var-chip-fixed" draggable="true" @dragstart="ev => onVarDragStart(ev, n.id, f)" @mouseenter="showVarTooltip($event, n, f)" @mousemove="moveVarTooltip" @mouseleave="hideVarTooltip" @click="copyText(varRef(n.id, f))">{{ n.id }}.{{ f }}</button>
                        </div>
                      </div>
                      <div v-if="groupedOutputFieldsOf(n).custom.length" class="var-group">
                        <div class="var-group-title">自定义输出</div>
                        <div class="var-chips">
                          <button v-for="f in groupedOutputFieldsOf(n).custom" :key="'c-' + f" type="button" class="var-chip" draggable="true" @dragstart="ev => onVarDragStart(ev, n.id, f)" @mouseenter="showVarTooltip($event, n, f)" @mousemove="moveVarTooltip" @mouseleave="hideVarTooltip" @click="copyText(varRef(n.id, f))">{{ n.id }}.{{ f }}</button>
                        </div>
                      </div>
                    </div>
                  </template>
                </template>
                <span class="hint" v-else>暂无可用变量：上游节点还未声明输出（开始节点需先配置输入变量）</span>
              </div>

              <!-- 各类型的变量插值输入 -->
              <div class="field" v-if="selectedType === 'agent'">
                <label>问题模板</label>
                <textarea v-model="selectedConfig.query_template" rows="3"></textarea>
                <span class="var-btn" @click="insertVar('query_template')">⊕ 插入变量</span>
              </div>
              <div class="field" v-if="selectedType === 'service'">
                <label>参数（JSON，可用变量）</label>
                <textarea :value="jsonText('params')" @input="setJson('params', $event.target.value)" rows="4" placeholder='{"ticker":"HUAWEI"}'></textarea>
              </div>
              <div class="field" v-if="selectedType === 'code'">
                <label>参数（JSON，可用变量）</label>
                <p class="field-hint">把需要用到的上游变量写到这里，代码里通过 <code v-pre>params["键名"]</code> 读取；也可以不填，直接在代码里用 <code v-pre>context["节点id"]["字段"]</code> 读取上游输出。</p>
                <textarea :value="jsonText('params')" @input="setJson('params', $event.target.value)" rows="3" placeholder='{"summary":"{{llm1.text}}","cc":"{{llm1.cc}}"}'></textarea>
              </div>

              <!-- 输出变量：固定字段（只读，点击复制；配置了结构化输出的节点在结构化输出表中展示，不重复显示） -->
              <div class="field" v-if="selectedType !== 'agent' && selectedType !== 'llm' && selectedType !== 'code'">
                <label>输出变量（🔒 固定，点击复制）</label>
                <div class="var-chips" v-if="displayOutputFields(selectedNode).length">
                  <span
                    v-for="f in displayOutputFields(selectedNode)" :key="f"
                    class="var-chip var-chip-edit var-chip-fixed"
                    @mouseenter="showVarTooltip($event, selectedNode, f)"
                    @mousemove="moveVarTooltip"
                    @mouseleave="hideVarTooltip"
                  >
                    <span class="vc-name" @click="copyText(varRef(selectedNodeId, f))">{{ f }}</span>
                  </span>
                </div>
              </div>

              <!-- 结构化输出：固定字段锁定行 + 自定义字段行 -->
              <div class="field" v-if="selectedType === 'agent' || selectedType === 'llm' || selectedType === 'code'">
                <label>{{ selectedType === 'code' ? '输出变量（🔒 固定始终输出；自定义字段对应 return 字典里的键，下游引用 ' + varRefPlaceholder + '）' : '结构化输出（🔒 固定始终输出；自定义字段由大模型根据「说明」生成，下游引用 ' + varRefPlaceholder + '）' }}</label>
                <div class="struct-table">
                  <div class="st-head">
                    <span class="st-col st-col-name">字段名</span>
                    <span class="st-col st-col-type">类型</span>
                    <span class="st-col st-col-desc">说明</span>
                    <span class="st-col st-col-op"></span>
                  </div>
                  <!-- 固定字段：锁定行（不可编辑/删除） -->
                  <div v-for="f in (selectedType === 'agent' ? FIXED_STRUCT_FIELDS : selectedType === 'code' ? FIXED_STRUCT_FIELDS_CODE : FIXED_STRUCT_FIELDS_LLM)" :key="f.name" class="end-row struct-row struct-row-fixed">
                    <span class="st-col st-col-name st-lock">🔒 {{ f.name }}</span>
                    <span class="st-col st-col-type st-lock">{{ f.type }}</span>
                    <span class="st-col st-col-desc st-lock">{{ f.desc }}</span>
                    <span class="st-col st-col-op"></span>
                  </div>
                  <!-- 自定义字段行 -->
                  <div v-for="(row, ri) in structRows" :key="ri" class="end-row struct-row">
                    <input type="text" v-model="row.name" placeholder="如 count" class="st-col st-col-name" @change="flushStructRows">
                    <select v-model="row.type" class="st-col st-col-type" @change="flushStructRows">
                      <option value="string">string</option>
                      <option value="number">number</option>
                      <option value="boolean">boolean</option>
                      <option value="array">array</option>
                    </select>
                    <input type="text" v-model="row.description" :placeholder="selectedType === 'code' ? '如 电池容量' : '如 奥雷里亚诺的个数'" class="st-col st-col-desc" @change="flushStructRows">
                    <button type="button" class="btn sm st-col st-col-op" @click="structRows.splice(ri, 1); flushStructRows()">×</button>
                  </div>
                  <button type="button" class="btn sm" @click="structRows.push({ name: '', type: 'string', description: '' }); flushStructRows()">＋ 添加字段</button>
                </div>
              </div>
            </template>
          </div>
        </template>

        <div class="dr-empty" v-if="!drawerCollapsed && !selectedNode">点击画布上的节点<br>编辑它的配置</div>
      </aside>
    </div>

    <!-- 底部运行日志控制台 -->
    <div class="wf-console" :class="{ collapsed: consoleCollapsed }" :style="{ height: consoleCollapsed ? '32px' : consoleHeight + 'px' }">
      <div class="console-resizer" v-if="!consoleCollapsed" title="拖拽调节高度" @pointerdown="startConsoleResize"></div>
      <div class="console-head">
        <template v-if="!consoleCollapsed">
          <div class="console-tabs">
            <button class="ctab" :class="{ active: logTab === 'current' }" @click="logTab = 'current'">当前运行</button>
            <button class="ctab" :class="{ active: logTab === 'history' }" @click="logTab = 'history'">
              历史 <span class="ctab-badge">{{ runHistory.length }}/{{ KEEP_RUNS }}</span>
            </button>
          </div>
          <span class="console-spacer"></span>
          <button class="btn sm" v-if="logTab === 'current'" @click="clearLogs">清空</button>
          <button class="btn sm" v-else @click="refreshHistory" :disabled="loadingHistory">刷新</button>
        </template>
        <span class="console-spacer" v-else></span>
        <button type="button" class="console-toggle" :title="consoleCollapsed ? '展开' : '收起'" @click="consoleCollapsed = !consoleCollapsed">
          <svg v-if="consoleCollapsed" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="5 15 12 8 19 15" />
            <polyline points="5 9 12 2 19 9" />
          </svg>
          <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="5 9 12 16 19 9" />
            <polyline points="5 15 12 22 19 15" />
          </svg>
        </button>
      </div>

      <!-- 当前运行：SSE 流式日志 -->
      <div class="console-body" v-show="logTab === 'current'">
        <div v-for="(l, i) in logs" :key="i">
          <div v-if="l.kind === 'meta'" class="log-line log-meta">{{ l.text }}</div>
          <!-- 节点日志卡片：头部（状态+节点名+耗时）可点开，展开后分输入/输出 -->
          <div v-else class="log-card" :class="'stc-' + l.status">
            <div class="log-card-head" @click="toggleLog(i)">
              <span
                class="log-ico"
                :style="{ background: TYPE_META[nodeTypeOf(l)]?.color || 'var(--c-secondary)' }"
                v-html="TYPE_META[nodeTypeOf(l)]?.icon || '●'"
              ></span>
              <span class="log-title">{{ l.title || l.node_id }}</span>
              <span class="log-nodeid">{{ l.node_id }}</span>
              <span class="log-summary" v-if="l.error" :title="l.error">{{ l.error }}</span>
              <span class="log-summary" v-else-if="l.summary">{{ l.summary }}</span>
              <span class="log-dur" v-if="l.status === 'running' && runningSince[l.node_id]">{{ fmtElapsed(nowTick - runningSince[l.node_id]) }}</span>
              <span class="log-dur" v-else-if="l.duration_ms != null">{{ l.duration_ms }}ms</span>
              <span class="log-expand">{{ expandedLog === i ? '▾ 收起' : '▸ 详情' }}</span>
            </div>
            <div class="log-card-detail" v-if="expandedLog === i">
              <div class="log-pane">
                <div class="log-pane-title">输入</div>
                <pre class="log-pre">{{ l.input ? JSON.stringify(l.input, null, 2) : '（无）' }}</pre>
              </div>
              <div class="log-pane">
                <div class="log-pane-title">输出</div>
                <pre class="log-pre" :class="{ 'log-pre-err': l.error }">{{ l.output != null ? JSON.stringify(l.output, null, 2) : (l.error || '（无）') }}</pre>
              </div>
            </div>
          </div>
        </div>
        <div class="console-empty" v-if="!logs.length">暂无运行日志，点击「▶ 运行」开始</div>
      </div>

      <!-- 历史运行：最近 N 次记录列表 -->
      <div class="console-body" v-show="logTab === 'history'">
        <div class="history-hint" v-if="runHistory.length >= KEEP_RUNS">已达上限（{{ KEEP_RUNS }} 条），更早的记录会自动清理</div>
        <div v-for="r in runHistory" :key="r.id" class="run-row" :class="'stc-' + r.status">
          <div class="run-row-head">
            <span class="run-status">{{ statusIcon(r.status) }}</span>
            <span class="run-badge" :class="'b-' + r.status">{{ fmtStatusText(r.status) }}</span>
            <span class="run-type" :class="r.trigger_source === 'schedule' ? 't-schedule' : 't-manual'">
              {{ r.trigger_source === 'schedule' ? '定时' : '手动' }}
            </span>
            <span class="run-time">{{ fmtTime(r.started_at) }}</span>
            <span class="run-dur" v-if="r.duration_ms != null">{{ fmtMs(r.duration_ms) }}</span>
            <span class="run-nodes" v-if="r.node_count">· {{ r.node_count }} 节点
              <b class="ok" v-if="r.node_summary.succeeded">✓{{ r.node_summary.succeeded }}</b>
              <b class="bad" v-if="r.node_summary.failed">✗{{ r.node_summary.failed }}</b>
              <b class="skp" v-if="r.node_summary.skipped">⊘{{ r.node_summary.skipped }}</b>
            </span>
            <span class="run-actions">
              <button class="btn xs" @click="openRunDetail(r.id)">详情</button>
              <button class="btn xs" @click="replayRun(r.id)">回放</button>
              <button class="btn xs danger" :disabled="deletingRunId === r.id" @click="deleteRun(r.id)">删除</button>
            </span>
          </div>
          <div class="run-row-preview" v-if="r.error">
            <span class="run-err" :title="r.error">{{ r.error }}</span>
          </div>
        </div>
        <div class="console-empty" v-if="!loadingHistory && !runHistory.length">暂无历史运行，点击「▶ 运行」开始</div>
        <div class="console-empty" v-if="loadingHistory">加载中...</div>
      </div>
    </div>

    <!-- 历史运行详情抽屉 -->
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

    <!-- 右键菜单 -->
    <div v-if="contextMenu.visible" class="ctx-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }" @click.stop>
      <button class="ctx-item danger" @click="contextMenu.type === 'node' ? deleteNodeById(contextMenu.id) : deleteEdgeById(contextMenu.id)">
        删除{{ contextMenu.type === 'node' ? '节点' : '连线' }}
      </button>
    </div>

    <!-- 运行入参弹窗 -->
    <div class="modal-mask" v-if="runModal">
      <div class="modal">
        <h3>运行工作流</h3>
        <div class="m-sub">填写「开始」节点的输入变量</div>
        <div class="field" v-for="it in startInputs" :key="it.name">
          <label>{{ (it && (it.label || it.name)) || '未命名输入' }} <span v-if="it && it.required" class="req">*</span></label>
          <input type="text" v-model="runInputs[it.name]" :placeholder="it && it.name">
        </div>
        <div class="m-sub" v-if="!startInputs.length">该工作流没有声明输入变量，直接运行。</div>
        <div class="m-actions">
          <button class="btn" @click="runModal = false">取消</button>
          <button class="btn primary" @click="startRun">开始运行</button>
        </div>
      </div>
    </div>

    <!-- 变量说明悬浮提示（即时显示，替代原生 title；Teleport 到 body 避免被节点遮挡） -->
    <Teleport to="body">
      <div
        v-show="tooltip.visible"
        class="var-tooltip"
        :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px' }"
      >
        <div class="var-tooltip-title">{{ tooltip.title }}</div>
        <div class="var-tooltip-desc">{{ tooltip.desc }}</div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
/* 变量说明悬浮提示：即时显示、主题化、带阴影 */
.var-tooltip {
  position: fixed; left: 0; top: 0; z-index: 1000;
  max-width: 280px; min-width: 80px;
  padding: 8px 12px;
  background: var(--c-panel-elevated, var(--c-panel));
  border: 1px solid var(--c-border-strong, var(--c-border));
  border-radius: 10px;
  box-shadow: 0 10px 30px rgba(0,0,0,.28), 0 2px 6px rgba(0,0,0,.12);
  pointer-events: none;
  font-size: 12px; line-height: 1.5;
  color: var(--c-fg);
}
.var-tooltip-title {
  font-weight: 700; color: var(--c-accent);
  font-family: ui-monospace, monospace;
  margin-bottom: 3px; font-size: 12px;
}
.var-tooltip-desc { color: var(--c-secondary); font-size: 11.5px; }

.wf-editor { display: flex; flex-direction: column; gap: 12px; height: calc(100vh - 170px); min-height: 480px; }
.wf-toolbar { display: flex; align-items: center; gap: 10px; }
.wf-name { flex: 0 0 auto; width: 240px; padding: 7px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px); font-size: 14px; font-weight: 600; font-family: var(--font); outline: none; background: var(--c-panel); color: var(--c-fg); }
.wf-name:focus { border-color: var(--c-accent); }
.wf-saved { font-size: 12px; color: var(--c-success); font-weight: 600; }
.spacer { flex: 1; }

.wf-body { display: flex; gap: 12px; flex: 1; min-height: 0; }

/* 左面板 */
.wf-palette { width: 180px; flex-shrink: 0; display: flex; flex-direction: column; gap: 6px; padding: 12px 10px; border: 1px solid var(--c-border); border-radius: 12px; background: var(--c-panel); overflow-y: auto; }
.pal-group { font-size: 11px; font-weight: 700; color: var(--c-secondary); letter-spacing: 1px; }
.pal-item { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-bg); cursor: pointer; font-family: var(--font); color: var(--c-fg); transition: border-color 120ms, transform 120ms; }
.pal-item:hover { border-color: var(--c-accent); transform: translateY(-1px); }
.pal-ico { width: 22px; height: 22px; border-radius: 6px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 12px; flex-shrink: 0; }
.pal-ico svg { width: 14px; height: 14px; }
.pal-name { font-size: 12.5px; font-weight: 600; }
.pal-hint { margin-top: auto; padding: 8px; font-size: 11px; color: var(--c-secondary); border: 1px dashed var(--c-border); border-radius: 8px; line-height: 1.6; }
.pal-hint code { font-family: ui-monospace, monospace; color: var(--c-accent); }

/* 画布 */
.wf-canvas-wrap { flex: 1; position: relative; border: 1px solid var(--c-border); border-radius: 12px; overflow: hidden; background: var(--c-bg); }
.wf-console { position: relative; flex-shrink: 0; height: 220px; display: flex; flex-direction: column; border: 1px solid var(--c-border); border-radius: 10px; background: var(--c-panel); overflow: hidden; }
/* 顶边缘拖拽把手：hover/拖动时高亮横线 */
.console-resizer {
  position: absolute; top: -4px; left: 0; right: 0; height: 9px;
  cursor: row-resize; z-index: 6;
}
.console-resizer::after {
  content: ''; position: absolute; top: 4px; left: 0; right: 0; height: 1px;
  background: transparent; transition: background 150ms, height 150ms;
}
.console-resizer:hover::after { background: var(--c-accent); height: 2px; }
.console-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--c-border); flex-shrink: 0; background: var(--c-muted); }
.wf-console.collapsed { height: 32px; }
.wf-console.collapsed .console-head { height: 100%; padding: 0 12px; border-bottom: none; }
.console-toggle {
  width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;
  background: transparent; border: 1px solid var(--c-border); border-radius: 6px;
  color: var(--c-secondary); cursor: pointer; transition: color .15s, border-color .15s;
}
.console-toggle:hover { color: var(--c-accent); border-color: var(--c-accent); }
.console-toggle svg { width: 20px; height: 20px; }
.console-title { font-size: 12px; font-weight: 700; }
.console-spacer { flex: 1; }
.console-body { flex: 1; overflow-y: auto; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; }

/* 节点日志卡片 */
.log-card { border: 1px solid var(--c-border); border-radius: 8px; overflow: hidden; background: var(--c-panel); flex-shrink: 0; }
.log-card.stc-running { border-color: color-mix(in srgb, var(--c-accent) 55%, transparent); }
.log-card.stc-succeeded { border-left: 3px solid var(--c-success); }
.log-card.stc-failed { border-left: 3px solid var(--c-danger); }
.log-card.stc-skipped { opacity: .55; }
.log-card-head {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px;
  cursor: pointer; font-family: ui-monospace, monospace; font-size: 12px;
  background: var(--c-muted);
}
.log-card-head:hover { background: var(--c-muted-hover, var(--c-muted)); }
.log-nodeid { font-size: 10px; color: var(--c-secondary); opacity: .7; }
.log-expand { margin-left: auto; flex-shrink: 0; font-size: 10.5px; color: var(--c-secondary); }
.log-card-detail { display: flex; gap: 0; border-top: 1px solid var(--c-border); }
.log-pane { flex: 1; min-width: 0; padding: 8px 10px; }
.log-pane + .log-pane { border-left: 1px solid var(--c-border); }
.log-pane-title { font-size: 10.5px; font-weight: 700; color: var(--c-secondary); margin-bottom: 4px; font-family: var(--font); letter-spacing: .5px; }
.log-pre {
  margin: 0; font-family: ui-monospace, monospace; font-size: 11px; line-height: 1.55;
  white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow-y: auto;
  color: var(--c-fg);
}
.log-pre-err { color: var(--c-danger); }
.console-empty { padding: 24px; text-align: center; color: var(--c-secondary); }
.log-line { padding: 4px 12px; color: var(--c-secondary); }
.log-meta { font-weight: 600; color: var(--c-fg); border-bottom: 1px solid var(--c-border); }
.log-node { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.log-node:hover { background: var(--c-muted); }
.log-status { width: 14px; flex-shrink: 0; text-align: center; }
.log-node.st-running .log-status { color: var(--c-accent); animation: wf-blink 1s ease-in-out infinite; }
@keyframes wf-blink { 50% { opacity: .3; } }
.log-node.st-succeeded .log-status { color: var(--c-success); }
.log-node.st-failed .log-status { color: var(--c-danger); }
.log-node.st-skipped { opacity: .5; }
.log-ico {
  width: 18px; height: 18px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 5px; color: #fff; font-size: 10px;
}
.log-ico svg { width: 12px; height: 12px; }
.log-card.stc-running .log-ico { animation: wf-blink 1s ease-in-out infinite; }
.log-card.stc-failed .log-ico { filter: grayscale(0.35); }
.log-card.stc-skipped .log-ico { opacity: .5; }
.log-title { flex-shrink: 0; font-weight: 600; color: var(--c-fg); }
.log-summary { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-secondary); }
.log-node.st-failed .log-summary { color: var(--c-danger); }
.log-dur { flex-shrink: 0; color: var(--c-secondary); opacity: .8; }
.log-expand { flex-shrink: 0; color: var(--c-secondary); }
.log-output { padding: 0 12px 8px 26px; }
.log-output pre { margin: 0; padding: 8px; border-radius: 6px; background: var(--c-muted); font-size: 11px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; max-height: 180px; overflow-y: auto; }

/* 控制台 Tab 切换 */
.console-tabs { display: flex; gap: 4px; }
.ctab {
  border: 1px solid transparent; background: transparent; color: var(--c-secondary);
  font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 6px; cursor: pointer;
  font-family: var(--font); display: inline-flex; align-items: center; gap: 6px;
}
.ctab:hover { background: color-mix(in srgb, var(--c-fg) 8%, transparent); }
.ctab.active { color: var(--c-fg); background: var(--c-panel); border-color: var(--c-border); }
.ctab-badge {
  font-size: 10px; padding: 1px 6px; border-radius: 999px; background: var(--c-muted);
  color: var(--c-secondary); font-weight: 700;
}
.ctab.active .ctab-badge { background: color-mix(in srgb, var(--c-accent) 22%, transparent); color: var(--c-fg); }

/* 历史运行列表 */
.history-hint { font-size: 11px; color: var(--c-secondary); padding: 6px 8px; background: color-mix(in srgb, var(--c-accent) 10%, transparent); border-radius: 6px; }
.run-row { border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-panel); flex-shrink: 0; overflow: hidden; }
.run-row.stc-failed { border-left: 3px solid var(--c-danger); }
.run-row.stc-succeeded { border-left: 3px solid var(--c-success); }
.run-row.stc-running,
.run-row.stc-cancelled,
.run-row.stc-skipped { border-left: 3px solid var(--c-border); }
.run-row-head { display: flex; align-items: center; gap: 8px; padding: 7px 10px; flex-wrap: wrap; }
.run-status { width: 14px; text-align: center; }
.run-badge { font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 999px; }
.run-type { font-size: 10.5px; font-weight: 600; padding: 1px 7px; border-radius: 999px; }
.run-type.t-schedule { background: color-mix(in srgb, var(--c-warn, #d98b00) 18%, transparent); color: var(--c-warn, #d98b00); }
.run-type.t-manual { background: var(--c-muted); color: var(--c-secondary); }
.run-badge.b-succeeded { background: color-mix(in srgb, var(--c-success) 18%, transparent); color: var(--c-success); }
.run-badge.b-failed { background: color-mix(in srgb, var(--c-danger) 18%, transparent); color: var(--c-danger); }
.run-badge.b-running { background: color-mix(in srgb, var(--c-accent) 18%, transparent); color: var(--c-accent); }
.run-badge.b-cancelled, .run-badge.b-skipped { background: var(--c-muted); color: var(--c-secondary); }
.run-time { font-size: 11.5px; color: var(--c-fg); }
.run-dur { font-size: 11.5px; color: var(--c-secondary); opacity: .85; }
.run-nodes { font-size: 11.5px; color: var(--c-secondary); }
.run-nodes .ok { color: var(--c-success); }
.run-nodes .bad { color: var(--c-danger); }
.run-nodes .skp { color: var(--c-secondary); }
.run-actions { margin-left: auto; display: flex; gap: 4px; }
.btn.xs { padding: 3px 9px; font-size: 11px; }
.btn.xs.danger { color: var(--c-danger); }
.btn.xs.danger:hover { background: color-mix(in srgb, var(--c-danger) 14%, transparent); }
.run-row-preview { padding: 0 10px 8px 32px; }
.run-err { font-size: 11.5px; color: var(--c-danger); display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 历史运行详情抽屉 */
.run-drawer-mask { position: fixed; inset: 0; z-index: 300; background: rgba(0,0,0,.35); display: flex; justify-content: flex-end; }
.run-drawer {
  width: 560px; max-width: 92vw; height: 100%; display: flex; flex-direction: column;
  background: var(--c-panel); border-left: 1px solid var(--c-border); box-shadow: -12px 0 32px rgba(0,0,0,.18);
}
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

/* 历史详情里的节点卡片：默认展开 */
.log-card-detail-open { display: flex; gap: 0; border-top: 1px solid var(--c-border); background: var(--c-muted); }

/* 右键菜单 */
.ctx-menu { position: fixed; z-index: 200; min-width: 120px; padding: 4px; border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-panel-elevated, var(--c-panel)); box-shadow: 0 8px 24px rgba(0,0,0,.18); }
.ctx-item { display: block; width: 100%; text-align: left; padding: 7px 12px; border: 0; background: transparent; color: var(--c-fg); font-size: 12.5px; font-family: var(--font); cursor: pointer; border-radius: 6px; }
.ctx-item:hover { background: var(--c-muted); }
.ctx-item.danger { color: var(--c-danger); }
.ctx-item.danger:hover { background: color-mix(in srgb, var(--c-danger) 12%, transparent); }

/* 右抽屉 */
.wf-drawer { position: relative; width: 340px; flex-shrink: 0; display: flex; flex-direction: column; border: 1px solid var(--c-border); border-radius: 12px; background: var(--c-panel); overflow: hidden; transition: width .2s ease; }
.wf-drawer.collapsed { width: 28px; align-items: center; padding-top: 8px; border-radius: 12px 0 0 12px; }
.drawer-toggle {
  width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;
  background: transparent; border: 1px solid var(--c-border); border-radius: 6px;
  color: var(--c-secondary); cursor: pointer; transition: color .15s, border-color .15s; z-index: 10;
}
.drawer-toggle:hover { color: var(--c-accent); border-color: var(--c-accent); }
.drawer-toggle svg { width: 20px; height: 20px; }
/* 左边缘拖拽把手：hover/拖动时高亮 */
.drawer-resizer {
  position: absolute; left: -3px; top: 0; bottom: 0; width: 7px;
  cursor: col-resize; z-index: 5;
}
.drawer-resizer::after {
  content: ''; position: absolute; left: 3px; top: 0; bottom: 0; width: 1px;
  background: transparent; transition: background 150ms, width 150ms;
}
.drawer-resizer:hover::after { background: var(--c-accent); width: 2px; }
.dr-head { display: flex; align-items: center; gap: 9px; padding: 12px 14px; border-bottom: 1px solid var(--c-border); }
.dr-ico { width: 24px; height: 24px; border-radius: 6px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 13px; flex-shrink: 0; }
.dr-ico svg { width: 15px; height: 15px; }
.dr-head-t { flex: 1; min-width: 0; }
.dr-title { font-size: 13px; font-weight: 700; color: var(--c-fg); }
.dr-sub { font-size: 10.5px; color: var(--c-secondary); }
.dr-body { flex: 1; overflow-y: auto; padding: 12px 14px 16px; display: flex; flex-direction: column; gap: 10px; }

/* 分区标题：智能体配置 / 变量配置 */
.section-title {
  font-size: 11px; font-weight: 700; color: var(--c-accent); letter-spacing: 1px;
  display: flex; align-items: center; gap: 8px;
  margin-top: 6px;
}
.section-title::after { content: ''; flex: 1; height: 1px; background: color-mix(in srgb, var(--c-accent) 30%, transparent); }

/* 配置分区卡片：每个 field 变成独立小节 */
.field {
  display: flex; flex-direction: column; gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--c-border);
  border-radius: 10px;
  background: var(--c-bg);
}
.dr-empty { flex: 1; display: flex; align-items: center; justify-content: center; text-align: center; color: var(--c-secondary); font-size: 13px; line-height: 1.6; }

.field label {
  font-size: 11px; font-weight: 700; color: var(--c-secondary);
  letter-spacing: .3px;
  display: flex; align-items: center; gap: 4px;
}
.field input, .field textarea, .field select { padding: 7px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px); font-size: 12.5px; font-family: var(--font); outline: none; background: var(--c-panel); color: var(--c-fg); }
.field input:focus, .field textarea:focus, .field select:focus { border-color: var(--c-accent); }
.field textarea { resize: vertical; min-height: 56px; line-height: 1.5; }
.req { color: var(--c-danger); }
.hint { font-size: 10.5px; color: var(--c-secondary); line-height: 1.5; opacity: .85; }
.field-hint { font-size: 11px; color: var(--c-secondary); line-height: 1.5; margin: -6px 0 6px; }
.var-btn { display: inline-flex; align-items: center; gap: 3px; margin-top: 5px; font-size: 11px; font-weight: 600; color: var(--c-accent); cursor: pointer; padding: 2px 8px; border: 1px dashed var(--c-accent); border-radius: 4px; width: fit-content; }
.var-btn:hover { background: var(--c-accent-weak, rgba(161,98,7,.10)); }

.skill-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.skill-chip { padding: 4px 10px; border-radius: 20px; font-size: 12px; border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; }
.skill-chip.active { background: var(--c-muted); border-color: var(--c-accent); color: var(--c-accent); }

/* 输出变量提示 */
.var-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.var-chip { padding: 3px 9px; border-radius: 6px; font-size: 11px; font-family: ui-monospace, monospace; border: 1px solid var(--c-border); background: var(--c-muted); color: var(--c-accent); cursor: pointer; }
.var-chip:hover { border-color: var(--c-accent); }
.var-chip-edit { display: inline-flex; align-items: center; gap: 4px; }
.var-chip-fixed { border-style: dashed; cursor: default; opacity: .92; }
.vc-name { cursor: pointer; }
.vc-del { cursor: pointer; color: var(--c-secondary); font-weight: 700; padding: 0 2px; }
.vc-del:hover { color: var(--c-danger); }
.upstream-node { margin-bottom: 4px; padding: 6px 8px; border: 1px dashed var(--c-border); border-radius: 8px; background: var(--c-panel); }
.up-node-name { font-size: 11px; color: var(--c-secondary); margin-bottom: 3px; }
.up-node-name code { color: var(--c-fg); }
.var-group { margin-top: 4px; }
.var-group + .var-group { margin-top: 6px; }
.var-group-title { font-size: 10px; color: var(--c-secondary); margin-bottom: 2px; }

/* 结束节点 key-value 行 */
.end-rows { display: flex; flex-direction: column; gap: 6px; }
.end-row { display: flex; align-items: center; gap: 6px; }

/* 结构化输出表：表头 + 弹性列宽（说明列吃剩余空间） */
.struct-table { display: flex; flex-direction: column; gap: 6px; }
.struct-table .end-row { gap: 4px; }
.st-head {
  display: flex; align-items: center; gap: 4px;
  font-size: 10.5px; font-weight: 700; color: var(--c-secondary);
  padding: 0 2px;
}
.st-col { flex-shrink: 0; }
.st-col-name { width: 84px; }
.st-col-type { width: 74px; }
.st-col-desc { flex: 1; min-width: 0; }  /* 说明列弹性伸缩 */
.st-col-op { width: 26px; text-align: center; }
.struct-row-fixed { opacity: .8; }
.st-lock {
  display: flex; align-items: center; font-size: 11.5px; color: var(--c-secondary);
  background: var(--c-muted); border-radius: var(--radius-sm, 6px);
  padding: 0 6px; height: 30px;
  font-family: ui-monospace, monospace;
}
.st-col input, .st-col select {
  width: 100%; padding: 6px 6px; border: 1px solid var(--c-border);
  border-radius: var(--radius-sm, 6px); font-size: 12px;
  background: var(--c-panel); color: var(--c-fg); outline: none;
}
.st-col select { padding: 6px 2px; }
.st-col input:focus, .st-col select:focus { border-color: var(--c-accent); }
.er-name { width: 90px; flex-shrink: 0; padding: 6px 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px); font-size: 12px; font-family: ui-monospace, monospace; background: var(--c-bg); color: var(--c-fg); outline: none; }
.struct-row .er-type { width: 84px; flex-shrink: 0; padding: 6px 4px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px); font-size: 12px; background: var(--c-bg); color: var(--c-fg); outline: none; }
.er-value { flex: 1; min-width: 0; padding: 6px 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px); font-size: 12px; font-family: ui-monospace, monospace; background: var(--c-bg); color: var(--c-fg); outline: none; }
.er-name:focus, .er-value:focus { border-color: var(--c-accent); }

/* 弹窗 */
.modal-mask { position: fixed; inset: 0; background: var(--c-overlay, rgba(0,0,0,.35)); z-index: 100; display: flex; align-items: center; justify-content: center; }
.modal { width: 380px; background: var(--c-panel); border-radius: 12px; padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; box-shadow: 0 12px 40px rgba(0,0,0,.25); }
.modal h3 { font-size: 14px; font-weight: 700; }
.m-sub { font-size: 12px; color: var(--c-secondary); }
.m-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
