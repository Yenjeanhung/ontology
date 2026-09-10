<script setup>
/** 动作编排画布：拖拽函数/逻辑节点 → 连线 → 配置 → 导出 flow JSON。 */
import { ref, computed, watch, onMounted } from 'vue'
import { VueFlow, useVueFlow, Handle, Position } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import ConditionRuleBuilder from '../workflow/ConditionRuleBuilder.vue'

const props = defineProps({
  /** 编排图 {schema_version, nodes, edges, layout} */
  modelValue: { type: Object, default: () => ({}) },
  /** 可用函数（后端 /functions 列表） */
  functions: { type: Array, default: () => [] },
  /** 测试运行 trace：用于节点着色 */
  trace: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:modelValue'])

const NODE_META = {
  start: { name: '开始', color: '#64748b', icon: '▶' },
  function: { name: '函数', color: '#2563eb', icon: 'ƒ' },
  code: { name: '代码', color: '#7c3aed', icon: '{ }' },
  condition: { name: '条件', color: '#d97706', icon: '◇' },
  end: { name: '结束', color: '#16a34a', icon: '■' },
}
const ON_ERROR_OPTIONS = [
  { value: 'fail', label: '中断动作' },
  { value: 'continue', label: '忽略并继续' },
]

const { screenToFlowCoordinate } = useVueFlow()

const nodes = ref([])
const edges = ref([])
const selectedId = ref('')
const seq = ref(0)
const edgeSeq = ref(0)
const keyword = ref('')
const loading = ref(false)

const selectedNode = computed(() => nodes.value.find(n => n.id === selectedId.value) || null)
const selectedType = computed(() => selectedNode.value?.data?.nodeType || '')
const selectedFn = computed(() => {
  if (selectedType.value !== 'function') return null
  const fid = selectedNode.value?.data?.config?.function_id
  return props.functions.find(f => f.id === fid) || null
})

const filteredFunctions = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return props.functions
  return props.functions.filter(f =>
    (f.name || '').toLowerCase().includes(kw) || (f.code || '').toLowerCase().includes(kw))
})

const traceMap = computed(() => {
  const m = {}
  for (const t of props.trace || []) {
    if (!t?.node) continue
    m[t.node] = {
      status: t.skipped ? 'skipped' : (t.ok ? 'succeeded' : 'failed'),
      duration_ms: t.duration_ms,
      output: t.output,
      error: t.error,
    }
  }
  return m
})

/** 上游可引用变量（含实体与入参），供配置时参考 */
const upstreamVars = computed(() => {
  if (!selectedNode.value) return []
  const incoming = new Set()
  const stack = [selectedId.value]
  const seen = new Set()
  while (stack.length) {
    const cur = stack.pop()
    if (seen.has(cur)) continue
    seen.add(cur)
    for (const e of edges.value) {
      if (e.target === cur && !seen.has(e.source)) {
        incoming.add(e.source)
        stack.push(e.source)
      }
    }
  }
  const list = ['entity.properties.xxx', 'params.xxx']
  for (const id of incoming) {
    const n = nodes.value.find(x => x.id === id)
    if (!n || n.data.nodeType === 'start') continue
    list.push(`${id}.value`)
    if (n.data.nodeType === 'condition') list.push(`${id}.passed`)
  }
  return list
})

// ===== 序列化 / 反序列化 =====

function defaultTitle(type, fn) {
  if (type === 'function') return fn?.name || '函数'
  return NODE_META[type]?.name || type
}

function defaultConfig(type, fn) {
  if (type === 'function') {
    return { function_id: fn?.id || '', function_code: fn?.code || '', params: {}, on_error: 'fail' }
  }
  if (type === 'condition') {
    return { rule: { combinator: 'and', rules: [] }, on_false: 'continue', abort_message: '' }
  }
  if (type === 'code') {
    return { code_text: 'def run(params, entity, context):\n    return {}\n', params: {}, on_error: 'fail' }
  }
  if (type === 'end') return { mode: 'output', result: {}, edits: [], abort_message: '' }
  return {}
}

