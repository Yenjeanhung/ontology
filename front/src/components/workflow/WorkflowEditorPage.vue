<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, reactive } from 'vue'
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
const nodeTypes = Object.fromEntries(Object.keys(TYPE_META).map(t => [t, WorkflowNode]))
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
    edges.value = (def.edges || []).map(toFlowEdge)
    nodeSeq = nodes.value.length
    edgeSeq = edges.value.length
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
  const id = `n${++nodeSeq}`
  const pos = position || { x: 100 + (nodeSeq % 4) * 220, y: 80 + (nodeSeq % 3) * 160 }
  nodes.value.push({
    id,
    type,
    position: pos,
    data: {
      nodeType: type,
      title: TYPE_META[type].name,
      config: JSON.parse(JSON.stringify(DEFAULT_CONFIG[type])),
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
  edges.value.push({
    id: `e${++edgeSeq}`,
    source: conn.source,
    target: conn.target,
    sourceHandle: conn.sourceHandle || undefined,
  })
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
const startInputs = computed(() => {
  const s = nodes.value.find(n => n.type === 'start')
  return s?.data?.config?.inputs || []
})

function openRunModal() {
  Object.keys(runInputs).forEach(k => delete runInputs[k])
  for (const it of startInputs.value) runInputs[it.name] = it.default ?? ''
  runModal.value = true
}

function setStatus(nodeId, status) {
  const n = nodes.value.find(x => x.id === nodeId)
  if (n) n.data.status = status
}
function clearStatus() {
  nodes.value.forEach(n => { n.data.status = '' })
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
      onNodeFinished(d) {
        setStatus(d.node_id, 'succeeded')
        logs.value.push({ kind: 'node', node_id: d.node_id, title: d.title, status: 'succeeded', summary: d.summary, output: d.output, duration_ms: d.duration_ms })
      },
      onNodeFailed(d) {
        setStatus(d.node_id, 'failed')
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

// 切换节点时同步实体服务下拉
watch(selectedNodeId, (id) => {
  if (id) {
    const n = nodes.value.find(x => x.id === id)
    if (n?.type === 'service') loadSvc(n.data.config)
  }
})

// 右键菜单：点击空白处关闭
onMounted(() => window.addEventListener('click', closeContextMenu))
onBeforeUnmount(() => window.removeEventListener('click', closeContextMenu))
</script>

<template>
  <div class="wf-editor">
    <!-- 工具栏 -->
    <div class="wf-toolbar">
      <button class="btn" @click="router.push('/workflows')">← 返回</button>
      <input class="wf-name" v-model="wfName" placeholder="工作流名称">
      <span v-if="saved" class="wf-saved">已保存</span>
      <div class="spacer"></div>
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
      <aside class="wf-drawer">
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
                <label>输出映射（JSON）</label>
                <textarea :value="jsonText('outputs')" @input="setJson('outputs', $event.target.value)" rows="6" placeholder='[{"name":"answer","value":"{{agent.answer}}"}]'></textarea>
              </div>
            </template>

            <!-- 智能体 -->
            <template v-else-if="selectedType === 'agent'">
              <div class="field">
                <label>绑定方式</label>
                <select v-model="selectedConfig.agent_id">
                  <option value="">内联配置（现场选 KB + 技能）</option>
                  <option v-for="a in palette.agents" :key="a.id" :value="a.id">{{ a.name }} · {{ a.kb_name || a.kb_id }}</option>
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
              <div class="field">
                <label>问题模板</label>
                <textarea v-model="selectedConfig.query_template" rows="3"></textarea>
                <span class="var-btn" @click="insertVar('query_template')">⊕ 插入变量</span>
              </div>
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
              <div class="field">
                <label>参数（JSON，可用变量）</label>
                <textarea :value="jsonText('params')" @input="setJson('params', $event.target.value)" rows="3" placeholder='{"x":"{{agent.answer}}"}'></textarea>
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
            <span class="log-dur" v-if="l.duration_ms != null">{{ l.duration_ms }}ms</span>
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
.log-node.st-running .log-status { color: var(--c-accent); }
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
.wf-drawer { width: 300px; flex-shrink: 0; display: flex; flex-direction: column; border: 1px solid var(--c-border); border-radius: 12px; background: var(--c-panel); overflow: hidden; }
.dr-head { display: flex; align-items: center; gap: 9px; padding: 12px 14px; border-bottom: 1px solid var(--c-border); }
.dr-ico { width: 24px; height: 24px; border-radius: 6px; display: flex; align-items: center; justify-content: center; color: #fff; font-size: 13px; flex-shrink: 0; }
.dr-head-t { flex: 1; min-width: 0; }
.dr-title { font-size: 13px; font-weight: 700; color: var(--c-fg); }
.dr-sub { font-size: 10.5px; color: var(--c-secondary); }
.dr-body { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 12px; }
.dr-empty { flex: 1; display: flex; align-items: center; justify-content: center; text-align: center; color: var(--c-secondary); font-size: 13px; line-height: 1.6; }

.field { display: flex; flex-direction: column; gap: 5px; }
.field label { font-size: 11.5px; font-weight: 600; color: var(--c-secondary); }
.field input, .field textarea, .field select { padding: 7px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px); font-size: 12.5px; font-family: var(--font); outline: none; background: var(--c-bg); color: var(--c-fg); }
.field input:focus, .field textarea:focus, .field select:focus { border-color: var(--c-accent); }
.field textarea { resize: vertical; min-height: 56px; line-height: 1.5; }
.req { color: var(--c-danger); }
.hint { font-size: 11px; color: var(--c-secondary); line-height: 1.5; }
.var-btn { display: inline-flex; align-items: center; gap: 3px; margin-top: 5px; font-size: 11px; font-weight: 600; color: var(--c-accent); cursor: pointer; padding: 2px 8px; border: 1px dashed var(--c-accent); border-radius: 4px; width: fit-content; }
.var-btn:hover { background: var(--c-accent-weak, rgba(161,98,7,.10)); }

.skill-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.skill-chip { padding: 4px 10px; border-radius: 20px; font-size: 12px; border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; }
.skill-chip.active { background: var(--c-muted); border-color: var(--c-accent); color: var(--c-accent); }

/* 弹窗 */
.modal-mask { position: fixed; inset: 0; background: var(--c-overlay, rgba(0,0,0,.35)); z-index: 100; display: flex; align-items: center; justify-content: center; }
.modal { width: 380px; background: var(--c-panel); border-radius: 12px; padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; box-shadow: 0 12px 40px rgba(0,0,0,.25); }
.modal h3 { font-size: 14px; font-weight: 700; }
.m-sub { font-size: 12px; color: var(--c-secondary); }
.m-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
