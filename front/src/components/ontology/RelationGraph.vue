<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { createConstraint, updateConstraint, deleteConstraint } from '../../api'

const props = defineProps({
  constraints: { type: Array, default: () => [] },
  allConstraints: { type: Array, default: () => [] },
  categoryId: { type: String, required: true },
  ontologies: { type: Array, default: () => [] },
  relations: { type: Array, default: () => [] },
  searchQuery: { type: String, default: '' },
})

const emit = defineEmits(['changed'])

const NODE_COLORS = [
  '#5b8fbc', '#7aa17e', '#c2a45c', '#c48b7a', '#7b8db0',
  '#8aa88a', '#bc9a5e', '#b8887a', '#6b90b5', '#8a9e8a',
  '#ab8d5c', '#9a7fb0',
]

const WORLD_W = 1200
const WORLD_H = 760
const defaultViewBox = { x: 0, y: 0, w: WORLD_W, h: WORLD_H }

const svgRef = ref(null)
const viewBox = ref({ ...defaultViewBox })
const zoomLevel = ref(1)
const MIN_ZOOM = 0.25
const MAX_ZOOM = 3

const nodes = ref([])
const edges = ref([])
const colorMap = ref({})
const selectedNodeId = ref('')
const selectedEdgeKey = ref('')
const layoutMsg = ref('')
let layoutMsgTimer = null

const isDragging = ref(false)
const isPanning = ref(false)
const dragNode = ref(null)
const dragOffset = ref({ x: 0, y: 0 })
const panStart = ref({ x: 0, y: 0 })
const panViewBoxStart = ref({ x: 0, y: 0 })

// 对话框状态
const showAddDialog = ref(false)
const showEditDialog = ref(false)
const dialogError = ref('')
const submitting = ref(false)
const addForm = ref({ sourceId: '', relationId: '', targetId: '' })
const editForm = ref({ relationId: '' })
const editingEdge = ref(null)

// 右键菜单状态
const contextMenu = ref({ show: false, x: 0, y: 0, edge: null })

const nodeMap = computed(() => {
  const m = {}
  for (const n of nodes.value) m[n.id] = n
  return m
})

const edgeMap = computed(() => {
  const m = {}
  for (const e of edges.value) m[e.key] = e
  return m
})

const ontologyOptions = computed(() =>
  props.ontologies.map(o => ({ value: o.id, label: o.name }))
)

const relationOptions = computed(() =>
  props.relations.map(r => ({ value: r.id, label: r.name }))
)

// 曲线参数：同一对节点的多条平行边扇形分开，避免重叠
const edgeRenderList = computed(() => {
  const map = nodeMap.value
  const pairIndex = new Map()
  const counts = new Map()
  for (const e of edges.value) {
    const key = [e.source, e.target].sort().join('||')
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  const seen = new Map()

  return edges.value.map(edge => {
    const sn = map[edge.source]
    const tn = map[edge.target]
    if (!sn || !tn) return { ...edge, sx: 0, sy: 0, tx: 0, ty: 0, mx: 0, my: 0, cx: 0, cy: 0, lx: 0, ly: 0 }
    const key = [edge.source, edge.target].sort().join('||')
    const total = counts.get(key) || 1
    const idx = seen.get(key) || 0
    seen.set(key, idx + 1)
    const spread = total <= 1 ? 0 : (idx - (total - 1) / 2) * 0.18

    const sr = getNodeRadius(sn) + 3
    const tr = getNodeRadius(tn) + 3
    const sx = sn.x, sy = sn.y, tx = tn.x, ty = tn.y
    const dx = tx - sx, dy = ty - sy
    const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1)
    const ux = dx / dist, uy = dy / dist
    const sxo = sx + ux * sr, syo = sy + uy * sr
    const txo = tx - ux * tr, tyo = ty - uy * tr
    const mx = (sxo + txo) / 2, my = (syo + tyo) / 2
    const cx = mx - uy * dist * (0.12 + spread)
    const cy = my + ux * dist * (0.12 + spread)
    const lx = (mx + cx) / 2
    const ly = (my + cy) / 2
    return { ...edge, sx: sxo, sy: syo, tx: txo, ty: tyo, mx, my, cx, cy, lx, ly }
  })
})

const selectedNodeEdges = computed(() => {
  if (!selectedNodeId.value) return []
  return edges.value.filter(e => e.source === selectedNodeId.value || e.target === selectedNodeId.value)
})

const selectedEdgeDetail = computed(() => {
  if (!selectedEdgeKey.value) return null
  const e = edgeMap.value[selectedEdgeKey.value]
  if (!e) return null
  const sn = nodeMap.value[e.source]
  const tn = nodeMap.value[e.target]
  return {
    ...e,
    sourceName: sn ? sn.name : '?',
    targetName: tn ? tn.name : '?',
  }
})

function getNodeColor(node) {
  return colorMap.value[node.id] || '#8899aa'
}

function getNodeRadius(node) {
  if (node.id === selectedNodeId.value) return 20
  return 11 + Math.min(node.degree * 1.2, 7)
}

function getNodeLabel(node) {
  return node.name.length > 12 ? node.name.slice(0, 11) + '..' : node.name
}

// ===== 关系 CRUD 操作 =====

function getOntologyName(id) {
  const o = props.ontologies.find(x => x.id === id)
  return o ? o.name : '?'
}

function getRelationName(id) {
  const r = props.relations.find(x => x.id === id)
  return r ? r.name : '?'
}

function openAddDialog() {
  dialogError.value = ''
  addForm.value = { sourceId: '', relationId: '', targetId: '' }
  showAddDialog.value = true
}

function openEditDialog(edge) {
  dialogError.value = ''
  editingEdge.value = edge
  editForm.value = { relationId: edge.relationId || '' }
  showEditDialog.value = true
}

async function submitAdd() {
  dialogError.value = ''
  const { sourceId, relationId, targetId } = addForm.value
  if (!sourceId || !relationId || !targetId) {
    dialogError.value = '请完整填写起点、关系和终点'
    return
  }
  if (sourceId === targetId) {
    dialogError.value = '起点和终点不能是同一个本体'
    return
  }
  // 前端预校验：检查是否已存在相同 source-target 的约束
  const dup = props.constraints.find(
    c => c.source_ontology_id === sourceId && c.target_ontology_id === targetId
  )
  if (dup) {
    dialogError.value = `「${getOntologyName(sourceId)}」与「${getOntologyName(targetId)}」之间已存在关系约束，每对本体只能建立一个关系`
    return
  }
  submitting.value = true
  try {
    await createConstraint(props.categoryId, {
      source_ontology_id: sourceId,
      relation_id: relationId,
      target_ontology_id: targetId,
    })
    showAddDialog.value = false
    emit('changed')
  } catch (e) {
    dialogError.value = e.message || '创建失败'
  } finally {
    submitting.value = false
  }
}