function loadFlow(flow) {
  loading.value = true
  const f = flow || {}
  const rawNodes = Array.isArray(f.nodes) ? f.nodes : []
  const layout = f.layout || {}
  nodes.value = rawNodes.map((n, i) => ({
    id: String(n.id),
    type: 'afNode',
    position: layout[n.id] || { x: 40 + i * 220, y: 140 },
    data: {
      nodeType: n.type || 'function',
      title: n.title || defaultTitle(n.type, null),
      config: { ...(n.config || {}) },
    },
  }))
  edges.value = (Array.isArray(f.edges) ? f.edges : []).map(e => ({
    id: `e${edgeSeq.value++}`,
    source: String(e.source),
    target: String(e.target),
    sourceHandle: e.source_handle || null,
    targetHandle: e.target_handle || null,
    animated: true,
    label: e.source_handle === 'true' ? '真' : (e.source_handle === 'false' ? '假' : ''),
  }))
  seq.value = nodes.value.reduce((m, n) => Math.max(m, parseInt(String(n.id).replace(/^n/, ''), 10) || 0), 0)
  if (!nodes.value.length) seed()
  selectedId.value = nodes.value[0]?.id || ''
  loading.value = false
}

function seed() {
  nodes.value = [
    { id: 'n1', type: 'afNode', position: { x: 40, y: 140 }, data: { nodeType: 'start', title: '开始', config: {} } },
    { id: 'n2', type: 'afNode', position: { x: 480, y: 140 }, data: { nodeType: 'end', title: '结束', config: defaultConfig('end') } },
  ]
  edges.value = [{ id: `e${edgeSeq.value++}`, source: 'n1', target: 'n2', animated: true }]
  seq.value = 2
}

function serialize() {
  return {
    schema_version: 1,
    nodes: nodes.value.map(n => ({
      id: n.id,
      type: n.data.nodeType,
      title: n.data.title || '',
      config: n.data.config || {},
    })),
    edges: edges.value.map(e => ({
      source: e.source,
      target: e.target,
      source_handle: e.sourceHandle || '',
    })),
    layout: Object.fromEntries(nodes.value.map(n => [n.id, {
      x: Math.round(n.position?.x || 0), y: Math.round(n.position?.y || 0),
    }])),
  }
}

watch(() => props.modelValue, v => {
  const next = JSON.stringify(v || {})
  if (next === JSON.stringify(serialize())) return
  loadFlow(v)
}, { deep: true })

watch([nodes, edges], () => {
  if (loading.value) return
  emit('update:modelValue', serialize())
}, { deep: true })

onMounted(() => loadFlow(props.modelValue))

// ===== 画布交互 =====

function nextId() {
  seq.value += 1
  let id = `n${seq.value}`
  while (nodes.value.some(n => n.id === id)) { seq.value += 1; id = `n${seq.value}` }
  return id
}

function onPaletteDragStart(ev, payload) {
  ev.dataTransfer.setData('application/x-af-node', JSON.stringify(payload))
  ev.dataTransfer.effectAllowed = 'move'
}

function onDragOver(ev) {
  ev.preventDefault()
  ev.dataTransfer.dropEffect = 'move'
}

function onDrop(ev) {
  ev.preventDefault()
  const raw = ev.dataTransfer.getData('application/x-af-node')
  if (!raw) return
  let payload
  try { payload = JSON.parse(raw) } catch { return }
  const pos = screenToFlowCoordinate({ x: ev.clientX, y: ev.clientY })
  addNode(payload.kind, payload.function_id, pos)
}

function addNode(kind, functionId, pos) {
  const fn = props.functions.find(f => f.id === functionId)
  const id = nextId()
  nodes.value.push({
    id,
    type: 'afNode',
    position: pos || { x: 120 + nodes.value.length * 40, y: 160 + nodes.value.length * 30 },
    data: { nodeType: kind, title: defaultTitle(kind, fn), config: defaultConfig(kind, fn) },
  })
  // 自动从当前选中节点连一条线，减少手工连线
  const from = nodes.value.find(n => n.id === selectedId.value)
  if (from && from.id !== id && from.data.nodeType !== 'end' && kind !== 'start') {
    connect(from.id, id, null)
  }
  selectedId.value = id
}

