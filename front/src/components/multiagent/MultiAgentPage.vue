<script setup>
/**
 * 多智能体协作（通用智能体团队，业务无关、任务类型无关）。
 *
 * 页面定位：通用智能体团队——核心智能体内置（Planner / Synthesizer），
 * 能力智能体自由勾选组队（Retriever / GraphAgent / Critic），任务类型不限
 * （研判 / 写作 / 总结 / 问答皆可）。业务场景以适配器在后端接入，本页面
 * 只面向通用契约渲染：团队编制（team）→ 协作时间线（team/plan/node_*）
 * → 素材卡（evidence/fact）→ 评审质控（conflict）→ 流式成果（token/done）。
 * 事件契约见 doc/智能体/多智能体场景.md §4。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { marked } from 'marked'
import {
  createMultiTask,
  deleteMultiTask,
  listMultiScenarios,
  listMultiTasks,
  streamTaskRun,
  updateMultiTask,
} from '../../api/multiAgent'

// ── 场景、任务库与编制 ──
const scenario = ref(null)         // 通用场景（唯一默认场景）
const tasks = ref([])              // 任务库：可配置/可编辑的任务提示词模板
const selectedTaskId = ref('')     // 当前选中任务（运行 = 任务提示词模板 + 用户问题）
const loadError = ref('')
const taskInput = ref('')          // 用户具体问题/主题输入
const taskForm = ref({ show: false, id: '', name: '', prompt: '' })  // 任务编辑表单
const savingTask = ref(false)

// 智能体名册：核心内置 + 能力可选（后端下发，本地兜底）
const FALLBACK_ROSTER = {
  core: [
    { id: 'planner', name: 'Planner · 任务规划', desc: '用 LLM 把任务分解为可并行子任务' },
    { id: 'synthesizer', name: 'Synthesizer · 结果合成', desc: '汇总共享黑板素材，流式交付最终成果' },
  ],
  optional: [
    { id: 'retriever', name: 'Retriever · 知识库取证', desc: '为每个子任务检索平台全部知识库语料（RAG 增强）' },
    { id: 'data_agent', name: 'DataAgent · 数据查询', desc: '查询实体台账结构化数据：聚合统计 + 最新明细（真实数据）' },
    { id: 'graph_agent', name: 'GraphAgent · 图谱事实', desc: '实体图谱关键词检索，产出结构化事实卡' },
    { id: 'tool_agent', name: 'ToolAgent · 工具调用', desc: 'Function Calling 自主取证：多轮调用内置工具与 MCP 外部工具' },
    { id: 'critic', name: 'Critic · 评审质控', desc: '素材交叉验证、冲突消解与质量裁定' },
  ],
  default: ['retriever', 'data_agent', 'graph_agent', 'critic'],
}
const roster = computed(() => scenario.value?.agents || FALLBACK_ROSTER)
const selectedAgents = ref([...FALLBACK_ROSTER.default])   // 已勾选的能力智能体

const coreAgents = computed(() => roster.value.core || [])
const optionalAgents = computed(() => roster.value.optional || [])

const pipelineHint = computed(() => {
  const useRetriever = selectedAgents.value.includes('retriever')
  const steps = ['Planner 分解', useRetriever ? 'Retriever×N 并行检索' : 'Worker×N 并行执行（模型知识）']
  if (selectedAgents.value.includes('graph_agent')) steps.push('GraphAgent 图谱事实')
  if (selectedAgents.value.includes('tool_agent')) steps.push('ToolAgent 工具调用（Function Calling）')
  if (selectedAgents.value.includes('critic')) steps.push('Critic 评审质控')
  steps.push('Synthesizer 流式合成')
  return steps.join(' → ')
})

function toggleAgent(id) {
  if (runState.reviewing) return
  const i = selectedAgents.value.indexOf(id)
  if (i >= 0) selectedAgents.value = selectedAgents.value.filter((a) => a !== id)
  else selectedAgents.value = [...selectedAgents.value, id]
}

// ── 运行状态 ──
const runState = ref({ reviewing: false })
let abortCtrl = null

const current = ref(null)          // 当前任务 {title, headline}
const teamName = ref('')
const members = ref([])            // [{node, role, name}]
const plan = ref([])
const nodes = ref({})              // node → {status:'running'|'done', summary}
const nodeOrder = ref([])
const evidenceDomains = ref([])    // [{domain, cards:[...]}]
const conflicts = ref([])
const verdict = ref(null)
const conclusionMd = ref('')
const errorMsg = ref('')
const elapsed = ref(0)
const reviewing = computed(() => runState.value.reviewing)

const NODE_FALLBACK_NAMES = {
  planner: 'Planner · 任务规划',
  graph_agent: 'GraphAgent · 图谱事实',
  critic: 'Critic · 评审质控',
  synthesizer: 'Synthesizer · 结果合成',
}

const evidenceCards = computed(() => evidenceDomains.value.flatMap((d) => d.cards))

// ── 素材分类（tab） / 折叠 / 引用定位 ──
// 后端 _synth_user 的编号约定：[素材N]=证据卡拍平序（与 evidenceCards 同序），
// [事实N]=事实卡序（facts 数组序，前端即 stance==='fact' 的卡片序）。
// 引用写法三种：单张 [事实5] / 连续范围 [事实2-9] / 离散列表 [事实2、5]。
const evTab = ref('all')
const evCollapsed = ref(false)
const KIND_META = {
  doc: { label: '知识库文档' },
  model: { label: '模型产出' },
  graph: { label: '图谱事实' },
  data: { label: '数据查询' },
  tool: { label: '工具产出' },
}

function cardKind(c) {
  if (c.grade === 'graph_fact') return 'graph'
  if (c.grade === 'data_fact') return 'data'
  if (c.grade === 'tool_result') return 'tool'
  if (c.grade === 'model_output' || c.grade === 'model_knowledge') return 'model'
  return 'doc'
}

const taggedCards = computed(() => evidenceCards.value.map((c, i) => ({ ...c, _i: i, kind: cardKind(c) })))
const factCount = computed(() => taggedCards.value.filter((c) => c.stance === 'fact').length)

const evTabs = computed(() => {
  const counts = {}
  for (const c of taggedCards.value) counts[c.kind] = (counts[c.kind] || 0) + 1
  const tabs = [{ key: 'all', label: '全部', count: taggedCards.value.length }]
  for (const k of ['doc', 'model', 'graph', 'data', 'tool']) {
    if (counts[k]) tabs.push({ key: k, label: KIND_META[k].label, count: counts[k] })
  }
  return tabs
})

const filteredEvCards = computed(() =>
  evTab.value === 'all' ? taggedCards.value : taggedCards.value.filter((c) => c.kind === evTab.value))

let flashTimer = null

function flashCard(el) {
  if (!el) return
  el.classList.remove('is-flash')
  void el.offsetWidth          // 强制重排，重启动画
  el.classList.add('is-flash')
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => el.classList.remove('is-flash'), 1600)
}

/** 成果里的引用 chip 点击 → 滚动定位到素材面板对应卡片并高亮。 */
function jumpCite(evt) {
  const chip = evt.target.closest('.ma-cite')
  if (!chip || chip.classList.contains('is-missing')) return
  const n = Number(chip.dataset.n)
  const card = chip.dataset.kind === 'fact'
    ? taggedCards.value.filter((c) => c.stance === 'fact')[n - 1] || null
    : taggedCards.value[n - 1] || null
  if (!card) return
  if (evTab.value !== 'all' && evTab.value !== card.kind) evTab.value = card.kind
  if (evCollapsed.value) evCollapsed.value = false
  requestAnimationFrame(() => {
    const el = document.getElementById(`ma-card-${card._i}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      flashCard(el)
    }
  })
}

function memberName(node) {
  const m = members.value.find((x) => x.node === node)
  if (m?.name) return m.name
  if (NODE_FALLBACK_NAMES[node]) return NODE_FALLBACK_NAMES[node]
  if (node.startsWith('retriever')) return 'Retriever · 知识库取证'
  if (node.startsWith('worker')) return 'Worker · 子任务执行'
  return node
}

function resetRunState() {
  teamName.value = ''
  members.value = []
  plan.value = []
  nodes.value = {}
  nodeOrder.value = []
  evidenceDomains.value = []
  conflicts.value = []
  verdict.value = null
  conclusionMd.value = ''
  errorMsg.value = ''
  elapsed.value = 0
}

function touchNode(node) {
  if (!nodes.value[node]) {
    nodes.value[node] = { status: 'running', summary: '' }
    nodeOrder.value = [...nodeOrder.value, node]
  }
}

function handleEvent(evt) {
  switch (evt.type) {
    case 'team':
      teamName.value = evt.team || ''
      members.value = evt.members || []
      break
    case 'plan':
      plan.value = evt.plan || []
      break
    case 'node_start':
      touchNode(evt.node)
      break
    case 'node_done':
      touchNode(evt.node)
      nodes.value[evt.node] = { status: 'done', summary: evt.summary || '' }
      break
    case 'evidence': {
      const hit = evidenceDomains.value.find((d) => d.domain === evt.domain)
      if (hit) hit.cards = evt.cards || []
      else evidenceDomains.value = [...evidenceDomains.value, { domain: evt.domain, cards: evt.cards || [] }]
      break
    }
    case 'fact':
      // 图谱/结构化事实卡以独立卡片组展示（grade=graph_fact，优先采信）
      evidenceDomains.value = [...evidenceDomains.value, { domain: '__facts__', cards: (evt.facts || []).map((f) => ({
        id: f.id,
        domain: f.grade === 'data_fact' ? 'data' : f.grade === 'tool_result' ? 'tool' : 'graph',
        grade: f.grade,
        source: f.grade === 'data_fact' ? '实体台账 · 结构化查询'
          : f.grade === 'tool_result' ? '工具链 · Function Calling' : '本体图谱 · 结构化事实',
        title: f.title, summary: f.detail, quote: '', stance: 'fact',
      })) }]
      break
    case 'conflict':
      conflicts.value = evt.conflicts || []
      verdict.value = {
        suggest_label: evt.suggest_label,
        confidence: evt.confidence,
        need_human: evt.need_human,
        comment: evt.comment,
      }
      break
    case 'token':
      conclusionMd.value += evt.content || ''
      break
    case 'done':
      if (evt.conclusion && !conclusionMd.value) conclusionMd.value = evt.conclusion
      elapsed.value = evt.elapsed_ms || 0
      break
    case 'error':
      errorMsg.value = evt.content || ''
      break
    default:
      break
  }
}

/** 发起团队协作：任务 + 当前编制。 */
async function runTask(taskObj) {
  if (reviewing.value) return
  abortCtrl?.abort()
  abortCtrl = new AbortController()
  current.value = taskObj
  resetRunState()
  runState.value.reviewing = true
  try {
    await streamTaskRun(scenario.value?.id || 'universal', taskObj.task, [...selectedAgents.value], {
      onEvent: handleEvent,
      signal: abortCtrl.signal,
    })
  } catch (err) {
    if (err?.name !== 'AbortError') errorMsg.value = err?.message || '协作请求失败'
  } finally {
    runState.value.reviewing = false
  }
}

const selectedTask = computed(() => tasks.value.find((t) => t.id === selectedTaskId.value) || null)

/** 组合最终任务文本：选中任务 → 任务提示词模板（{question} 替换为用户问题）；未选 → 纯自由输入。 */
function composeTask() {
  const q = taskInput.value.trim()
  const t = selectedTask.value
  if (!t) return q
  if (!q) return t.prompt
  if ((t.prompt || '').includes('{question}')) return t.prompt.replaceAll('{question}', q)
  return `${t.prompt}\n\n${q}`
}

/** 选中/取消任务：应用其默认编制（可再手动调整），再输入具体问题运行。 */
function applyTask(t) {
  if (reviewing.value) return
  selectedTaskId.value = selectedTaskId.value === t.id ? '' : t.id
  syncAgentsFromTask(t)
}

function syncAgentsFromTask(t) {
  if (selectedTaskId.value !== t.id) return
  const ids = optionalAgents.value.map((a) => a.id)
  if (Array.isArray(t.agents) && t.agents.length) {
    selectedAgents.value = ids.filter((id) => t.agents.includes(id))
  }
}

/** 快捷运行：该行 ▶ 直接按「任务提示词（+ 已输入的问题）」起团队。 */
function quickRun(t) {
  if (reviewing.value) return
  selectedTaskId.value = t.id
  syncAgentsFromTask(t)
  const q = taskInput.value.trim()
  const task = q ? composeTask() : t.prompt
  runTask({ id: t.id, title: t.name, headline: q || t.name, task })
}

/** 运行：选中任务 → 任务提示词模板 + 输入框问题；未选 → 自由任务。 */
function startTask() {
  const task = composeTask()
  if (!task) return
  const t = selectedTask.value
  runTask({
    id: t ? t.id : '__adhoc__',
    title: t ? t.name : '自由任务',
    headline: taskInput.value.trim() || (t ? t.name : '自由任务'),
    task,
  })
}

// ── 任务库 CRUD：新建 / 编辑提示词 / 删除 ──
function openNewTask() {
  taskForm.value = { show: true, id: '', name: '', prompt: '' }
}

function openEditTask(t) {
  taskForm.value = { show: true, id: t.id, name: t.name, prompt: t.prompt }
}

async function saveTask() {
  const f = taskForm.value
  const name = f.name.trim()
  const prompt = f.prompt.trim()
  if (!name || !prompt || savingTask.value) return
  savingTask.value = true
  try {
    if (f.id) {
      const updated = await updateMultiTask(f.id, { name, prompt, agents: [...selectedAgents.value] })
      tasks.value = tasks.value.map((t) => (t.id === f.id ? updated : t))
    } else {
      const created = await createMultiTask({ name, prompt, agents: [...selectedAgents.value] })
      tasks.value = [...tasks.value, created]
      selectedTaskId.value = created.id
    }
    taskForm.value = { show: false, id: '', name: '', prompt: '' }
  } catch (err) {
    loadError.value = err?.message || '保存任务失败'
  } finally {
    savingTask.value = false
  }
}

async function removeTask(t) {
  if (reviewing.value) return
  try {
    await deleteMultiTask(t.id)
    tasks.value = tasks.value.filter((x) => x.id !== t.id)
    if (selectedTaskId.value === t.id) selectedTaskId.value = ''
    if (current.value?.id === t.id) current.value = null
  } catch (err) {
    loadError.value = err?.message || '删除任务失败'
  }
}

function rerun() {
  if (!current.value) return
  if (current.value.id === '__adhoc__' && taskInput.value.trim()) {
    current.value = { ...current.value, task: taskInput.value.trim(), headline: taskInput.value.trim() }
  }
  runTask(current.value)
}

function gradeLabel(grade) {
  if (grade === 'graph_fact') return '图谱事实'
  if (grade === 'data_fact') return '数据查询'
  if (grade === 'tool_result') return '工具产出'
  if (grade === 'model_output') return '模型生成'
  if (grade === 'model_knowledge') return '模拟生成'
  if (grade === 'duty_record') return '值班记录'
  return '有出处文档'
}

/** 解析引用编号串："5" / "2-9" / "2~9" / "2、5" / "2, 5·7" → 去重升序数组。 */
function parseCiteNums(s) {
  const nums = new Set()
  for (const part of String(s).split(/[、,，·\s]+/)) {
    const m = part.match(/^(\d+)(?:\s*[-~—至]\s*(\d+))?$/)
    if (!m) continue
    const a = Number(m[1])
    const b = m[2] === undefined ? a : Number(m[2])
    const lo = Math.min(a, b)
    const hi = Math.max(a, b)
    if (hi - lo <= 50) for (let i = lo; i <= hi; i++) nums.add(i)  // 防呆：范围最多展开 50 个
  }
  return [...nums].sort((x, y) => x - y)
}

/** 编号数组 → 紧凑标签：连续段合并（2-9），离散段用 · 连接（2-4·7）。 */
function fmtCiteNums(nums) {
  if (!nums.length) return ''
  const runs = []
  let s = nums[0]
  let e = nums[0]
  for (let i = 1; i < nums.length; i++) {
    if (nums[i] === e + 1) { e = nums[i]; continue }
    runs.push(s === e ? `${s}` : `${s}-${e}`)
    s = e = nums[i]
  }
  runs.push(s === e ? `${s}` : `${s}-${e}`)
  return runs.join('·')
}

function renderMd(text) {
  try {
    let html = marked.parse(text || '', { breaks: true })
    const nMat = taggedCards.value.length
    const nFact = factCount.value
    // 引用 chip：单张 [素材3]/[事实5]、连续范围 [事实2-9]、离散列表 [事实2、5]；
    // 范围渲染为一枚组 chip，点击定位到第一张有效卡，title 展示全部编号。
    html = html.replace(/\[(素材|事实)([0-9、,，·\-~—至\s]+)\]/g, (_, kind, inner) => {
      const total = kind === '事实' ? nFact : nMat
      const ok = parseCiteNums(inner).filter((n) => n >= 1 && n <= total)
      const label = ok.length ? `${kind}${fmtCiteNums(ok)}` : `${kind}${String(inner).trim()}`
      const title = ok.length ? `点击定位${kind}卡：${ok.join('、')}` : '引用编号不存在'
      return `<sup class="ma-cite${ok.length ? '' : ' is-missing'}" data-kind="${kind === '事实' ? 'fact' : 'mat'}" data-n="${ok[0] || ''}" title="${title}">${label}</sup>`
    })
    return html
  } catch {
    return text
  }
}

onMounted(async () => {
  try {
    const scenarios = await listMultiScenarios()
    scenario.value = scenarios.find((s) => s.adhoc) || scenarios[0] || null
    if (!scenario.value) return
    selectedAgents.value = [...(scenario.value.agents?.default || FALLBACK_ROSTER.default)]
    tasks.value = await listMultiTasks()
  } catch (err) {
    loadError.value = err?.message || '加载智能体团队失败'
  }
})

onBeforeUnmount(() => {
  abortCtrl?.abort()
  clearTimeout(flashTimer)
})
</script>

<template>
  <div class="ma-page">
    <header class="ma-head">
      <div>
        <h1>多智能体协作</h1>
        <p class="ma-sub">
          通用智能体团队：核心智能体内置（Planner / Synthesizer），能力智能体自由组队，任务类型不限——研判、写作、总结、问答皆可
        </p>
      </div>
      <p class="ma-claim">团队即服务：动态规划 + 并行执行 + 可选质控，全程留痕</p>
    </header>

    <div class="ma-layout">
      <!-- ── 左：任务工作台 + 组队 ── -->
      <aside class="ma-workbench">
        <h2 class="ma-col-title">任务工作台 <span class="muted">{{ scenario?.business || '任意任务' }}</span></h2>
        <p v-if="scenario" class="ma-scenario-desc muted">{{ scenario.description }}</p>
        <p v-if="loadError" class="ma-error">{{ loadError }}</p>

        <!-- 智能体组队：核心内置 + 能力自由勾选 -->
        <div class="ma-agents">
          <p class="ma-agents-title muted">团队编制 <span>核心内置 · 能力自由勾选</span></p>
          <div class="ma-agent-row">
            <span
              v-for="a in coreAgents"
              :key="a.id"
              class="ma-agent-chip is-core"
              :title="a.desc"
            >{{ a.name }}<i>内置</i></span>
          </div>
          <div class="ma-agent-row">
            <button
              v-for="a in optionalAgents"
              :key="a.id"
              type="button"
              class="ma-agent-chip is-toggle"
              :class="{ 'is-on': selectedAgents.includes(a.id) }"
              :title="a.desc"
              :disabled="reviewing"
              @click="toggleAgent(a.id)"
            >{{ selectedAgents.includes(a.id) ? '✓ ' : '+ ' }}{{ a.name }}</button>
          </div>
          <p class="ma-agent-hint muted">核心 {{ coreAgents.length }} 名 + 能力 {{ selectedAgents.length }} 名 · {{ pipelineHint }}</p>
        </div>

        <!-- 任务输入：选中任务 = 任务提示词模板 + 具体问题；未选 = 自由任务 -->
        <div class="ma-task-box">
          <textarea
            v-model="taskInput"
            class="ma-task-input"
            rows="4"
            :placeholder="selectedTask
              ? `已选任务「${selectedTask.name}」：在这里输入具体问题/主题，运行时替换模板中的 {question}`
              : '输入任意任务（不限类型）…例如：检索知识库盘点主题 / 写一份简报 / 总结成摘要 / 回答开放问题'"
            :disabled="reviewing"
            @keydown.enter.exact.prevent="startTask"
          ></textarea>
          <div class="ma-task-actions">
            <span class="ma-task-hint">{{ selectedTask ? '按任务提示词模板运行 · Planner 动态分解' : '自由任务 · Planner 用 LLM 动态分解，按编制即刻成团' }}</span>
            <button class="ma-btn" type="button" :disabled="reviewing || !composeTask()" @click="startTask">
              {{ reviewing ? '团队运行中…' : '团队运行' }}
            </button>
          </div>
        </div>

        <div class="ma-tasklib-head">
          <p class="ma-examples-title muted">任务库（可配置 · 选中后输入问题运行）</p>
          <button class="ma-btn sm ghost" type="button" :disabled="reviewing" @click="openNewTask">＋ 新建任务</button>
        </div>

        <!-- 任务编辑表单（新建 / 编辑提示词） -->
        <div v-if="taskForm.show" class="ma-task-form">
          <input
            v-model="taskForm.name"
            class="ma-form-input"
            maxlength="100"
            placeholder="任务名称，如：研判 · 专题盘点"
          />
          <textarea
            v-model="taskForm.prompt"
            class="ma-form-input"
            rows="4"
            placeholder="任务提示词模板，支持 {question} 占位符——运行时替换为输入框里的具体问题；未含占位符时问题拼接在模板之后"
          ></textarea>
          <p class="ma-form-hint muted">保存时将把当前勾选的团队编制存为该任务的默认编制（选中任务时自动应用）</p>
          <div class="ma-form-actions">
            <button
              class="ma-btn sm"
              type="button"
              :disabled="savingTask || !taskForm.name.trim() || !taskForm.prompt.trim()"
              @click="saveTask"
            >保存</button>
            <button class="ma-btn sm ghost" type="button" @click="taskForm.show = false">取消</button>
          </div>
        </div>

        <!-- 任务库列表：点击选中 / ▶ 直接运行 / ✎ 编辑 / ✕ 删除 -->
        <div
          v-for="t in tasks"
          :key="t.id"
          class="ma-example"
          :class="{ 'is-active': selectedTaskId === t.id }"
          role="button"
          tabindex="0"
          @click="applyTask(t)"
          @keydown.enter.prevent="applyTask(t)"
        >
          <div class="ma-example-head">
            <b>{{ t.name }}</b>
            <span class="ma-example-acts">
              <i class="ma-act" title="按提示词直接运行" @click.stop="quickRun(t)">▶</i>
              <i class="ma-act" title="编辑提示词" @click.stop="openEditTask(t)">✎</i>
              <i class="ma-act danger" title="删除任务" @click.stop="removeTask(t)">✕</i>
            </span>
          </div>
          <span class="ma-example-prompt">{{ t.prompt }}</span>
        </div>
      </aside>

      <!-- ── 右：协作面板（通用渲染，与业务无关） ── -->
      <section class="ma-panel">
        <div v-if="!current" class="ma-placeholder">
          <p>输入任意任务，点「团队运行」；示例任务点击即跑。团队编制随勾选动态装配。</p>
          <p class="muted">流水线：{{ pipelineHint }}</p>
        </div>

        <template v-else>
          <div class="ma-panel-head">
            <strong>{{ current.title }} · {{ current.headline }}</strong>
            <span v-if="elapsed" class="muted">耗时 {{ elapsed }}ms</span>
            <button class="ma-btn sm" type="button" :disabled="reviewing" @click="rerun">
              重新运行
            </button>
          </div>

          <!-- 协作时间线 -->
          <ol class="ma-timeline">
            <li v-if="teamName" class="ma-t-team">团队：{{ teamName }}（{{ members.length }} 成员）</li>
            <li v-if="plan.length" class="ma-t-plan">
              <b>Planner</b> 拆解 {{ plan.length }} 项子任务
              <div class="ma-plan-steps">
                <span v-for="p in plan" :key="p.id">{{ p.goal }}</span>
              </div>
            </li>
            <li v-for="n in nodeOrder" :key="n" class="ma-t-node" :class="nodes[n].status">
              <span class="ma-dot" aria-hidden="true"></span>
              <div class="ma-t-body">
                <b>{{ memberName(n) }}</b>
                <span v-if="nodes[n].status === 'running'" class="ma-spin">进行中…</span>
                <span v-else class="muted">{{ nodes[n].summary }}</span>
              </div>
            </li>
          </ol>

          <!-- 素材卡：分类 tab + 可折叠；卡片 id 供成果引用 chip 定位 -->
          <div v-if="evidenceCards.length" class="ma-cards-wrap">
            <div class="ma-cards-bar">
              <button class="ma-fold" type="button" @click="evCollapsed = !evCollapsed">
                <i class="ma-fold-arrow" :class="{ open: !evCollapsed }">▸</i>
                <b>素材与事实</b>
                <span class="muted">{{ evidenceCards.length }} 条 · 成果引用编号可点击定位</span>
              </button>
              <div v-if="!evCollapsed" class="ma-tabs">
                <button
                  v-for="t in evTabs" :key="t.key" type="button" class="ma-tab"
                  :class="{ 'is-on': evTab === t.key }" @click="evTab = t.key"
                >{{ t.label }}<em>{{ t.count }}</em></button>
              </div>
            </div>
            <div v-show="!evCollapsed" class="ma-cards">
              <div
                v-for="c in filteredEvCards" :key="c.id" :id="`ma-card-${c._i}`"
                class="ma-ev" :class="[c.stance]"
              >
                <div class="ma-ev-head">
                  <b>{{ c.title }}</b>
                  <span class="ma-grade" :class="{ 'is-fact': c.grade === 'graph_fact' || c.grade === 'data_fact' || c.grade === 'tool_result' }">{{ gradeLabel(c.grade) }}</span>
                </div>
                <div class="muted ma-ev-src">{{ c.source }}</div>
                <p>{{ c.summary }}</p>
                <code v-if="c.quote">{{ c.quote }}</code>
              </div>
            </div>
          </div>

          <!-- 评审存疑 -->
          <div v-for="(cf, i) in conflicts" :key="i" class="ma-conflict">
            <b>⚠️ 评审质控：{{ cf.topic }}</b>
            <p>{{ cf.verdict }}</p>
            <p class="muted">{{ cf.basis }}<template v-if="cf.review_hint"> → {{ cf.review_hint }}</template></p>
          </div>

          <!-- 质控结论行 -->
          <div v-if="verdict" class="ma-verdict">
            质控建议：<b>{{ verdict.suggest_label }}</b>
            <span class="muted">· 置信度 {{ verdict.confidence }} · 需人裁：{{ verdict.need_human ? '是' : '否' }}</span>
          </div>

          <!-- 成果（流式 markdown；引用 chip 事件委托 → 定位素材卡） -->
          <article
            v-if="conclusionMd" class="ma-conclusion"
            @click="jumpCite" v-html="renderMd(conclusionMd)"
          ></article>

          <p v-if="errorMsg" class="ma-error">{{ errorMsg }}</p>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ma-page {
  padding: 20px 24px 32px;
  max-width: 1280px;
  margin: 0 auto;
}
.ma-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.ma-head h1 {
  font-size: 20px;
  margin: 0 0 4px;
}
.ma-sub {
  margin: 0;
  font-size: 12.5px;
  color: var(--c-secondary);
}
.ma-claim {
  margin: 0;
  font-size: 12.5px;
  color: var(--c-secondary);
}
.muted { color: var(--c-secondary); }

.ma-layout {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 16px;
  align-items: start;
}
@media (max-width: 960px) {
  .ma-layout { grid-template-columns: 1fr; }
}

.ma-col-title {
  font-size: 13px;
  margin: 0 0 8px;
  color: var(--c-fg);
}
.ma-scenario-desc {
  font-size: 12px;
  margin: 0 0 10px;
  line-height: 1.6;
}

/* 智能体组队 */
.ma-agents {
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 10px;
  margin-bottom: 12px;
}
.ma-agents-title {
  font-size: 12px;
  margin: 0 0 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.ma-agents-title span {
  font-size: 11px;
  color: var(--c-secondary);
  font-weight: 400;
}
.ma-agent-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.ma-agent-row + .ma-agent-row { margin-top: 6px; }
.ma-agent-chip {
  font-size: 11.5px;
  border-radius: 999px;
  padding: 3px 10px;
  border: 1px solid var(--c-border);
  background: transparent;
  color: var(--c-fg);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.ma-agent-chip.is-core {
  background: var(--c-muted);
  cursor: default;
}
.ma-agent-chip.is-core i {
  font-style: normal;
  font-size: 10px;
  color: var(--c-accent);
  border: 1px solid var(--c-accent);
  border-radius: 999px;
  padding: 0 6px;
}
.ma-agent-chip.is-toggle {
  cursor: pointer;
  color: var(--c-secondary);
}
.ma-agent-chip.is-toggle.is-on {
  border-color: var(--c-accent);
  color: var(--c-accent);
  background: var(--c-accent-weak);
  font-weight: 600;
}
.ma-agent-chip.is-toggle:disabled { cursor: not-allowed; opacity: 0.6; }
.ma-agent-hint {
  font-size: 11px;
  margin: 8px 0 0;
  line-height: 1.5;
}

/* 任务输入 */
.ma-task-box {
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 10px;
  margin-bottom: 12px;
}
.ma-task-input {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  min-height: 84px;
  background: var(--c-bg, transparent);
  color: var(--c-fg);
  border: 1px solid var(--c-border);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
  font-family: inherit;
}
.ma-task-input:focus {
  outline: none;
  border-color: var(--c-accent);
}
.ma-task-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
}
.ma-task-hint {
  font-size: 11px;
  color: var(--c-fg-muted, #888);
}
.ma-examples-title {
  font-size: 11px;
  margin: 0 0 8px;
}

/* 任务库 */
.ma-tasklib-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 0 8px;
}
.ma-btn.ghost { background: transparent; }
.ma-example {
  display: block;
  width: 100%;
  box-sizing: border-box;
  text-align: left;
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 8px;
  cursor: pointer;
  color: var(--c-fg);
}
.ma-example:hover { border-color: var(--c-accent); }
.ma-example:focus-visible { outline: none; border-color: var(--c-accent); }
.ma-example.is-active {
  border-color: var(--c-accent);
  box-shadow: 0 0 0 1px var(--c-accent) inset;
}
.ma-example-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.ma-example-head b {
  font-size: 12.5px;
}
.ma-example-acts {
  display: inline-flex;
  gap: 8px;
  flex: none;
}
.ma-act {
  font-style: normal;
  font-size: 12px;
  color: var(--c-secondary);
  cursor: pointer;
  padding: 0 2px;
}
.ma-act:hover { color: var(--c-accent); }
.ma-act.danger:hover { color: #dc2626; }
.ma-example-prompt {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 11.5px;
  color: var(--c-secondary);
  line-height: 1.5;
  margin-top: 2px;
}

/* 任务编辑表单 */
.ma-task-form {
  background: var(--c-bg, transparent);
  border: 1px dashed var(--c-accent);
  border-radius: 10px;
  padding: 10px;
  margin-bottom: 10px;
}
.ma-form-input {
  width: 100%;
  box-sizing: border-box;
  background: transparent;
  color: var(--c-fg);
  border: 1px solid var(--c-border);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12.5px;
  line-height: 1.6;
  font-family: inherit;
  margin-bottom: 8px;
  resize: vertical;
}
.ma-form-input:focus { outline: none; border-color: var(--c-accent); }
.ma-form-hint {
  font-size: 11px;
  margin: 0 0 8px;
}
.ma-form-actions { display: flex; gap: 8px; }

.ma-btn {
  border: 1px solid var(--c-accent);
  background: var(--c-accent-weak);
  color: var(--c-accent);
  font-weight: 600;
  font-size: 12.5px;
  border-radius: 8px;
  padding: 6px 14px;
  cursor: pointer;
}
.ma-btn:hover:not(:disabled) { filter: brightness(1.05); }
.ma-btn:disabled { opacity: 0.55; cursor: not-allowed; }
.ma-btn.sm { padding: 3px 10px; font-size: 12px; }

/* 右侧面板 */
.ma-panel {
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 16px 18px;
  min-height: 320px;
}
.ma-placeholder {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  min-height: 280px;
  text-align: center;
  font-size: 14px;
}
.ma-placeholder .muted { font-size: 12.5px; }
.ma-panel-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  font-size: 14.5px;
}
.ma-panel-head .ma-btn { margin-left: auto; }

/* 时间线 */
.ma-timeline {
  list-style: none;
  margin: 0 0 12px;
  padding: 10px 12px;
  border: 1px dashed var(--c-border);
  border-radius: 10px;
  font-size: 12.5px;
}
.ma-t-team { color: var(--c-secondary); margin-bottom: 6px; }
.ma-t-plan { margin-bottom: 6px; }
.ma-plan-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.ma-plan-steps span {
  font-size: 11.5px;
  background: var(--c-accent-weak);
  color: var(--c-accent);
  border-radius: 999px;
  padding: 2px 10px;
}
.ma-t-node {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 3px 0;
}
.ma-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 5px;
  flex: none;
  background: var(--c-border);
}
.ma-t-node.running .ma-dot { background: #d97706; animation: ma-pulse 1s infinite; }
.ma-t-node.done .ma-dot { background: #16a34a; }
.ma-t-body b { margin-right: 8px; }
.ma-spin { color: #d97706; }
@keyframes ma-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

/* 素材卡：分类 tab + 可折叠容器 */
.ma-cards-wrap {
  border: 1px solid var(--c-border);
  border-radius: 10px;
  margin-bottom: 12px;
  overflow: hidden;
}
.ma-cards-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: var(--c-muted);
  font-size: 12.5px;
}
.ma-fold {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: none;
  color: var(--c-fg);
  cursor: pointer;
  font-size: 12.5px;
  padding: 0;
}
.ma-fold-arrow {
  font-style: normal;
  display: inline-block;
  transition: transform 0.15s;
  color: var(--c-secondary);
}
.ma-fold-arrow.open { transform: rotate(90deg); }
.ma-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.ma-tab {
  font-size: 11.5px;
  border: 1px solid var(--c-border);
  background: transparent;
  color: var(--c-secondary);
  border-radius: 999px;
  padding: 2px 10px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.ma-tab em {
  font-style: normal;
  font-size: 10.5px;
  background: var(--c-muted);
  border-radius: 999px;
  padding: 0 5px;
}
.ma-tab.is-on {
  border-color: var(--c-accent);
  color: var(--c-accent);
  background: var(--c-accent-weak);
  font-weight: 600;
}
.ma-tab.is-on em { background: transparent; color: var(--c-accent); }
.ma-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.ma-cards-wrap .ma-cards { margin-bottom: 0; padding: 10px; }
.ma-ev {
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 12.5px;
  background: var(--c-bg);
}
.ma-ev.worsen { border-left: 3px solid #ea580c; }
.ma-ev.improve { border-left: 3px solid #16a34a; }
.ma-ev.neutral { border-left: 3px solid var(--c-border); }
.ma-ev.fact { border-left: 3px solid var(--c-accent); }
.ma-ev.is-flash { animation: ma-flash 1.5s ease; }
@keyframes ma-flash {
  0%, 55% { box-shadow: 0 0 0 2px var(--c-accent); border-color: var(--c-accent); }
  100% { box-shadow: none; }
}
.ma-ev-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.ma-grade {
  font-size: 11px;
  color: var(--c-secondary);
  border: 1px solid var(--c-border);
  border-radius: 999px;
  padding: 0 8px;
  white-space: nowrap;
}
.ma-grade.is-fact {
  color: var(--c-accent);
  border-color: var(--c-accent);
}
.ma-ev-src { font-size: 11.5px; margin: 2px 0 6px; }
.ma-ev p { margin: 0 0 6px; line-height: 1.55; }
.ma-ev code {
  display: block;
  font-size: 11.5px;
  color: var(--c-secondary);
  background: var(--c-muted);
  border-radius: 6px;
  padding: 4px 8px;
}

/* 评审 / 质控 / 成果 */
.ma-conflict {
  border: 1px solid rgba(217, 119, 6, 0.55);
  background: rgba(217, 119, 6, 0.08);
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 13px;
  margin-bottom: 10px;
}
.ma-conflict p { margin: 4px 0 0; }
.ma-verdict {
  font-size: 13.5px;
  margin-bottom: 10px;
}
.ma-verdict b { color: var(--c-accent); }
.ma-conclusion {
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 12px 16px;
  font-size: 13.5px;
  line-height: 1.7;
}
.ma-conclusion :deep(h3) { font-size: 14px; margin: 10px 0 6px; }
.ma-conclusion :deep(h3:first-child) { margin-top: 0; }
.ma-conclusion :deep(ul) { margin: 4px 0; padding-left: 20px; }
.ma-conclusion :deep(ol) { margin: 4px 0; padding-left: 20px; }
.ma-conclusion :deep(p) { margin: 6px 0; }
/* 成果引用 chip：可点击定位到素材面板对应卡片 */
.ma-conclusion :deep(.ma-cite) {
  display: inline-block;
  font-size: 11px;
  line-height: 1.4;
  color: var(--c-accent);
  background: var(--c-accent-weak);
  border: 1px solid var(--c-accent);
  border-radius: 999px;
  padding: 0 7px;
  margin: 0 2px;
  cursor: pointer;
  vertical-align: 2px;
  user-select: none;
  white-space: nowrap;
}
.ma-conclusion :deep(.ma-cite:hover) { filter: brightness(1.12); }
.ma-conclusion :deep(.ma-cite.is-missing) {
  color: var(--c-secondary);
  border-color: var(--c-border);
  background: transparent;
  cursor: not-allowed;
  text-decoration: line-through;
}

.ma-error {
  color: #dc2626;
  font-size: 13px;
  margin: 8px 0 0;
}
</style>
