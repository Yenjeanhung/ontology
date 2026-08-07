<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  constraints: { type: Array, default: () => [] },
})

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
const layoutMsg = ref('')
let layoutMsgTimer = null

const isDragging = ref(false)
const isPanning = ref(false)
const dragNode = ref(null)
const dragOffset = ref({ x: 0, y: 0 })
const panStart = ref({ x: 0, y: 0 })
const panViewBoxStart = ref({ x: 0, y: 0 })

const nodeMap = computed(() => {
  const m = {}
  for (const n of nodes.value) m[n.id] = n
  return m
})

// 曲线参数：同一对节点的多条平行边扇形分开，避免重叠
const edgeRenderList = computed(() => {
  const map = nodeMap.value
  // 先按无序节点对分组，分配弯曲偏移
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
    // 垂直方向偏移控制弧度
    const cx = mx - uy * dist * (0.12 + spread)
    const cy = my + ux * dist * (0.12 + spread)
    // 标签放在二次贝塞尔曲线的真实中点（t=0.5），而非控制点，避免标签飘离线条
    const lx = (mx + cx) / 2
    const ly = (my + cy) / 2
    return { ...edge, sx: sxo, sy: syo, tx: txo, ty: tyo, mx, my, cx, cy, lx, ly }
  })
})

const selectedNodeEdges = computed(() => {
  if (!selectedNodeId.value) return []
  return edges.value.filter(e => e.source === selectedNodeId.value || e.target === selectedNodeId.value)
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
  // 解除所有手动固定，重新跑力学自动布局
  for (const n of nodes.value) { n.fixed = false; n.vx = 0; n.vy = 0 }
  startSimulation()
}

// ===== 最佳布局：环形排列 + 最小化连线交叉 =====
function nodeDeg(id) {
  const n = nodes.value.find(x => x.id === id)
  return n ? n.degree : 0
}

// 环形布局下，两条弦(边)交叉当且仅当端点在环上交错
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
  // 爬山：尝试所有两两交换，保留能减少交叉的交换，直到收敛
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

// 计算给定节点位置下的真实线段交叉数（用节点中心连成的直线段）
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

// 纯 JS 力学布局（不依赖 DOM/动画），用于多种子择优
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

// 模拟退火：在已有布局上随机扰动节点位置，进一步消除交叉（突破力学布局的局部最优）
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

  // 候选 A：环形 + 最小弦交叉（对稀疏图往往更好）
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

  // 候选 B：多种随机种子的力学布局（对稠密图往往更好），按真实交叉数择优
  for (let seed = 0; seed < 10; seed++) {
    const pos = forceCandidate(ids, adj, seed)
    const c = crossingsFromPositions(pos, E)
    if (c < best.c) best = { pos, c }
  }

  // 局部退火：在最优候选基础上继续消减交叉，突破力学局部最优（图过大时跳过，避免卡顿）
  if (nodes.value.length <= 26 && E.length <= 100) {
    const ann = annealPositions(best.pos, E, ids)
    if (ann.c < best.c) best = ann
  }

  // 应用最优位置
  for (const n of nodes.value) {
    const p = best.pos[n.id]
    if (p) { n.x = p.x; n.y = p.y; n.vx = 0; n.vy = 0; n.fixed = false }
  }
  viewBox.value = { ...defaultViewBox }
  zoomLevel.value = 1
  layoutMsg.value = `最佳布局：剩余 ${best.c} 处交叉`
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
  node.fixed = true   // 粘滞：拖动后固定，不再被力学拉回
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
function onNodeClick(node) { selectedNodeId.value = node.id }

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
      <span class="rg-chip">{{ Math.round(zoomLevel * 100) }}%</span>
      <span class="rg-hint">滚轮缩放 · 拖拽节点/画布 · 点击节点查看关联 · 拖动后球会固定在松手处</span>
      <span v-if="layoutMsg" class="rg-layout-msg">{{ layoutMsg }}</span>
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
            </defs>

            <rect class="rg-bg" x="0" y="0" :width="WORLD_W" :height="WORLD_H" fill="transparent" />

            <g class="rg-edges">
              <path
                v-for="edge in edgeRenderList"
                :key="edge.key"
                :d="`M ${edge.sx} ${edge.sy} Q ${edge.cx} ${edge.cy} ${edge.tx} ${edge.ty}`"
                class="rg-edge"
                :class="{ hl: selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId) }"
                fill="none"
                marker-end="url(#rgArrow)"
              />
            </g>

            <g class="rg-edge-labels">
              <text
                v-for="edge in edgeRenderList"
                :key="'l' + edge.key"
                :x="edge.lx" :y="edge.ly"
                class="rg-edge-label"
                :class="{ hl: selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId) }"
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
              最佳布局
            </button>
            <button class="rg-tool-btn" @click="relayout" title="解除手动固定，重新自动布局">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
              重新布局
            </button>
          </div>

          <div class="rg-zoom">
            <button class="rg-zoom-btn" @click="zoomIn" title="放大">+</button>
            <button class="rg-zoom-btn reset" @click="zoomReset" title="重置">{{ Math.round(zoomLevel * 100) }}%</button>
            <button class="rg-zoom-btn" @click="zoomOut" title="缩小">&minus;</button>
          </div>
        </div>
      </div>

      <aside class="rg-inspector">
        <div class="rg-inspector-title">节点详情</div>
        <template v-if="selectedNodeId && nodeMap[selectedNodeId]">
          <div class="rg-card">
            <div class="rg-inspector-name">
              <span class="rg-dot" :style="{ background: getNodeColor(nodeMap[selectedNodeId]) }"></span>
              {{ nodeMap[selectedNodeId].name }}
            </div>
            <div class="rg-inspector-sub">连接 {{ selectedNodeEdges.length }} 条关系</div>
          </div>
          <div class="rg-rels">
            <div v-for="e in selectedNodeEdges" :key="e.key" class="rg-rel-item">
              <span class="rg-rel-src" v-if="e.source === selectedNodeId">{{ nodeMap[selectedNodeId].name }}</span>
              <span class="rg-rel-src other" v-else>{{ (nodeMap[e.source] || {}).name || '?' }}</span>
              <span class="rg-rel-type">—{{ e.label }}→</span>
              <span class="rg-rel-tgt" v-if="e.target === selectedNodeId">{{ nodeMap[selectedNodeId].name }}</span>
              <span class="rg-rel-tgt other" v-else>{{ (nodeMap[e.target] || {}).name || '?' }}</span>
            </div>
            <div v-if="!selectedNodeEdges.length" class="rg-no-rels">无关联关系</div>
          </div>
        </template>
        <div v-else class="rg-inspector-empty">点击图谱中的本体节点，查看其参与的三元组关系。</div>
      </aside>
    </div>
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
.rg-hint { font-size: 12px; color: var(--c-secondary); }
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
.rg-layout-msg {
  margin-left: auto; padding: 4px 10px; border-radius: 999px;
  background: rgba(99,140,220,0.15); color: #8bb5f5;
  font-size: 12px; font-weight: 600;
}

.rg-main { display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, 320px); gap: 14px; align-items: start; }
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