function wouldCycle(source, target) {
  const adj = {}
  edges.value.forEach(e => { (adj[e.source] = adj[e.source] || []).push(e.target) })
  const stack = [target]
  const seen = new Set()
  while (stack.length) {
    const cur = stack.pop()
    if (cur === source) return true
    if (seen.has(cur)) continue
    seen.add(cur)
    ;(adj[cur] || []).forEach(n => stack.push(n))
  }
  return false
}

function connect(source, target, sourceHandle) {
  if (source === target) return
  if (wouldCycle(source, target)) return
  if (edges.value.some(e => e.source === source && e.target === target
    && (e.sourceHandle || '') === (sourceHandle || ''))) return
  edges.value.push({
    id: `e${edgeSeq.value++}`,
    source, target,
    sourceHandle: sourceHandle || null,
    animated: true,
    label: sourceHandle === 'true' ? '真' : (sourceHandle === 'false' ? '假' : ''),
  })
}

function onConnect({ source, target, sourceHandle }) {
  connect(source, target, sourceHandle || null)
}

function onNodeClick({ node }) { selectedId.value = node.id }

function removeSelected() {
  if (!selectedNode.value || selectedNode.value.data.nodeType === 'start') return
  const id = selectedNode.value.id
  nodes.value = nodes.value.filter(n => n.id !== id)
  edges.value = edges.value.filter(e => e.source !== id && e.target !== id)
  selectedId.value = ''
}

// ===== 配置面板辅助 =====

function onFunctionChange() {
  const node = selectedNode.value
  const fn = selectedFn.value
  if (!node || !fn) return
  node.data.title = node.data.title === '函数' ? fn.name : node.data.title
  node.data.config.function_code = fn.code
  // 按新函数的参数定义重建入参（保留同名字段的值）
  const schema = Array.isArray(fn.params_schema) ? fn.params_schema : []
  const next = {}
  schema.forEach(p => { next[p.name] = node.data.config.params?.[p.name] ?? (p.default ?? '') })
  node.data.config.params = next
}

function fnParams(fn) {
  if (!fn) return []
  if (Array.isArray(fn.params_schema)) return fn.params_schema
  return []
}

function asText(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'string') return v
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

/** JSON 文本域编辑：解析失败时回滚输入框内容，避免写入非法配置 */
function onJsonChange(field, ev) {
  const node = selectedNode.value
  if (!node) return
  const el = ev.target
  try {
    node.data.config[field] = JSON.parse(el.value || '{}')
  } catch {
    el.value = asText(node.data.config[field])
  }
}

function varText(v) {
  return `{{ ${v} }}`
}

function nodeSubtitle(node) {
  const t = node.data.nodeType
  const cfg = node.data.config || {}
  if (t === 'function') {
    const fn = props.functions.find(f => f.id === cfg.function_id)
    return fn ? `${fn.name}（${fn.code}）` : '未选择函数'
  }
  if (t === 'condition') return cfg.on_false === 'abort' ? '不通过则中止' : '不通过则走假分支'
  if (t === 'code') return '沙箱 Python 片段'
  if (t === 'end') return cfg.mode === 'abort' ? '中止动作' : '输出结果 / 写回'
  return '入口'
}
</script>

