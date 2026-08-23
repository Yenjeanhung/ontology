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
  fetchEntities, fetchEntityServices,
} from '../../api'
import { useToast } from '../../composables/useToast'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const wfId = route.params.workflowId

const TYPE_META = {
  start: { name: '开始', icon: '▶', color: '#64748b' },
  end: { name: '结束', icon: '■', color: '#64748b' },
  agent: { name: '智能体', icon: '🤖', color: '#d97706' },
  service: { name: '实体服务', icon: '⚙️', color: '#2563eb' },
  llm: { name: '大模型', icon: '✨', color: '#7c3aed' },
  condition: { name: '条件分支', icon: '⇄', color: '#ea580c' },
  code: { name: '代码', icon: '</>', color: '#059669' },
}
// 自定义节点组件映射（Vue Flow 按节点 type 渲染 WorkflowNode）
// markRaw：组件对象不进响应式代理（Vue Flow 内部会 h() 渲染，代理化组件触发性能 warning）
const nodeTypes = markRaw(Object.fromEntries(Object.keys(TYPE_META).map(t => [t, WorkflowNode])))
const { screenToFlowCoordinate } = useVueFlow()

const DEFAULT_CONFIG = {
  start: { inputs: [] },
  end: { outputs: [] },
  agent: { agent_id: '', kb_id: '', skill_ids: [], query_template: '{{start.input}}' },
  service: { kb_id: '', entity_id: '', service_id: '', params: {} },
  llm: { system_prompt: '', prompt_template: '' },
  condition: { operator: '==', left: '', right: '' },
  code: { code_text: 'def run(params, entity, context):\n    return {"data": params}', params: {} },
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

// 结构化输出表顶部展示的固定字段（锁定行，仅智能体节点用）
const FIXED_STRUCT_FIELDS = [
  { name: 'answer', type: 'string', desc: '完整回答文本（固定）' },
  { name: 'chunks', type: 'array', desc: '引用来源分片（固定）' },
  { name: 'entities', type: 'array', desc: '识别实体（固定）' },
]
// 节点声明输出变量：优先 config.output_fields（用户手动管理），旧数据回退到类型默认
function outputFieldsOf(nodeLike) {
  const t = nodeLike.type
  const arr = nodeLike.data?.config?.output_fields ?? nodeLike.config?.output_fields
  return Array.isArray(arr) && arr.length ? arr : (OUTPUT_FIELDS_DEFAULT[t] || [])
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
  } catch (err) {
    toast.error(`加载失败: ${err.message}`)
  }
})

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
  nodes.value = nodes.value.filter(n => n.id !== id)
  edges.value = edges.value.filter(e => e.source !== id && e.target !== id)
  if (selectedNodeId.value === id) selectedNodeId.value = null
  closeContextMenu()
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

// ───── 保存 ─────
async function save() {
  if (!wfName.value.trim()) { toast.error('名称不能为空'); return }
  if (!validateStructRows()) return
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
const consoleOpen = ref(false)
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
  return s?.data?.config?.inputs || []
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
    if (status === 'running') n.data.elapsedText = '0s'
    // succeeded/failed：定格最终耗时（durationMs 优先，缺省用本地计时）
    else if (status === 'succeeded' || status === 'failed') {
      n.data.elapsedText = fmtElapsed(durationMs ?? (runningSince[nodeId] ? Date.now() - runningSince[nodeId] : null))
    }
    else n.data.elapsedText = ''
  }
  if (status === 'running') runningSince[nodeId] = Date.now()
  else delete runningSince[nodeId]
}
function clearStatus() {
  nodes.value.forEach(n => { n.data.status = '' })
  Object.keys(runningSince).forEach(k => delete runningSince[k])
  logs.value = []
}