.rg-edge { stroke: rgba(255,255,255,0.30); stroke-width: 1.6; transition: stroke 250ms, stroke-width 250ms; }
.rg-edge.hl { stroke: rgba(255,255,255,0.7); stroke-width: 2.8; }
.rg-edge-label {
  fill: rgba(255,255,255,0.55); font-size: 11px; text-anchor: middle; pointer-events: none;
  font-weight: 500; stroke: rgba(0,0,0,0.55); stroke-width: 2; paint-order: stroke;
}
.rg-edge-label.hl { fill: rgba(255,255,255,0.9); font-weight: 700; stroke: rgba(0,0,0,0.65); stroke-width: 3; }

.rg-node { cursor: pointer; }
.rg-glow { stroke-width: 1.5; opacity: 0; transition: opacity 250ms; }
.rg-glow.active { opacity: 0.25; }
.rg-circle { transition: r 200ms ease; }
.rg-label {
  fill: rgba(255,255,255,0.75); font-size: 12px; text-anchor: middle; pointer-events: none;
  font-weight: 500; text-shadow: 0 1px 4px rgba(0,0,0,0.7);
}
.rg-label.bold { fill: #fff; font-weight: 700; font-size: 13px; }

.rg-inspector {
  border: 1px solid var(--c-border); border-radius: 16px; padding: 14px;
  background: var(--c-panel); position: sticky; top: 16px;
  max-height: calc(100vh - 160px); overflow: auto;
}
.rg-inspector-title { font-size: 14px; font-weight: 700; margin-bottom: 10px; }
.rg-card { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.rg-inspector-name { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.rg-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
.rg-inspector-sub { color: var(--c-secondary); font-size: 12px; }
.rg-rels { display: flex; flex-direction: column; gap: 6px; }
.rg-rel-item {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 7px 9px; border-radius: 8px; background: var(--c-muted); font-size: 12px;
}
.rg-rel-src, .rg-rel-tgt { font-weight: 600; }
.rg-rel-src.other, .rg-rel-tgt.other { color: var(--c-secondary); font-weight: 500; }
.rg-rel-type { color: var(--c-accent); font-weight: 700; font-size: 11px; }
.rg-no-rels { color: var(--c-secondary); font-size: 12px; font-style: italic; }
.rg-inspector-empty { color: var(--c-secondary); font-size: 13px; line-height: 1.6; }

@media (max-width: 1100px) {
  .rg-main { grid-template-columns: 1fr; }
  .rg-inspector { position: static; max-height: none; }
}
</style>