async function submitEdit() {
  dialogError.value = ''
  if (!editForm.value.relationId) {
    dialogError.value = '请选择关系类型'
    return
  }
  const edge = editingEdge.value
  if (!edge) return
  submitting.value = true
  try {
    await updateConstraint(props.categoryId, edge.id, {
      relation_id: editForm.value.relationId,
    })
    showEditDialog.value = false
    selectedEdgeKey.value = ''
    emit('changed')
  } catch (e) {
    dialogError.value = e.message || '修改失败'
  } finally {
    submitting.value = false
  }
}

async function removeEdge(edge) {
  const srcName = getOntologyName(edge.source)
  const tgtName = getOntologyName(edge.target)
  if (!confirm(`确认删除关系「${srcName} —${edge.label}→ ${tgtName}」？`)) return
  try {
    await deleteConstraint(props.categoryId, edge.id)
    selectedEdgeKey.value = ''
    emit('changed')
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

// ===== 图谱构建 =====

function buildGraph() {
  const nodeMapBuild = new Map()
  const edgeList = []
  const palette = {}

  for (const c of props.constraints) {
    const sid = c.source_ontology_id
    const tid = c.target_ontology_id
    if (!sid || !tid) continue
    if (!nodeMapBuild.has(sid)) {
      nodeMapBuild.set(sid, { id: sid, name: c.source_ontology_name || sid, degree: 0 })
    }
    if (!nodeMapBuild.has(tid)) {
      nodeMapBuild.set(tid, { id: tid, name: c.target_ontology_name || tid, degree: 0 })
    }
    const edgeKey = `${sid}||${c.relation_id || c.relation_name}||${tid}`
    if (!edgeList.some(e => e.key === edgeKey)) {
      edgeList.push({
        key: edgeKey, id: c.id,
        source: sid, target: tid,
        label: c.relation_name || '',
        relationId: c.relation_id || '',
      })
      nodeMapBuild.get(sid).degree++
      nodeMapBuild.get(tid).degree++
    }
  }

  const list = Array.from(nodeMapBuild.values())
  list.sort((a, b) => b.degree - a.degree)
  list.forEach((n, i) => { palette[n.id] = NODE_COLORS[i % NODE_COLORS.length] })

  const cx = WORLD_W / 2
  const cy = WORLD_H / 2
  const radius = Math.min(WORLD_W, WORLD_H) * 0.36
  const nodeList = list.map((n, index) => {
    let x, y
    if (list.length === 1) { x = cx; y = cy }
    else {
      const angle = (index / list.length) * Math.PI * 2
      x = cx + Math.cos(angle) * radius
      y = cy + Math.sin(angle) * radius
    }
    return { ...n, x, y, vx: 0, vy: 0, fixed: false }
  })

  nodes.value = nodeList
  edges.value = edgeList
  colorMap.value = palette
  selectedNodeId.value = ''
  selectedEdgeKey.value = ''
  viewBox.value = { ...defaultViewBox }
  zoomLevel.value = 1
}

// ======================= Force simulation =======================
function simulationStep() {
  const arr = nodes.value
  if (!arr.length) return 0
  const repulsion = 14000
  const attraction = 0.003
  const gravity = 0.0025
  const damping = 0.82
  const minDist = 82

  const neighbors = new Map()
  for (const n of arr) neighbors.set(n.id, new Set())
  for (const e of edges.value) {
    if (neighbors.has(e.source)) neighbors.get(e.source).add(e.target)
    if (neighbors.has(e.target)) neighbors.get(e.target).add(e.source)
  }

  let maxSpeed = 0
  for (let i = 0; i < arr.length; i++) {
    const a = arr[i]
    if (a === dragNode.value || a.fixed) continue
    let fx = 0, fy = 0
    for (let j = 0; j < arr.length; j++) {
      if (i === j) continue
      const b = arr[j]
      const dx = a.x - b.x, dy = a.y - b.y
      const dist = Math.max(minDist, Math.sqrt(dx * dx + dy * dy))
      const force = repulsion / (dist * dist)
      fx += (dx / dist) * force
      fy += (dy / dist) * force
    }
    const myN = neighbors.get(a.id) || new Set()
    for (const nid of myN) {
      const b = arr.find(n => n.id === nid)
      if (!b) continue
      fx += (b.x - a.x) * attraction
      fy += (b.y - a.y) * attraction
    }
    fx += (WORLD_W / 2 - a.x) * gravity
    fy += (WORLD_H / 2 - a.y) * gravity
    a.vx = (a.vx + fx) * damping
    a.vy = (a.vy + fy) * damping
    a.x += a.vx
    a.y += a.vy
    a.x = Math.max(50, Math.min(WORLD_W - 50, a.x))
    a.y = Math.max(50, Math.min(WORLD_H - 50, a.y))
    const speed = Math.sqrt(a.vx * a.vx + a.vy * a.vy)
    if (speed > maxSpeed) maxSpeed = speed
  }
  return maxSpeed
}

let simTimer = null
let settleCounter = 0
function startSimulation() {
  stopSimulation()
  settleCounter = 0
  function tick() {
    let maxSpeed = 0
    for (let i = 0; i < 3; i++) {
      const s = simulationStep()
      if (s > maxSpeed) maxSpeed = s
    }
    if (!isDragging.value && maxSpeed < 0.03) {
      settleCounter++
      if (settleCounter > 120) { simTimer = null; return }
    } else {
      settleCounter = 0
    }
    simTimer = requestAnimationFrame(tick)
  }
  simTimer = requestAnimationFrame(tick)
}
function stopSimulation() {
  if (simTimer) { cancelAnimationFrame(simTimer); simTimer = null }
}

function relayout() {
  for (const n of nodes.value) { n.fixed = false; n.vx = 0; n.vy = 0 }
  startSimulation()
}

// ===== 调整布局：环形排列 + 最小化连线交叉 =====
function nodeDeg(id) {
  const n = nodes.value.find(x => x.id === id)
  return n ? n.degree : 0
}

function chordCrossings(order) {
  const pos = {}
  order.forEach((id, i) => { pos[id] = i })
  const E = edges.value
  let cross = 0
  for (let i = 0; i < E.length; i++) {
    let a = pos[E[i].source], b = pos[E[i].target]
    if (a > b) { const t = a; a = b; b = t }
    for (let j = i + 1; j < E.length; j++) {
      let c = pos[E[j].source], d = pos[E[j].target]
      if (c > d) { const t = c; c = d; d = t }
      if (a === c || a === d || b === c || b === d) continue
      if ((a < c && c < b && b < d) || (c < a && a < d && d < b)) cross++
    }
  }
  return cross
}

function optimizeOrder(start) {
  let order = start.slice()
  let best = chordCrossings(order)
  let improved = true
  let guard = 0
  while (improved && guard++ < 60) {
    improved = false
    for (let i = 0; i < order.length; i++) {
      for (let j = i + 1; j < order.length; j++) {
        const test = order.slice()
        const tmp = test[i]; test[i] = test[j]; test[j] = tmp
        const c = chordCrossings(test)
        if (c < best) { best = c; order = test; improved = true }
      }
    }
  }
  return { order, crossings: best }
}

function shuffleArr(arr) {
  const a = arr.slice()
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    const t = a[i]; a[i] = a[j]; a[j] = t
  }
  return a
}

function dfsOrder(ids) {
  const adj = {}
  ids.forEach(id => { adj[id] = new Set() })
  for (const e of edges.value) {
    if (adj[e.source]) adj[e.source].add(e.target)
    if (adj[e.target]) adj[e.target].add(e.source)
  }
  const start = ids.slice().sort((a, b) => nodeDeg(b) - nodeDeg(a))[0]
  const visited = new Set()
  const order = []
  const stack = [start]
  while (stack.length) {
    const cur = stack.pop()
    if (visited.has(cur)) continue
    visited.add(cur); order.push(cur)
    const nbrs = [...(adj[cur] || [])].sort((a, b) => nodeDeg(b) - nodeDeg(a))
    for (const n of nbrs) if (!visited.has(n)) stack.push(n)
  }
  for (const id of ids) if (!visited.has(id)) order.push(id)
  return order
}

function crossingsFromPositions(pos, E) {
  const ccw = (ax, ay, bx, by, cx, cy) => (cy - ay) * (bx - ax) > (by - ay) * (cx - ax)
  const inter = (ax, ay, bx, by, cx, cy, dx, dy) => {
    if ((ax === cx && ay === cy) || (ax === dx && ay === dy) || (bx === cx && by === cy) || (bx === dx && by === dy)) return false
    return ccw(ax, ay, cx, cy, dx, dy) !== ccw(bx, by, cx, cy, dx, dy) && ccw(ax, ay, bx, by, cx, cy) !== ccw(ax, ay, bx, by, dx, dy)
  }
  let c = 0
  for (let i = 0; i < E.length; i++) {
    for (let j = i + 1; j < E.length; j++) {
      const A = pos[E[i].source], B = pos[E[i].target], C = pos[E[j].source], D = pos[E[j].target]
      if (!A || !B || !C || !D) continue
      if (inter(A.x, A.y, B.x, B.y, C.x, C.y, D.x, D.y)) c++
    }
  }
  return c
}

function forceCandidate(ids, adj, seed) {
  const cx = WORLD_W / 2, cy = WORLD_H / 2
  const baseR = Math.min(WORLD_W, WORLD_H) * 0.34
  const golden = 2.39996
  const ns = ids.map((id, i) => {
    const ang = (i + 1) * golden * (1 + seed * 0.37)
    const rr = baseR * (0.55 + ((i * 7 + seed * 3) % 6) / 14)
    return { id, x: cx + Math.cos(ang) * rr, y: cy + Math.sin(ang) * rr, vx: 0, vy: 0 }
  })
  const byId = {}
  ns.forEach(n => { byId[n.id] = n })
  const rep = 15000, att = 0.0035, grav = 0.003, damp = 0.86, minD = 80
  for (let step = 0; step < 700; step++) {
    for (let i = 0; i < ns.length; i++) {
      const a = ns[i]
      let fx = 0, fy = 0
      for (let j = 0; j < ns.length; j++) {
        if (i === j) continue
        const b = ns[j]
        const dx = a.x - b.x, dy = a.y - b.y
        const d = Math.max(minD, Math.hypot(dx, dy))
        const f = rep / (d * d)
        fx += dx / d * f; fy += dy / d * f
      }
      for (const nid of adj[a.id]) {
        const b = byId[nid]
        if (!b) continue
        fx += (b.x - a.x) * att; fy += (b.y - a.y) * att
      }
      fx += (cx - a.x) * grav; fy += (cy - a.y) * grav
      a.vx = (a.vx + fx) * damp; a.vy = (a.vy + fy) * damp
      a.x += a.vx; a.y += a.vy
      a.x = Math.max(60, Math.min(WORLD_W - 60, a.x))
      a.y = Math.max(60, Math.min(WORLD_H - 60, a.y))
    }
  }
  const pos = {}
  ns.forEach(n => { pos[n.id] = { x: n.x, y: n.y } })
  return pos
}

function annealPositions(startPos, E, ids) {
  const cur = {}
  for (const id in startPos) cur[id] = { x: startPos[id].x, y: startPos[id].y }
  let curC = crossingsFromPositions(cur, E)
  let bestC = curC
  let bestPos = {}
  for (const id in cur) bestPos[id] = { x: cur[id].x, y: cur[id].y }
  let T = 1.3
  for (let iter = 0; iter < 14000; iter++) {
    const id = ids[Math.floor(Math.random() * ids.length)]
    const old = { x: cur[id].x, y: cur[id].y }
    const step = 70 * T + 8
    cur[id].x = Math.max(60, Math.min(WORLD_W - 60, cur[id].x + (Math.random() * 2 - 1) * step))
    cur[id].y = Math.max(60, Math.min(WORLD_H - 60, cur[id].y + (Math.random() * 2 - 1) * step))
    const newC = crossingsFromPositions(cur, E)
    const dC = newC - curC
    if (dC <= 0 || Math.random() < Math.exp(-dC / T)) {
      curC = newC
      if (curC < bestC) {
        bestC = curC
        bestPos = {}
        for (const k in cur) bestPos[k] = { x: cur[k].x, y: cur[k].y }
      }
    } else {
      cur[id].x = old.x; cur[id].y = old.y
    }
    T *= 0.9996
  }
  return { pos: bestPos, c: bestC }
}

function optimizeLayout() {
  if (!nodes.value.length) return
  stopSimulation()
  const ids = nodes.value.map(n => n.id)
  const adj = {}
  ids.forEach(id => { adj[id] = new Set() })
  const E = edges.value.map(e => ({ source: e.source, target: e.target }))
  E.forEach(e => {
    if (adj[e.source]) adj[e.source].add(e.target)
    if (adj[e.target]) adj[e.target].add(e.source)
  })

  let best = null

  const byDeg = ids.slice().sort((a, b) => nodeDeg(b) - nodeDeg(a))
  const circStarts = [byDeg, byDeg.slice().reverse(), dfsOrder(ids)]
  for (let r = 0; r < 5; r++) circStarts.push(shuffleArr(ids))
  let bestCirc = null
  for (const s of circStarts) {
    const res = optimizeOrder(s)
    if (!bestCirc || res.crossings < bestCirc.crossings) bestCirc = res
  }
  const cx = WORLD_W / 2, cy = WORLD_H / 2, R = Math.min(WORLD_W, WORLD_H) * 0.4, m = bestCirc.order.length
  const circPos = {}
  bestCirc.order.forEach((id, i) => {
    const ang = (i / m) * Math.PI * 2 - Math.PI / 2
    circPos[id] = { x: cx + Math.cos(ang) * R, y: cy + Math.sin(ang) * R }
  })
  best = { pos: circPos, c: crossingsFromPositions(circPos, E) }

  for (let seed = 0; seed < 10; seed++) {
    const pos = forceCandidate(ids, adj, seed)
    const c = crossingsFromPositions(pos, E)
    if (c < best.c) best = { pos, c }
  }

  if (nodes.value.length <= 26 && E.length <= 100) {
    const ann = annealPositions(best.pos, E, ids)
    if (ann.c < best.c) best = ann
  }

  for (const n of nodes.value) {
    const p = best.pos[n.id]
    if (p) { n.x = p.x; n.y = p.y; n.vx = 0; n.vy = 0; n.fixed = false }
  }
  viewBox.value = { ...defaultViewBox }
  zoomLevel.value = 1
  layoutMsg.value = `调整布局：剩余 ${best.c} 处交叉`
  clearTimeout(layoutMsgTimer)
  layoutMsgTimer = setTimeout(() => { layoutMsg.value = '' }, 3500)
}

// ======================= Pan & Zoom =======================
function applyZoom(newZoom) {
  const vb = viewBox.value
  const cx = vb.x + vb.w / 2, cy = vb.y + vb.h / 2
  const w = WORLD_W / newZoom, h = WORLD_H / newZoom
  viewBox.value = { x: cx - w / 2, y: cy - h / 2, w, h }
  zoomLevel.value = newZoom
}
function zoomIn() { applyZoom(Math.min(MAX_ZOOM, zoomLevel.value * 1.4)) }
function zoomOut() { applyZoom(Math.max(MIN_ZOOM, zoomLevel.value / 1.4)) }
function zoomReset() { viewBox.value = { ...defaultViewBox }; zoomLevel.value = 1 }

function onWheel(event) {
  event.preventDefault()
  const delta = event.deltaY > 0 ? 0.85 : 1.15
  const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoomLevel.value * delta))
  const svg = svgRef.value
  if (!svg) { applyZoom(newZoom); return }
  const rect = svg.getBoundingClientRect()
  const mx = event.clientX - rect.left, my = event.clientY - rect.top
  const vb = viewBox.value
  const worldX = vb.x + (mx / rect.width) * vb.w
  const worldY = vb.y + (my / rect.height) * vb.h
  const w = WORLD_W / newZoom, h = WORLD_H / newZoom
  viewBox.value = { x: worldX - (mx / rect.width) * w, y: worldY - (my / rect.height) * h, w, h }
  zoomLevel.value = newZoom
}