async function startRun() {
  runModal.value = false
  clearStatus()
  running.value = true
  expandedLog.value = -1
  consoleOpen.value = true
  try {
    await runWorkflowStream(wfId, { ...runInputs }, {
      onStarted(d) { logs.value.push({ kind: 'meta', text: `工作流开始 · run ${d.run_id}` }) },
      onNodeStarted(d) {
        setStatus(d.node_id, 'running')
        logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'running' })
      },
      onNodeProgress(d) {
        // 心跳：更新对应 running 日志行的已运行时长
        const line = [...logs.value].reverse().find(l => l.kind === 'node' && l.node_id === d.node_id && l.status === 'running')
        if (line) line.elapsed_ms = d.elapsed_ms
      },
      onNodeFinished(d) {
        setStatus(d.node_id, 'succeeded', d.duration_ms)
        logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'succeeded', summary: d.summary, output: d.output, duration_ms: d.duration_ms })
      },
      onNodeFailed(d) {
        setStatus(d.node_id, 'failed', d.duration_ms)
        logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'failed', error: d.error, duration_ms: d.duration_ms })
      },
      onNodeSkipped(d) {
        setStatus(d.node_id, 'skipped')
        logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'skipped' })
      },
      onFinished(d) { logs.value.push({ kind: 'meta', text: `工作流${d.status === 'failed' ? '失败' : '完成'} · 耗时 ${d.duration_ms}ms` }) },
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
    if (n?.type === 'agent') { syncStructRows() }
  }
})

// 右键菜单：点击空白处关闭 + 运行中计时 tick
onMounted(() => window.addEventListener('click', closeContextMenu))
onMounted(() => { tickTimer = setInterval(() => { nowTick.value = Date.now() }, 1000) })
onBeforeUnmount(() => {
  window.removeEventListener('click', closeContextMenu)
  clearInterval(tickTimer)
  endDrawerResize()
})