<template>
  <div class="afc">
    <!-- 左：节点面板 -->
    <aside class="afc-palette">
      <div class="afc-pal-title">可用函数</div>
      <input v-model="keyword" class="afc-search" type="text" placeholder="搜索函数名称 / 编码">
      <div class="afc-pal-list">
        <div
          v-for="fn in filteredFunctions" :key="fn.id"
          class="afc-pal-item" :class="{ disabled: !fn.is_enabled }"
          :title="fn.description || fn.name"
          draggable="true" @dragstart="onPaletteDragStart($event, { kind: 'function', function_id: fn.id })"
          @click="addNode('function', fn.id, null)"
        >
          <span class="afc-pal-ico" :style="{ background: NODE_META.function.color }">ƒ</span>
          <span class="afc-pal-name">{{ fn.name }}</span>
          <span class="afc-pal-code">{{ fn.code }}</span>
        </div>
        <div v-if="!filteredFunctions.length" class="afc-pal-empty">暂无函数</div>
      </div>

      <div class="afc-pal-title">逻辑节点</div>
      <div class="afc-pal-list">
        <div
          v-for="t in ['condition', 'code', 'end']" :key="t"
          class="afc-pal-item" draggable="true"
          @dragstart="onPaletteDragStart($event, { kind: t })"
          @click="addNode(t, null, null)"
        >
          <span class="afc-pal-ico" :style="{ background: NODE_META[t].color }">{{ NODE_META[t].icon }}</span>
          <span class="afc-pal-name">{{ NODE_META[t].name }}</span>
        </div>
      </div>
      <div class="afc-pal-hint">
        拖拽到画布，或点击直接添加。连线后点击节点配置；变量用 <code v-pre>{{ 节点id.value }}</code> 引用上游输出。
      </div>
    </aside>

    <!-- 中：画布 -->
    <div class="afc-canvas" @drop="onDrop" @dragover="onDragOver">
      <VueFlow
        v-model:nodes="nodes"
        v-model:edges="edges"
        :min-zoom="0.3"
        :max-zoom="1.8"
        @connect="onConnect"
        @node-click="onNodeClick"
      >
        <template #node-afNode="np">
          <div
            class="afc-node"
            :class="[traceMap[np.id]?.status || '', { selected: selectedId === np.id }]"
            :style="{ '--nc': NODE_META[np.data.nodeType]?.color || '#64748b' }"
          >
            <Handle v-if="np.data.nodeType !== 'start'" type="target" :position="Position.Left" class="afc-handle" />
            <template v-if="np.data.nodeType === 'condition'">
              <Handle id="true" type="source" :position="Position.Right" class="afc-handle afc-handle-true" style="top: 34%" />
              <Handle id="false" type="source" :position="Position.Right" class="afc-handle afc-handle-false" style="top: 66%" />
            </template>
            <Handle v-else-if="np.data.nodeType !== 'end'" type="source" :position="Position.Right" class="afc-handle" />

            <div class="afc-node-head">
              <span class="afc-node-ico" :style="{ background: NODE_META[np.data.nodeType]?.color }">
                {{ NODE_META[np.data.nodeType]?.icon }}
              </span>
              <span class="afc-node-title">{{ np.data.title || np.id }}</span>
              <span v-if="traceMap[np.id]" class="afc-node-dur">{{ traceMap[np.id].duration_ms ?? 0 }}ms</span>
            </div>
            <div class="afc-node-body">{{ nodeSubtitle(np) }}</div>
            <div v-if="traceMap[np.id]?.error" class="afc-node-err" :title="traceMap[np.id].error">
              {{ traceMap[np.id].error }}
            </div>
          </div>
        </template>
        <Background :gap="18" />
        <Controls />
      </VueFlow>
    </div>

    <!-- 右：节点配置 -->
    <aside class="afc-inspector">
      <template v-if="!selectedNode">
        <div class="afc-ins-empty">点击画布中的节点进行配置</div>
      </template>
      <template v-else>
        <div class="afc-ins-head">
          <span class="afc-ins-type" :style="{ background: NODE_META[selectedType]?.color }">
            {{ NODE_META[selectedType]?.name }}
          </span>
          <span class="afc-ins-id">{{ selectedNode.id }}</span>
          <button v-if="selectedType !== 'start'" class="btn sm danger-ghost" @click="removeSelected">删除</button>
        </div>

        <div class="afc-ins-body">
          <div class="afc-field">
            <label>节点名称</label>
            <input type="text" v-model="selectedNode.data.title">
          </div>

          <!-- 函数节点 -->
          <template v-if="selectedType === 'function'">
            <div class="afc-field">
              <label>调用函数</label>
              <select v-model="selectedNode.data.config.function_id" @change="onFunctionChange">
                <option value="">— 请选择 —</option>
                <option v-for="fn in functions" :key="fn.id" :value="fn.id" :disabled="!fn.is_enabled">
                  {{ fn.name }}（{{ fn.code }}）
                </option>
              </select>
            </div>
            <div v-if="selectedFn?.description" class="afc-fn-desc">{{ selectedFn.description }}</div>
            <div v-if="fnParams(selectedFn).length" class="afc-sub">入参（可用 <code v-pre>{{ }}</code> 引用）</div>
            <div v-for="p in fnParams(selectedFn)" :key="p.name" class="afc-field">
              <label>{{ p.label || p.name }}<i v-if="p.required" class="req">*</i></label>
              <input type="text" v-model="selectedNode.data.config.params[p.name]" :placeholder="p.description || String(p.default ?? '')">
            </div>
            <div class="afc-field">
              <label>失败时</label>
              <select v-model="selectedNode.data.config.on_error">
                <option v-for="o in ON_ERROR_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
          </template>

          <!-- 条件节点 -->
          <template v-else-if="selectedType === 'condition'">
            <div class="afc-sub">条件规则</div>
            <ConditionRuleBuilder v-model="selectedNode.data.config.rule" :depth="1" :max-depth="3" />
            <div class="afc-field">
              <label>不通过时</label>
              <select v-model="selectedNode.data.config.on_false">
                <option value="continue">走「假」分支</option>
                <option value="abort">中止动作</option>
              </select>
            </div>
            <div v-if="selectedNode.data.config.on_false === 'abort'" class="afc-field">
              <label>中止提示</label>
              <input type="text" v-model="selectedNode.data.config.abort_message" placeholder="如：过站裕度不足，禁止放行">
            </div>
          </template>

          <!-- 代码节点 -->
          <template v-else-if="selectedType === 'code'">
            <div class="afc-field">
              <label>Python 代码（定义 run）</label>
              <textarea v-model="selectedNode.data.config.code_text" rows="10" spellcheck="false" class="afc-code"></textarea>
            </div>
            <div class="afc-field">
              <label>失败时</label>
              <select v-model="selectedNode.data.config.on_error">
                <option v-for="o in ON_ERROR_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
          </template>

          <!-- 结束节点 -->
          <template v-else-if="selectedType === 'end'">
            <div class="afc-field">
              <label>结束方式</label>
              <select v-model="selectedNode.data.config.mode">
                <option value="output">输出结果（可带写回）</option>
                <option value="abort">中止动作</option>
              </select>
            </div>
            <template v-if="selectedNode.data.config.mode === 'abort'">
              <div class="afc-field">
                <label>中止提示</label>
                <input type="text" v-model="selectedNode.data.config.abort_message">
              </div>
            </template>
            <template v-else>
              <div class="afc-field">
                <label>返回数据 JSON</label>
                <textarea rows="4" spellcheck="false" class="afc-code"
                  :value="asText(selectedNode.data.config.result)"
                  @change="onJsonChange('result', $event)"
                  placeholder='{"delay_min": "{{ f2.value }}"}'></textarea>
              </div>
              <div class="afc-field">
                <label>写回 edits JSON（数组）</label>
                <textarea rows="5" spellcheck="false" class="afc-code"
                  :value="asText(selectedNode.data.config.edits)"
                  @change="onJsonChange('edits', $event)"
                  placeholder='[{"op":"set_property","property_code":"leg_status","value":"released"}]'></textarea>
              </div>
            </template>
          </template>

          <div class="afc-sub">可引用变量</div>
          <div class="afc-vars">
            <code v-for="v in upstreamVars" :key="v">{{ varText(v) }}</code>
          </div>
        </div>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.afc { display: flex; gap: 10px; height: 460px; border: 1px solid var(--c-border); border-radius: var(--radius, 8px); overflow: hidden; }