function getSvgPoint(event) {
  const svg = svgRef.value
  if (!svg) return { x: 0, y: 0 }
  const pt = svg.createSVGPoint()
  pt.x = event.clientX; pt.y = event.clientY
  const ctm = svg.getScreenCTM()
  if (!ctm) return { x: event.clientX, y: event.clientY }
  const t = pt.matrixTransform(ctm.inverse())
  return { x: t.x, y: t.y }
}

function onNodeMouseDown(event, node) {
  event.stopPropagation(); event.preventDefault()
  isDragging.value = true
  dragNode.value = node
  node.fixed = true
  const pt = getSvgPoint(event)
  dragOffset.value = { x: pt.x - node.x, y: pt.y - node.y }
}
function onBackgroundMouseDown(event) {
  if (event.target === svgRef.value || event.target.classList.contains('rg-bg')) {
    isPanning.value = true
    panStart.value = { x: event.clientX, y: event.clientY }
    panViewBoxStart.value = { x: viewBox.value.x, y: viewBox.value.y }
  }
}
function onMouseMove(event) {
  if (isDragging.value && dragNode.value) {
    const pt = getSvgPoint(event)
    dragNode.value.x = pt.x - dragOffset.value.x
    dragNode.value.y = pt.y - dragOffset.value.y
    dragNode.value.vx = 0; dragNode.value.vy = 0
    return
  }
  if (isPanning.value) {
    const svg = svgRef.value
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    const sx = viewBox.value.w / rect.width
    const sy = viewBox.value.h / rect.height
    viewBox.value = {
      ...viewBox.value,
      x: panViewBoxStart.value.x - (event.clientX - panStart.value.x) * sx,
      y: panViewBoxStart.value.y - (event.clientY - panStart.value.y) * sy,
    }
  }
}
function onMouseUp() {
  if (isDragging.value && dragNode.value) {
    dragNode.value.vx = 0; dragNode.value.vy = 0
  }
  isDragging.value = false
  dragNode.value = null
  isPanning.value = false
}
function onNodeClick(node) {
  selectedNodeId.value = node.id
  selectedEdgeKey.value = ''
}
function onEdgeClick(edgeKey) {
  selectedEdgeKey.value = edgeKey
  selectedNodeId.value = ''
}