// ── 抽屉宽度拖拽（左边缘把手，280–560px，记忆到 localStorage） ──
const DRAWER_W_KEY = 'knowsource.workflow.drawerWidth'
const drawerWidth = ref(parseInt(localStorage.getItem(DRAWER_W_KEY) || '', 10) || 340)
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
  selectedNode.value.data.config.structured_outputs = structRows.value
    .filter(r => r.name.trim())
    .map(r => ({ name: r.name.trim(), type: r.type, description: r.description.trim() }))
}
// 保存前校验：结构化输出里字段名填了但说明为空 → 提示（说明是大模型识别字段的关键）
function validateStructRows() {
  for (const n of nodes.value) {
    if (n.type !== 'agent') continue
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
      <button class="btn" @click="consoleOpen = !consoleOpen">日志</button>
      <button class="btn" @click="save" :disabled="saving">{{ saving ? '保存中...' : '保存' }}</button>
      <button class="btn primary" @click="openRunModal" :disabled="running">▶ 运行</button>
    </div>

    <div class="wf-body">
      <!-- 左：节点面板 -->
      <aside class="wf-palette">
        <div class="pal-group">节点</div>
        <button v-for="t in palette.node_types" :key="t.type" class="pal-item" draggable="true" @dragstart="onDragStart($event, t.type)" @click="addNode(t.type)">
          <span class="pal-ico" :style="{ background: TYPE_META[t.type]?.color || '#64748b' }">{{ t.icon }}</span>
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
      <aside class="wf-drawer" :style="{ width: drawerWidth + 'px' }">
        <div class="drawer-resizer" title="拖拽调节宽度" @pointerdown="startDrawerResize"></div>
        <template v-if="selectedNode">
          <div class="dr-head">
            <span class="dr-ico" :style="{ background: TYPE_META[selectedType]?.color || '#64748b' }">{{ TYPE_META[selectedType]?.icon }}</span>
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
                      <div class="var-chips">
                        <button v-for="f in outputFieldsOf(n)" :key="f" type="button" class="var-chip" @click="appendEndRowFromVar(n.id, f)">{{ n.id }}.{{ f }}</button>
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
                <textarea v-model="selectedConfig.code_text" rows="10" style="font-family:ui-monospace,monospace"></textarea>
              </div>
            </template>

            <!-- ═══ 分区二：变量配置（输入引用 / 参数插值 / 输出声明） ═══ -->
            <template v-if="selectedType !== 'start' && selectedType !== 'end'">
              <div class="section-title">变量配置</div>

              <!-- 输入变量：沿连线可流入本节点的上游输出（智能体用「⊕ 插入变量」，不显示此块） -->
              <div class="field" v-if="selectedType !== 'agent'">
                <template v-if="upstreamNodes.some(n => outputFieldsOf(n).length)">
                  <label>输入变量（上游，点击复制）</label>
                  <template v-for="n in upstreamNodes" :key="n.id">
                    <div v-if="outputFieldsOf(n).length" class="upstream-node">
                      <div class="up-node-name">{{ n.data?.title || n.type }} · <code>{{ n.id }}</code></div>
                      <div class="var-chips">
                        <button v-for="f in outputFieldsOf(n)" :key="f" type="button" class="var-chip" @click="copyText(varRef(n.id, f))">{{ n.id }}.{{ f }}</button>
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
              <div class="field" v-if="selectedType === 'llm'">
                <label>Prompt 模板</label>
                <textarea v-model="selectedConfig.prompt_template" rows="4"></textarea>
                <span class="var-btn" @click="insertVar('prompt_template')">⊕ 插入变量</span>
              </div>
              <div class="field" v-if="selectedType === 'code'">
                <label>参数（JSON，可用变量）</label>
                <textarea :value="jsonText('params')" @input="setJson('params', $event.target.value)" rows="3" placeholder='{"x":"{{agent.answer}}"}'></textarea>
              </div>

              <!-- 输出变量：固定字段（只读，点击复制；智能体节点在结构化输出表中展示，不重复显示） -->
              <div class="field" v-if="selectedType !== 'agent'">
                <label>输出变量（🔒 固定，点击复制）</label>
                <div class="var-chips" v-if="displayOutputFields(selectedNode).length">
                  <span
                    v-for="f in displayOutputFields(selectedNode)" :key="f"
                    class="var-chip var-chip-edit var-chip-fixed"
                  >
                    <span class="vc-name" @click="copyText(varRef(selectedNodeId, f))">{{ f }}</span>
                  </span>
                </div>
              </div>

              <!-- 智能体：结构化输出（固定字段锁定行 + 自定义字段行） -->
              <div class="field" v-if="selectedType === 'agent'">
                <label>结构化输出（🔒 固定始终输出；自定义字段由大模型根据「说明」生成，下游引用 <code v-pre>{{节点.字段}}</code>）</label>
                <div class="struct-table">
                  <div class="st-head">
                    <span class="st-col st-col-name">字段名</span>
                    <span class="st-col st-col-type">类型</span>
                    <span class="st-col st-col-desc">说明（必填，供大模型识别）</span>
                    <span class="st-col st-col-op"></span>
                  </div>
                  <!-- 固定字段：锁定行（不可编辑/删除） -->
                  <div v-for="f in FIXED_STRUCT_FIELDS" :key="f.name" class="end-row struct-row struct-row-fixed">
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
                    <input type="text" v-model="row.description" placeholder="如 奥雷里亚诺的个数" class="st-col st-col-desc" @change="flushStructRows">
                    <button type="button" class="btn sm st-col st-col-op" @click="structRows.splice(ri, 1); flushStructRows()">×</button>
                  </div>
                  <button type="button" class="btn sm" @click="structRows.push({ name: '', type: 'string', description: '' }); flushStructRows()">＋ 添加字段</button>
                </div>
              </div>
            </template>
          </div>
        </template>

        <div class="dr-empty" v-else>点击画布上的节点<br>编辑它的配置</div>
      </aside>
    </div>

    <!-- 底部运行日志控制台 -->
    <div class="wf-console" v-if="consoleOpen">
      <div class="console-head">
        <span class="console-title">{{ running ? '运行中...' : '运行日志' }}</span>
        <span class="console-spacer"></span>
        <button class="btn sm" @click="clearLogs">清空</button>
        <button class="btn sm" @click="consoleOpen = false">收起</button>
      </div>
      <div class="console-body">
        <div v-for="(l, i) in logs" :key="i">
          <div v-if="l.kind === 'meta'" class="log-line log-meta">{{ l.text }}</div>
          <div v-else class="log-line log-node" :class="'st-' + l.status" @click="toggleLog(i)">
            <span class="log-status">{{ statusIcon(l.status) }}</span>
            <span class="log-title">{{ l.title || l.node_id }}</span>
            <span class="log-summary" v-if="l.summary || l.error">{{ l.summary || l.error }}</span>
            <span class="log-dur" v-if="l.status === 'running' && runningSince[l.node_id]">{{ fmtElapsed(nowTick - runningSince[l.node_id]) }}</span>
            <span class="log-dur" v-else-if="l.duration_ms != null">{{ l.duration_ms }}ms</span>
            <span class="log-expand" v-if="l.output != null || l.error">{{ expandedLog === i ? '▾' : '▸' }}</span>
          </div>
          <div class="log-output" v-if="expandedLog === i && (l.output != null || l.error)">
            <pre>{{ logDetail(l) }}</pre>
          </div>
        </div>
        <div class="console-empty" v-if="!logs.length">暂无运行日志，点击「▶ 运行」开始</div>
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
          <label>{{ it.label || it.name }} <span v-if="it.required" class="req">*</span></label>
          <input type="text" v-model="runInputs[it.name]" :placeholder="it.name">
        </div>
        <div class="m-sub" v-if="!startInputs.length">该工作流没有声明输入变量，直接运行。</div>
        <div class="m-actions">
          <button class="btn" @click="runModal = false">取消</button>
          <button class="btn primary" @click="startRun">开始运行</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
.pal-name { font-size: 12.5px; font-weight: 600; }
.pal-hint { margin-top: auto; padding: 8px; font-size: 11px; color: var(--c-secondary); border: 1px dashed var(--c-border); border-radius: 8px; line-height: 1.6; }
.pal-hint code { font-family: ui-monospace, monospace; color: var(--c-accent); }

/* 画布 */
.wf-canvas-wrap { flex: 1; position: relative; border: 1px solid var(--c-border); border-radius: 12px; overflow: hidden; background: var(--c-bg); }
.wf-console { flex-shrink: 0; height: 200px; display: flex; flex-direction: column; border: 1px solid var(--c-border); border-radius: 10px; background: var(--c-panel); overflow: hidden; }
.console-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--c-border); flex-shrink: 0; }
.console-title { font-size: 12px; font-weight: 700; }
.console-spacer { flex: 1; }
.console-body { flex: 1; overflow-y: auto; padding: 6px 0; font-size: 12px; font-family: ui-monospace, monospace; }
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
.log-title { flex-shrink: 0; font-weight: 600; color: var(--c-fg); }
.log-summary { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-secondary); }
.log-node.st-failed .log-summary { color: var(--c-danger); }
.log-dur { flex-shrink: 0; color: var(--c-secondary); opacity: .8; }
.log-expand { flex-shrink: 0; color: var(--c-secondary); }
.log-output { padding: 0 12px 8px 26px; }
.log-output pre { margin: 0; padding: 8px; border-radius: 6px; background: var(--c-muted); font-size: 11px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; max-height: 180px; overflow-y: auto; }

/* 右键菜单 */
.ctx-menu { position: fixed; z-index: 200; min-width: 120px; padding: 4px; border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-panel-elevated, var(--c-panel)); box-shadow: 0 8px 24px rgba(0,0,0,.18); }
.ctx-item { display: block; width: 100%; text-align: left; padding: 7px 12px; border: 0; background: transparent; color: var(--c-fg); font-size: 12.5px; font-family: var(--font); cursor: pointer; border-radius: 6px; }
.ctx-item:hover { background: var(--c-muted); }
.ctx-item.danger { color: var(--c-danger); }
.ctx-item.danger:hover { background: color-mix(in srgb, var(--c-danger) 12%, transparent); }

/* 右抽屉 */
.wf-drawer { position: relative; width: 340px; flex-shrink: 0; display: flex; flex-direction: column; border: 1px solid var(--c-border); border-radius: 12px; background: var(--c-panel); overflow: hidden; }
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