/* 左侧面板 */
.afc-palette { width: 190px; flex-shrink: 0; border-right: 1px solid var(--c-border); padding: 8px; overflow-y: auto; background: var(--c-panel); }
.afc-pal-title { font-size: 11px; font-weight: 700; color: var(--c-secondary); margin: 4px 0 6px; }
.afc-search { width: 100%; padding: 4px 6px; font-size: 11.5px; border: 1px solid var(--c-border); border-radius: 5px; background: var(--c-bg); color: var(--c-fg); margin-bottom: 6px; }
.afc-pal-list { display: flex; flex-direction: column; gap: 4px; }
.afc-pal-item { display: flex; align-items: center; gap: 6px; padding: 5px 6px; border: 1px solid var(--c-border); border-radius: 6px; cursor: grab; background: var(--c-bg-soft, var(--c-panel)); }
.afc-pal-item:hover { border-color: var(--c-accent); }
.afc-pal-item.disabled { opacity: .45; cursor: not-allowed; }
.afc-pal-ico { width: 18px; height: 18px; border-radius: 5px; color: #fff; font-size: 10px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.afc-pal-name { font-size: 11.5px; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.afc-pal-code { margin-left: auto; font-size: 9.5px; color: var(--c-secondary); font-family: ui-monospace, monospace; }
.afc-pal-empty { font-size: 11px; color: var(--c-secondary); padding: 4px; }
.afc-pal-hint { margin-top: 10px; font-size: 10.5px; line-height: 1.5; color: var(--c-secondary); }

/* 画布 */
.afc-canvas { flex: 1; min-width: 0; position: relative; background: var(--c-bg-soft, rgba(0,0,0,.02)); }
.afc-node { width: 168px; background: var(--c-panel); border: 1px solid var(--c-border-strong, var(--c-border)); border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.06); font-family: var(--font); }
.afc-node.selected { border-color: var(--c-accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c-accent) 22%, transparent); }
.afc-node.succeeded { border-color: var(--c-success); }
.afc-node.failed { border-color: var(--c-danger); }
.afc-node.skipped { opacity: .45; }
.afc-node-head { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-bottom: 1px solid var(--c-border); }
.afc-node-ico { width: 17px; height: 17px; border-radius: 5px; color: #fff; font-size: 9.5px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.afc-node-title { font-size: 11.5px; font-weight: 700; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.afc-node-dur { font-size: 9.5px; color: var(--c-secondary); }
.afc-node-body { padding: 5px 8px; font-size: 10.5px; color: var(--c-secondary); }
.afc-node-err { padding: 4px 8px; font-size: 9.5px; color: var(--c-danger); border-top: 1px dashed var(--c-border); max-height: 34px; overflow: hidden; }
.afc-handle { width: 10px; height: 10px; border: 2px solid var(--c-border-strong, var(--c-border)); background: var(--c-panel); }
.afc-handle-true { border-color: var(--c-success) !important; }
.afc-handle-false { border-color: var(--c-danger) !important; }

/* 右侧配置 */
.afc-inspector { width: 250px; flex-shrink: 0; border-left: 1px solid var(--c-border); background: var(--c-panel); overflow-y: auto; }
.afc-ins-empty { padding: 24px 12px; text-align: center; font-size: 11.5px; color: var(--c-secondary); }
.afc-ins-head { display: flex; align-items: center; gap: 6px; padding: 8px; border-bottom: 1px solid var(--c-border); position: sticky; top: 0; background: var(--c-panel); z-index: 2; }
.afc-ins-type { padding: 1px 7px; border-radius: 999px; color: #fff; font-size: 10px; font-weight: 700; }
.afc-ins-id { font-size: 10.5px; color: var(--c-secondary); font-family: ui-monospace, monospace; }
.afc-ins-body { padding: 8px; display: flex; flex-direction: column; gap: 8px; }
.afc-field { display: flex; flex-direction: column; gap: 3px; }
.afc-field label { font-size: 10.5px; color: var(--c-secondary); }
.afc-field input, .afc-field select, .afc-field textarea { font-size: 11.5px; padding: 4px 6px; border: 1px solid var(--c-border); border-radius: 5px; background: var(--c-bg); color: var(--c-fg); width: 100%; box-sizing: border-box; }
.afc-code { font-family: ui-monospace, monospace; font-size: 10.5px; line-height: 1.5; resize: vertical; }
.afc-sub { font-size: 10.5px; font-weight: 700; color: var(--c-secondary); margin-top: 2px; }
.afc-fn-desc { font-size: 10.5px; line-height: 1.5; color: var(--c-secondary); background: var(--c-bg-soft, rgba(0,0,0,.03)); padding: 5px 6px; border-radius: 5px; }
.afc-vars { display: flex; flex-wrap: wrap; gap: 4px; }
.afc-vars code { font-size: 10px; padding: 1px 5px; border-radius: 4px; background: color-mix(in srgb, var(--c-accent) 10%, transparent); color: var(--c-accent); }
.danger-ghost { color: var(--c-danger); background: transparent; border: 1px solid color-mix(in srgb, var(--c-danger) 30%, transparent); }
.req { color: var(--c-danger); font-style: normal; margin-left: 2px; }
</style>