function onEdgeRightClick(event, edgeKey) {
  event.preventDefault()
  event.stopPropagation()
  const e = edgeMap.value[edgeKey]
  if (!e) return
  // 先选中该连线
  selectedEdgeKey.value = edgeKey
  selectedNodeId.value = ''
  // 显示右键菜单（使用 clientX/clientY 定位）
  contextMenu.value = { show: true, x: event.clientX, y: event.clientY, edge: e }
}

function onEdgeDblClick(edgeKey) {
  const e = edgeMap.value[edgeKey]
  if (!e) return
  selectedEdgeKey.value = edgeKey
  selectedNodeId.value = ''
  openEditDialog(e)
}

function hideContextMenu() {
  contextMenu.value = { show: false, x: 0, y: 0, edge: null }
}

function contextEdit() {
  const e = contextMenu.value.edge
  hideContextMenu()
  if (e) openEditDialog(e)
}

function contextDelete() {
  const e = contextMenu.value.edge
  hideContextMenu()
  if (e) removeEdge(e)
}

function clearSelection() {
  selectedNodeId.value = ''
  selectedEdgeKey.value = ''
  hideContextMenu()
}

watch(() => props.constraints, () => {
  buildGraph()
  if (nodes.value.length) startSimulation()
}, { deep: false })

onMounted(() => {
  buildGraph()
  if (nodes.value.length) startSimulation()
})
onUnmounted(stopSimulation)
</script>

<template>
  <div v-if="!nodes.length" class="rg-empty">
    暂无三元组约束，无法生成图谱
  </div>
  <div v-else class="rg-root">
    <div class="rg-meta">
      <span class="rg-chip">{{ nodes.length }} 个本体</span>
      <span class="rg-chip">{{ edges.length }} 条关系</span>
      <span v-if="props.searchQuery" class="rg-chip rg-chip-filter">筛选: {{ props.searchQuery }}</span>
      <span class="rg-chip">{{ Math.round(zoomLevel * 100) }}%</span>
      <span class="rg-hint">滚轮缩放 · 拖拽节点/画布 · 点击/右键/双击连线编辑 · 拖动后球会固定在松手处</span>
      <span class="rg-spacer"></span>
      <span v-if="layoutMsg" class="rg-layout-msg">{{ layoutMsg }}</span>
      <button class="rg-add-btn" @click="openAddDialog" title="添加新的关系约束">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        添加关系
      </button>
    </div>

    <div class="rg-main">
      <div class="rg-canvas-wrap">
        <div class="rg-canvas" :class="{ panning: isPanning }">
          <svg
            ref="svgRef"
            :viewBox="`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`"
            class="rg-svg"
            preserveAspectRatio="xMidYMid meet"
            @mousedown="onBackgroundMouseDown"
            @mousemove="onMouseMove"
            @mouseup="onMouseUp"
            @mouseleave="onMouseUp"
            @wheel.prevent="onWheel"
          >
            <defs>
              <radialGradient id="rgNodeGrad" cx="38%" cy="32%">
                <stop offset="0%" stop-color="white" stop-opacity="0.5" />
                <stop offset="60%" stop-color="white" stop-opacity="0.06" />
                <stop offset="100%" stop-color="black" stop-opacity="0.15" />
              </radialGradient>
              <filter id="rgShadow"><feDropShadow dx="0" dy="1.5" stdDeviation="2.5" flood-color="#000" flood-opacity="0.35" /></filter>
              <filter id="rgGlow"><feGaussianBlur stdDeviation="5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
              <marker id="rgArrow" markerWidth="12" markerHeight="10" refX="10" refY="5" orient="auto" markerUnits="userSpaceOnUse">
                <polygon points="0 0, 12 5, 0 10" fill="rgba(200,215,230,0.85)" />
              </marker>
              <marker id="rgArrowHl" markerWidth="12" markerHeight="10" refX="10" refY="5" orient="auto" markerUnits="userSpaceOnUse">
                <polygon points="0 0, 12 5, 0 10" fill="rgba(255,255,255,0.95)" />
              </marker>
            </defs>

            <rect class="rg-bg" x="0" y="0" :width="WORLD_W" :height="WORLD_H" fill="transparent" @click="clearSelection" />

            <g class="rg-edges">
              <path
                v-for="edge in edgeRenderList"
                :key="edge.key"
                :d="`M ${edge.sx} ${edge.sy} Q ${edge.cx} ${edge.cy} ${edge.tx} ${edge.ty}`"
                class="rg-edge"
                :class="{
                  hl: selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId),
                  selected: edge.key === selectedEdgeKey,
                }"
                fill="none"
                :marker-end="(selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId)) || edge.key === selectedEdgeKey ? 'url(#rgArrowHl)' : 'url(#rgArrow)'"
                @click.stop="onEdgeClick(edge.key)"
                @contextmenu.prevent="onEdgeRightClick($event, edge.key)"
                @dblclick.stop="onEdgeDblClick(edge.key)"
              />
              <!-- invisible wider hit area for easier edge clicking -->
              <path
                v-for="edge in edgeRenderList"
                :key="'hit-' + edge.key"
                :d="`M ${edge.sx} ${edge.sy} Q ${edge.cx} ${edge.cy} ${edge.tx} ${edge.ty}`"
                fill="none" stroke="transparent" stroke-width="14"
                class="rg-edge-hit"
                @click.stop="onEdgeClick(edge.key)"
                @contextmenu.prevent="onEdgeRightClick($event, edge.key)"
                @dblclick.stop="onEdgeDblClick(edge.key)"
              />
            </g>

            <g class="rg-edge-labels">
              <text
                v-for="edge in edgeRenderList"
                :key="'l' + edge.key"
                :x="edge.lx" :y="edge.ly"
                class="rg-edge-label"
                :class="{
                  hl: selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId),
                  selected: edge.key === selectedEdgeKey,
                }"
                @click.stop="onEdgeClick(edge.key)"
                @contextmenu.prevent="onEdgeRightClick($event, edge.key)"
                @dblclick.stop="onEdgeDblClick(edge.key)"
              >{{ edge.label }}</text>
            </g>

            <g class="rg-nodes">
              <g
                v-for="node in nodes"
                :key="node.id"
                class="rg-node"
                :class="{ selected: node.id === selectedNodeId }"
                :transform="`translate(${node.x}, ${node.y})`"
                @mousedown.prevent="onNodeMouseDown($event, node)"
                @click.stop="onNodeClick(node)"
              >
                <circle :r="getNodeRadius(node) + 8" class="rg-glow" :class="{ active: node.id === selectedNodeId }" :stroke="getNodeColor(node)" fill="none" />
                <circle :r="getNodeRadius(node)" :fill="getNodeColor(node)" class="rg-circle" :filter="node.id === selectedNodeId ? 'url(#rgGlow)' : 'url(#rgShadow)'" />
                <circle :r="getNodeRadius(node)" fill="url(#rgNodeGrad)" pointer-events="none" />
                <text :y="getNodeRadius(node) + 15" class="rg-label" :class="{ bold: node.id === selectedNodeId }">{{ getNodeLabel(node) }}</text>
              </g>
            </g>
          </svg>

          <div class="rg-canvas-tools">
            <button class="rg-tool-btn" @click="optimizeLayout" title="一键最小化连线交叉的最佳环形布局">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5l4.6-1.9z"/><path d="M19 14l.7 1.8L21.5 16.5 19.7 17.2 19 19l-.7-1.8L16.5 16.5l1.8-.7z"/></svg>
              调整布局
            </button>
            <button class="rg-tool-btn" @click="relayout" title="解除手动固定，重新自动布局">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
              重新布局
            </button>
            <button class="rg-tool-btn rg-tool-btn-add" @click="openAddDialog" title="添加关系约束">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              添加
            </button>
          </div>

          <div class="rg-zoom">
            <button class="rg-zoom-btn" @click="zoomIn" title="放大">+</button>
            <button class="rg-zoom-btn reset" @click="zoomReset" title="重置">{{ Math.round(zoomLevel * 100) }}%</button>
            <button class="rg-zoom-btn" @click="zoomOut" title="缩小">&minus;</button>
          </div>

          <!-- 右键菜单 -->
          <Teleport to="body">
            <div
              v-if="contextMenu.show"
              class="rg-ctxmenu"
              :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
              @click.stop
            >
              <button class="rg-ctxitem" @click="contextEdit">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                编辑关系
              </button>
              <button class="rg-ctxitem rg-ctxitem-del" @click="contextDelete">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                删除关系
              </button>
            </div>
          </Teleport>
          <!-- 右键菜单遮罩（点击任意位置关闭）-->
          <Teleport to="body">
            <div v-if="contextMenu.show" class="rg-ctxmask" @click="hideContextMenu" @contextmenu.prevent="hideContextMenu"></div>
          </Teleport>
        </div>
      </div>

      <aside class="rg-inspector">
        <div class="rg-inspector-title">
          详情面板
          <button v-if="selectedNodeId || selectedEdgeKey" class="rg-clear-btn" @click="clearSelection" title="清除选择">✕</button>
        </div>

        <!-- 节点详情 -->
        <template v-if="selectedNodeId && nodeMap[selectedNodeId]">
          <div class="rg-card">
            <div class="rg-inspector-name">
              <span class="rg-dot" :style="{ background: getNodeColor(nodeMap[selectedNodeId]) }"></span>
              {{ nodeMap[selectedNodeId].name }}
            </div>
            <div class="rg-inspector-sub">连接 {{ selectedNodeEdges.length }} 条关系</div>
          </div>
          <div class="rg-rels">
            <div
              v-for="e in selectedNodeEdges" :key="e.key"
              class="rg-rel-item"
              :class="{ 'rg-rel-item-sel': e.key === selectedEdgeKey }"
              @click="onEdgeClick(e.key)"
            >
              <span class="rg-rel-src" v-if="e.source === selectedNodeId">{{ nodeMap[selectedNodeId].name }}</span>
              <span class="rg-rel-src other" v-else>{{ (nodeMap[e.source] || {}).name || '?' }}</span>
              <span class="rg-rel-type">—{{ e.label }}→</span>
              <span class="rg-rel-tgt" v-if="e.target === selectedNodeId">{{ nodeMap[selectedNodeId].name }}</span>
              <span class="rg-rel-tgt other" v-else>{{ (nodeMap[e.target] || {}).name || '?' }}</span>
              <span class="rg-rel-actions">
                <button class="rg-act-btn" title="编辑关系" @click.stop="openEditDialog(e)">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                </button>
                <button class="rg-act-btn rg-act-btn-del" title="删除关系" @click.stop="removeEdge(e)">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </span>
            </div>
            <div v-if="!selectedNodeEdges.length" class="rg-no-rels">无关联关系</div>
          </div>
        </template>

        <!-- 连线详情 -->
        <template v-else-if="selectedEdgeKey && selectedEdgeDetail">
          <div class="rg-card">
            <div class="rg-inspector-name">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
              关系详情
            </div>
          </div>
          <div class="rg-edge-detail">
            <div class="rg-edge-row">
              <span class="rg-edge-lbl">起点</span>
              <span class="rg-edge-val">{{ selectedEdgeDetail.sourceName }}</span>
            </div>
            <div class="rg-edge-row">
              <span class="rg-edge-lbl">关系</span>
              <span class="rg-edge-val rg-edge-rel">{{ selectedEdgeDetail.label }}</span>
            </div>
            <div class="rg-edge-row">
              <span class="rg-edge-lbl">终点</span>
              <span class="rg-edge-val">{{ selectedEdgeDetail.targetName }}</span>
            </div>
          </div>
          <div class="rg-edge-actions">
            <button class="btn sm primary" @click="openEditDialog(selectedEdgeDetail)">编辑关系</button>
            <button class="btn sm danger" @click="removeEdge(selectedEdgeDetail)">删除关系</button>
          </div>
        </template>

        <div v-else class="rg-inspector-empty">点击图谱中的本体节点或连线，查看详情并进行编辑操作。</div>
      </aside>
    </div>

    <!-- 添加关系对话框 -->
    <Teleport to="body">
      <div v-if="showAddDialog" class="rg-overlay" @click.self="showAddDialog = false">
        <div class="rg-dialog">
          <div class="rg-dialog-head">
            <span class="rg-dialog-title">添加关系约束</span>
            <button class="rg-dialog-close" @click="showAddDialog = false">✕</button>
          </div>
          <div class="rg-dialog-body">
            <div class="rg-dialog-hint">选择起点本体、关系类型和终点本体，约束抽取时仅生成符合的三元组</div>
            <div class="rg-form">
              <div class="rg-field">
                <label>起点本体</label>
                <select v-model="addForm.sourceId" class="rg-select">
                  <option value="" disabled>选择起点本体...</option>
                  <option v-for="o in ontologyOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </div>
              <div class="rg-field">
                <label>关系类型</label>
                <select v-model="addForm.relationId" class="rg-select">
                  <option value="" disabled>选择关系类型...</option>
                  <option v-for="r in relationOptions" :key="r.value" :value="r.value">{{ r.label }}</option>
                </select>
              </div>
              <div class="rg-field">
                <label>终点本体</label>
                <select v-model="addForm.targetId" class="rg-select">
                  <option value="" disabled>选择终点本体...</option>
                  <option v-for="o in ontologyOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
                </select>
              </div>
            </div>
            <div v-if="dialogError" class="rg-dialog-error">{{ dialogError }}</div>
          </div>
          <div class="rg-dialog-foot">
            <button class="btn" @click="showAddDialog = false">取消</button>
            <button class="btn primary" @click="submitAdd" :disabled="submitting">
              <span v-if="submitting" class="spinner"></span>
              确认添加
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 编辑关系对话框 -->
    <Teleport to="body">
      <div v-if="showEditDialog" class="rg-overlay" @click.self="showEditDialog = false">
        <div class="rg-dialog">
          <div class="rg-dialog-head">
            <span class="rg-dialog-title">修改关系类型</span>
            <button class="rg-dialog-close" @click="showEditDialog = false">✕</button>
          </div>
          <div class="rg-dialog-body">
            <div v-if="editingEdge" class="rg-dialog-info">
              当前关系：<strong>{{ getOntologyName(editingEdge.source) }}</strong> —{{ editingEdge.label }}→ <strong>{{ getOntologyName(editingEdge.target) }}</strong>
            </div>
            <div class="rg-field">
              <label>新关系类型</label>
              <select v-model="editForm.relationId" class="rg-select">
                <option value="" disabled>选择关系类型...</option>
                <option v-for="r in relationOptions" :key="r.value" :value="r.value">{{ r.label }}</option>
              </select>
            </div>
            <div v-if="dialogError" class="rg-dialog-error">{{ dialogError }}</div>
          </div>
          <div class="rg-dialog-foot">
            <button class="btn" @click="showEditDialog = false">取消</button>
            <button class="btn primary" @click="submitEdit" :disabled="submitting">
              <span v-if="submitting" class="spinner"></span>
              确认修改
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.rg-empty { padding: 36px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius); }

.rg-root { display: flex; flex-direction: column; gap: 10px; }
.rg-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.rg-chip {
  padding: 4px 10px; border: 1px solid var(--c-border); border-radius: 999px;
  background: var(--c-panel); color: var(--c-secondary); font-size: 12px; font-weight: 600;
}
.rg-chip-filter {
  border-color: rgba(99,140,220,0.4); background: rgba(99,140,220,0.1); color: #8bb5f5;
}
.rg-hint { font-size: 12px; color: var(--c-secondary); }
.rg-spacer { flex: 1; min-width: 0; }
.rg-add-btn {
  display: inline-flex; align-items: center; gap: 5px;
  height: 30px; padding: 0 14px; flex-shrink: 0;
  border: 1px solid var(--c-accent); border-radius: 8px;
  background: rgba(99,140,220,0.12); color: #8bb5f5;
  font-size: 12px; font-weight: 600; cursor: pointer;
  transition: background 120ms, color 120ms;
}
.rg-add-btn:hover { background: rgba(99,140,220,0.22); color: #a8cfff; }
.rg-canvas-tools {
  position: absolute; top: 12px; right: 12px; z-index: 2;
  display: flex; gap: 6px;
}
.rg-tool-btn {
  display: inline-flex; align-items: center; gap: 5px;
  height: 30px; padding: 0 12px;
  border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;
  background: rgba(22,27,34,0.92); color: #c9d1d9;
  font-size: 12px; font-weight: 600; cursor: pointer;
  backdrop-filter: blur(8px); transition: background 120ms, color 120ms;
}
.rg-tool-btn:hover { background: rgba(255,255,255,0.12); color: #fff; }
.rg-tool-btn-add { border-color: rgba(99,140,220,0.4); color: #8bb5f5; }
.rg-tool-btn-add:hover { background: rgba(99,140,220,0.2); color: #a8cfff; }
.rg-layout-msg {
  padding: 4px 10px; border-radius: 999px; flex-shrink: 0;
  background: rgba(99,140,220,0.15); color: #8bb5f5;
  font-size: 12px; font-weight: 600;
}

.rg-main { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 340px); gap: 14px; align-items: start; }
.rg-canvas-wrap { min-width: 0; }
.rg-canvas {
  position: relative; border: 1px solid var(--c-border); border-radius: 16px; overflow: hidden;
  background:
    radial-gradient(ellipse at 25% 25%, rgba(100,130,160,0.06), transparent 50%),
    radial-gradient(ellipse at 75% 70%, rgba(120,140,120,0.05), transparent 50%),
    #11161c;
  cursor: grab; min-height: 560px; max-height: calc(100vh - 280px);
}
.rg-canvas.panning { cursor: grabbing; }
.rg-svg { width: 100%; display: block; }

.rg-zoom {
  position: absolute; bottom: 12px; right: 12px; display: flex; gap: 2px;
  background: rgba(22,27,34,0.92); border: 1px solid rgba(255,255,255,0.1);
  border-radius: 10px; padding: 3px; backdrop-filter: blur(8px);
}
.rg-zoom-btn {
  width: 34px; height: 30px; display: inline-flex; align-items: center; justify-content: center;
  border: 0; border-radius: 7px; background: transparent; color: #c9d1d9;
  font-size: 12px; font-weight: 600; cursor: pointer; transition: background 120ms;
}
.rg-zoom-btn:hover { background: rgba(255,255,255,0.08); }
.rg-zoom-btn.reset { width: auto; min-width: 44px; font-size: 10px; }

/* 连线样式 */
.rg-edge { stroke: rgba(255,255,255,0.30); stroke-width: 1.6; transition: stroke 250ms, stroke-width 250ms; cursor: pointer; }
.rg-edge.hl { stroke: rgba(255,255,255,0.7); stroke-width: 2.8; }
.rg-edge.selected { stroke: #8bb5f5; stroke-width: 3; filter: drop-shadow(0 0 6px rgba(139,181,245,0.4)); }
.rg-edge-hit { cursor: pointer; }
.rg-edge-label {
  fill: rgba(255,255,255,0.55); font-size: 11px; text-anchor: middle; pointer-events: none;
  font-weight: 500; stroke: rgba(0,0,0,0.55); stroke-width: 2; paint-order: stroke;
  cursor: pointer;
}
.rg-edge-label.hl { fill: rgba(255,255,255,0.9); font-weight: 700; stroke: rgba(0,0,0,0.65); stroke-width: 3; pointer-events: none; }
.rg-edge-label.selected { fill: #8bb5f5; font-weight: 700; stroke: rgba(0,0,0,0.7); stroke-width: 3.5; pointer-events: none; }

.rg-node { cursor: pointer; }
.rg-glow { stroke-width: 1.5; opacity: 0; transition: opacity 250ms; }
.rg-glow.active { opacity: 0.25; }
.rg-circle { transition: r 200ms ease; }
.rg-label {
  fill: rgba(255,255,255,0.75); font-size: 12px; text-anchor: middle; pointer-events: none;
  font-weight: 500; text-shadow: 0 1px 4px rgba(0,0,0,0.7);
}
.rg-label.bold { fill: #fff; font-weight: 700; font-size: 13px; }

/* 侧栏 */
.rg-inspector {
  border: 1px solid var(--c-border); border-radius: 16px; padding: 14px;
  background: var(--c-panel); position: sticky; top: 16px;
  max-height: calc(100vh - 160px); overflow: auto;
}
.rg-inspector-title {
  font-size: 14px; font-weight: 700; margin-bottom: 10px;
  display: flex; align-items: center; justify-content: space-between;
}
.rg-clear-btn {
  width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center;
  border: 0; border-radius: 6px; background: transparent; color: var(--c-secondary);
  font-size: 12px; cursor: pointer;
}
.rg-clear-btn:hover { background: var(--c-muted); color: var(--c-fg); }
.rg-card { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.rg-inspector-name { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.rg-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
.rg-inspector-sub { color: var(--c-secondary); font-size: 12px; }
.rg-rels { display: flex; flex-direction: column; gap: 6px; }
.rg-rel-item {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 7px 9px; border-radius: 8px; background: var(--c-muted); font-size: 12px;
  cursor: pointer; border: 1px solid transparent; transition: border-color 150ms, background 150ms;
}
.rg-rel-item:hover { background: var(--c-border); }
.rg-rel-item-sel { border-color: #8bb5f5; background: rgba(139,181,245,0.08); }
.rg-rel-src, .rg-rel-tgt { font-weight: 600; }
.rg-rel-src.other, .rg-rel-tgt.other { color: var(--c-secondary); font-weight: 500; }
.rg-rel-type { color: var(--c-accent); font-weight: 700; font-size: 11px; }
.rg-rel-actions { margin-left: auto; display: flex; gap: 2px; }
.rg-act-btn {
  width: 24px; height: 24px; display: inline-flex; align-items: center; justify-content: center;
  border: 0; border-radius: 5px; background: transparent; color: var(--c-secondary);
  cursor: pointer; transition: background 120ms, color 120ms;
}
.rg-act-btn:hover { background: rgba(255,255,255,0.1); color: var(--c-fg); }
.rg-act-btn-del:hover { background: rgba(220,38,38,0.15); color: #f87171; }
.rg-no-rels { color: var(--c-secondary); font-size: 12px; font-style: italic; }
.rg-inspector-empty { color: var(--c-secondary); font-size: 13px; line-height: 1.6; }

/* 连线详情 */
.rg-edge-detail {
  display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px;
  padding: 10px; border-radius: 10px; background: var(--c-muted);
}
.rg-edge-row { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.rg-edge-lbl { color: var(--c-secondary); font-weight: 600; min-width: 36px; }
.rg-edge-val { font-weight: 600; }
.rg-edge-rel { color: var(--c-accent); }
.rg-edge-actions { display: flex; gap: 8px; }

/* 对话框 */
.rg-overlay {
  position: fixed; inset: 0; z-index: 9999;
  background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center;
  backdrop-filter: blur(4px);
}
.rg-dialog {
  background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 16px;
  width: 460px; max-width: 94vw; box-shadow: 0 16px 48px rgba(0,0,0,0.5);
}
.rg-dialog-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid var(--c-border);
}
.rg-dialog-title { font-size: 15px; font-weight: 700; }
.rg-dialog-close {
  width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center;
  border: 0; border-radius: 8px; background: transparent; color: var(--c-secondary);
  font-size: 14px; cursor: pointer;
}
.rg-dialog-close:hover { background: var(--c-muted); color: var(--c-fg); }
.rg-dialog-body { padding: 16px 20px; display: flex; flex-direction: column; gap: 12px; }
.rg-dialog-hint { font-size: 12px; color: var(--c-secondary); line-height: 1.5; }
.rg-dialog-info { font-size: 13px; color: var(--c-secondary); }
.rg-form { display: flex; flex-direction: column; gap: 10px; }
.rg-field { display: flex; flex-direction: column; gap: 4px; }
.rg-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.rg-select {
  height: 36px; padding: 0 10px; border: 1px solid var(--c-border); border-radius: 8px;
  background: var(--c-bg); color: var(--c-fg); font-size: 13px; outline: none;
}
.rg-select:focus { border-color: #8bb5f5; }
.rg-dialog-error {
  padding: 8px 12px; border-radius: 8px;
  background: rgba(220,38,38,0.08); color: #f87171;
  font-size: 12px; font-weight: 600;
}
.rg-dialog-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 20px; border-top: 1px solid var(--c-border);
}

/* 右键菜单 */
.rg-ctxmask {
  position: fixed; inset: 0; z-index: 9998;
}
.rg-ctxmenu {
  position: fixed; z-index: 9999;
  background: rgba(22,27,34,0.96); border: 1px solid rgba(255,255,255,0.12);
  border-radius: 10px; padding: 4px; min-width: 140px;
  box-shadow: 0 8px 28px rgba(0,0,0,0.55); backdrop-filter: blur(12px);
}
.rg-ctxitem {
  display: flex; align-items: center; gap: 8px;
  width: 100%; padding: 8px 12px; border: 0; border-radius: 7px;
  background: transparent; color: #c9d1d9; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: background 100ms, color 100ms;
}
.rg-ctxitem:hover { background: rgba(255,255,255,0.08); color: #fff; }
.rg-ctxitem-del:hover { background: rgba(220,38,38,0.18); color: #f87171; }

@media (max-width: 1100px) {
  .rg-main { grid-template-columns: 1fr; }
  .rg-inspector { position: static; max-height: none; }
}
</style>
