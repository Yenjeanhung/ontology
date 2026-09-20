<script setup>
/**
 * 多智能体协作（通用智能体团队，业务无关、任务类型无关）。
 *
 * 聊天式协作：主区为消息流（用户任务气泡 + 团队过程/成果块逐轮顺排），底部输入区
 * 常驻——智能体组队（默认 0.6B 意图路由自动组队，可切手动勾选能力智能体）+ 任务
 * 提示词模板 + 任务输入。每次运行自动留痕到协作会话，点开历史即多轮回放；同一会话
 * 内可继续追问（短期记忆）。事件契约见 doc/智能体/多智能体场景.md §4。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import {
  createMultiTask,
  deleteMultiSession,
  deleteMultiTask,
  listMultiScenarios,
  listMultiSessionMessages,
  listMultiSessions,
  listMultiTasks,
  renameMultiSession,
  streamTaskRun,
  updateMultiTask,
} from '../../api/multiAgent'

// ── 场景、任务库 ──
const scenario = ref(null)
const tasks = ref([])
const selectedTaskId = ref('')
const loadError = ref('')
const taskInput = ref('')
const taskForm = ref({ show: false, id: '', name: '', prompt: '' })
const savingTask = ref(false)

// 智能体名册（后端下发，本地兜底）
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
const optionalAgents = computed(() => [
  ...(roster.value.optional || []),
  ...(roster.value.custom || []),   // 自定义智能体（智能体配置页，custom:{id}）
])
/** 欢迎区一行阵容展示：核心在前、可选在后（opt 标记 = 底部可手动勾选的能力智能体）。 */
const allRoster = computed(() => [
  ...(roster.value.core || []).map((a) => ({ ...a, opt: false })),
  ...optionalAgents.value.map((a) => ({ ...a, opt: true })),
])

// ── 智能体组队：默认自动路由，可切手动勾选（后端非空 agents = 尊重用户组合） ──
const agentsMode = ref('auto')     // 'auto' = 意图路由自动组队 | 'manual' = 手动勾选
const setupOpen = ref(true)        // 顶部协作配置面板（阵容/任务库/组队）展开态
const manualAgents = ref([])       // 手动勾选的能力智能体 id（空 = 按默认组合跑）

function toggleAgent(id) {
  manualAgents.value = manualAgents.value.includes(id)
    ? manualAgents.value.filter((x) => x !== id)
    : [...manualAgents.value, id]
}

/** 配置面板介绍卡即勾选：点能力智能体卡 → 自动切手动模式并勾选/取消（核心卡内置不可点）。 */
function toggleCardAgent(a) {
  if (!a.opt) return
  if (agentsMode.value !== 'manual') agentsMode.value = 'manual'
  toggleAgent(a.id)
}

// 切回自动路由时清掉手动勾选：卡片选中态/高亮随之消失，组队口径不残留
watch(agentsMode, (mode) => {
  if (mode === 'auto') manualAgents.value = []
})

function pickAgents() {
  return agentsMode.value === 'manual' ? [...manualAgents.value] : []
}

const agentHint = computed(() => agentsMode.value === 'auto'
  ? '0.6B 意图路由按任务自动组队：chat 高置信直答 · data/graph/kb 精简组合 · 低置信兜底全组合'
  : (manualAgents.value.length
    ? `手动组合（${manualAgents.value.length} 项）· 路由仅叠加 NL2Filter / 改写门控`
    : '点击上方能力智能体卡片勾选；未选 → 按默认组合（Retriever / Data / Graph / Critic）'))

// ── 协作会话（协作历史 / 短期记忆） ──
const sessions = ref([])
const activeSessionId = ref('')    // 多轮续聊锚点（session 事件回传后非空）
const loadingSession = ref(false)

// ── 运行状态 ──
const runState = ref({ reviewing: false })
let abortCtrl = null
const reviewing = computed(() => runState.value.reviewing)

/** 聊天轮次：每轮 = 用户任务气泡 + 团队块（过程/素材/质控/成果独立状态）。 */
const rounds = ref([])

function newRound(task) {
  return {
    task,
    live: true,
    reviewing: true,
    team: '', members: [], route: null, plan: [], planStep: null,
    nodes: {}, nodeOrder: [],
    evidenceDomains: [],
    conflicts: [], verdict: null,
    conclusion: '', error: '', elapsed: 0,
    manual: false,
    evTab: 'all', procCollapsed: false,
  }
}

/** 历史轮重建：meta（后端 turn_meta，与渲染状态同构）→ 团队块静态渲染。 */
function roundFromMeta(task, conclusion, meta) {
  const r = newRound(task)
  r.live = false
  r.reviewing = false
  r.procCollapsed = true   // 历史回放：过程默认收起，对话流只露结论
  const m = meta || {}
  r.team = m.team || ''
  r.members = m.members || []
  r.route = m.route || null
  r.planStep = m.plan_ms != null ? { ms: m.plan_ms, summary: m.plan_summary || '' } : null
  r.plan = m.plan || []
  for (const n of m.nodes || []) {
    r.nodes[n.node] = { status: 'done', summary: n.summary || '', ms: n.elapsed_ms ?? null }
    r.nodeOrder = [...r.nodeOrder, n.node]
  }
  r.evidenceDomains = (m.domains || []).map((d) => ({
    domain: d.domain,
    cards: (d.cards || []).map((c) => ({
      ...c,
      // __facts__ 组的前端 domain 由 grade 推导（与实时 fact 事件处理一致）
      domain: d.domain === '__facts__'
        ? (c.grade === 'data_fact' ? 'data' : c.grade === 'tool_result' ? 'tool' : 'graph')
        : d.domain,
    })),
  }))
  r.conflicts = m.conflicts || []
  r.verdict = m.verdict || null
  r.conclusion = conclusion || ''
  r.elapsed = m.elapsed_ms || 0
  r.error = !conclusion && !meta ? '该轮无成果产出（运行未完成）' : ''
  return r
}

const NODE_FALLBACK_NAMES = {
  planner: 'Planner · 任务规划',
  graph_agent: 'GraphAgent · 图谱事实',
  critic: 'Critic · 评审质控',
  synthesizer: 'Synthesizer · 结果合成',
}

// ── 素材分类 / 折叠 / 引用定位（按轮） ──
// 编号约定：[素材N]=证据卡拍平序，[事实N]=事实卡序（stance==='fact'）。
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

function cardsOf(r) {
  return (r.evidenceDomains || []).flatMap((d) => d.cards)
}

function taggedOf(r) {
  return cardsOf(r).map((c, i) => ({ ...c, _i: i, kind: cardKind(c) }))
}

function evTabsOf(r) {
  const cards = taggedOf(r)
  const counts = {}
  for (const c of cards) counts[c.kind] = (counts[c.kind] || 0) + 1
  const tabs = [{ key: 'all', label: '全部', count: cards.length }]
  for (const k of ['doc', 'model', 'graph', 'data', 'tool']) {
    if (counts[k]) tabs.push({ key: k, label: KIND_META[k].label, count: counts[k] })
  }
  return tabs
}

function filteredEvOf(r) {
  const cards = taggedOf(r)
  return r.evTab === 'all' ? cards : cards.filter((c) => c.kind === r.evTab)
}

let flashTimer = null

function flashCard(el) {
  if (!el) return
  el.classList.remove('is-flash')
  void el.offsetWidth
  el.classList.add('is-flash')
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => el.classList.remove('is-flash'), 1600)
}

/** 成果里的引用 chip 点击 → 滚动定位到该轮素材面板对应卡片并高亮。 */
function jumpCite(evt) {
  const chip = evt.target.closest('.ma-cite')
  if (!chip || chip.classList.contains('is-missing')) return
  const ri = Number(chip.dataset.round)
  const r = rounds.value[ri]
  if (!r) return
  const n = Number(chip.dataset.n)
  const cards = taggedOf(r)
  const card = chip.dataset.kind === 'fact'
    ? cards.filter((c) => c.stance === 'fact')[n - 1] || null
    : cards[n - 1] || null
  if (!card) return
  if (r.evTab !== 'all' && r.evTab !== card.kind) r.evTab = card.kind
  requestAnimationFrame(() => {
    const el = document.getElementById(`ma-card-${ri}-${card._i}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      flashCard(el)
    }
  })
}

function memberName(r, node) {
  const m = (r.members || []).find((x) => x.node === node)
  if (m?.name) return m.name
  if (NODE_FALLBACK_NAMES[node]) return NODE_FALLBACK_NAMES[node]
  if (node.startsWith('retriever')) return 'Retriever · 知识库取证'
  if (node.startsWith('worker')) return 'Worker · 子任务执行'
  return node
}

/** 过程面板收起态摘要：团队 · N 子任务 · 已完成/总步数 · 总耗时。 */
function procSummary(r) {
  if (r.error) return r.error.length > 48 ? r.error.slice(0, 48) + '…' : r.error
  const done = r.nodeOrder.filter((n) => r.nodes[n]?.status === 'done').length
  const parts = [r.team || '团队组建中']
  if (r.plan.length) parts.push(`${r.plan.length} 子任务`)
  if (r.nodeOrder.length) parts.push(`${done}/${r.nodeOrder.length} 步`)
  if (r.elapsed) parts.push(`${(r.elapsed / 1000).toFixed(1)}s`)
  return parts.join(' · ')
}

/** 运行中当前活动节点（时间线上最后一个未完成节点）。 */
function activeNodeName(r) {
  const cur = [...r.nodeOrder].reverse().find((n) => r.nodes[n]?.status !== 'done')
  return cur ? memberName(r, cur) : ''
}

/** 置信度百分比：非有限数（NaN/字符串"NaN"/null）一律不显示。 */
function fmtPct(v) {
  const n = Number(v)
  return Number.isFinite(n) ? Math.round(n * 100) : null
}

// ── 执行链路前置步骤（按轮）：意图路由 → NL2Filter，并入节点时间线展示 ──
function routeSteps(r) {
  const rt = r.route
  if (!rt) {
    // 手动组队 / 运行初期尚未收到 team 事件：占住第一步；旧回放无记录则不显行
    if (!r.live && !r.manual) return []
    return [{
      key: 'route', status: r.live ? 'running' : 'done',
      label: '意图路由 · 0.6B 小模型',
      desc: r.manual ? '手动组队指定组合，跳过小模型路由' : '小模型判别任务意图与组队…',
    }]
  }
  const steps = [
    {
      key: 'route', status: 'done',
      label: '意图路由 · 0.6B 小模型',
      ms: Number.isFinite(rt.elapsed_ms) ? rt.elapsed_ms : null,
      desc: rt.source === 'router'
        ? (rt.manual
          ? `mode=${rt.mode} · 置信 ${Number(rt.confidence || 0).toFixed(2)} · 手动组队，组合由用户指定`
          : `mode=${rt.mode} · 置信 ${Number(rt.confidence || 0).toFixed(2)}`
            + (Array.isArray(rt.agents) && rt.agents.length ? ` · 精简组合 [${rt.agents.join(',')}]` : ''))
        : '服务不可达/低置信 → 全组合老规则兜底',
    },
  ]
  // Planner 构建期规划（step_done 事件即时送达；旧回放无此记录则不显行）
  if (r.planStep) {
    steps.push({
      key: 'plan', status: 'done',
      label: 'Planner · LLM 任务规划',
      ms: Number.isFinite(r.planStep.ms) ? r.planStep.ms : null,
      desc: r.planStep.summary || 'LLM 分解并行子任务',
    })
  }
  // NL2Filter：未放行 → 未启用；放行但耗时未到（live 抽取中）→ running 态占位
  const nlReady = rt.nl2filter && Number.isFinite(rt.nl2filter_ms)
  steps.push({
    key: 'nl2f', status: rt.nl2filter && !nlReady && r.live ? 'running' : 'done',
    label: 'NL2Filter · 0.6B 小模型',
    ms: nlReady ? rt.nl2filter_ms : null,
    desc: !rt.nl2filter
      ? (rt.mode === 'data'
        ? (rt.source === 'router' ? '未启用（NL2FILTER_ENABLED 开关关闭）' : '未启用（路由不可用，全组合兜底）')
        : (rt.mode ? `未启用（路由判为 ${rt.mode} 类，仅 data 台账查询类启用）` : '未启用（路由不可用，全组合兜底）'))
      : (nlReady
        ? (rt.nl2filter_hit ? '抽取命中 → DataAgent 精准口径' : '未命中 → 词频老路兜底')
        : '0.6B 抽取结构化查询条件…'),
  })
  return steps
}

/** 耗时格式化：≥1s 显示 x.xs，否则 xxxms（仅对非空 ms 调用）。 */
function fmtMs(ms) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

// ── 聊天区滚动：新轮次强制到底，流式输出仅在用户本就贴近底部时跟随 ──
const chatEl = ref(null)

function scrollChat(force = false) {
  nextTick(() => {
    const el = chatEl.value
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 140
    if (force || near) el.scrollTop = el.scrollHeight
  })
}

// ── SSE 事件 → 轮状态 ──
function touchNode(r, node) {
  if (!r.nodes[node]) {
    r.nodes[node] = { status: 'running', summary: '', ms: null, _t0: performance.now() }
    r.nodeOrder = [...r.nodeOrder, node]
  }
}

function handleEvent(r, evt) {
  switch (evt.type) {
    case 'session':
      // 会话锚点：新建会话时后端回传 id，后续输入续聊同一会话
      activeSessionId.value = evt.session_id || ''
      refreshSessions()
      break
    case 'team':
      r.team = evt.team || ''
      r.members = evt.members || []
      r.route = evt.route || null
      break
    case 'step_done':
      // 构建期步骤完成（后端每步结束即时推送，含真实耗时）：
      // planner → 时间线 Planner 行；nl2filter → 合入 r.route（routeSteps 已消费该口径）
      if (evt.step === 'planner') r.planStep = { ms: evt.elapsed_ms ?? null, summary: evt.summary || '' }
      else if (evt.step === 'nl2filter') r.route = { ...(r.route || {}), nl2filter_ms: evt.elapsed_ms ?? null, nl2filter_hit: !!evt.hit, nl2filter: true }
      break
    case 'plan':
      r.plan = evt.plan || []
      break
    case 'node_start':
      touchNode(r, evt.node)
      break
    case 'node_done': {
      touchNode(r, evt.node)
      const t0 = r.nodes[evt.node]?._t0
      // 优先后端计时（engine.emit 统一出口，与回放同口径）；无则回退前端现场测量
      r.nodes[evt.node] = {
        status: 'done',
        summary: evt.summary || '',
        ms: evt.elapsed_ms ?? (t0 != null ? Math.round(performance.now() - t0) : null),
      }
      break
    }
    case 'evidence': {
      const hit = r.evidenceDomains.find((d) => d.domain === evt.domain)
      if (hit) hit.cards = evt.cards || []
      else r.evidenceDomains = [...r.evidenceDomains, { domain: evt.domain, cards: evt.cards || [] }]
      break
    }
    case 'fact':
      // 图谱/结构化/工具事实卡独立成组（grade 区分，优先采信）
      r.evidenceDomains = [...r.evidenceDomains, { domain: '__facts__', cards: (evt.facts || []).map((f) => ({
        id: f.id,
        domain: f.grade === 'data_fact' ? 'data' : f.grade === 'tool_result' ? 'tool' : 'graph',
        grade: f.grade,
        source: f.grade === 'data_fact' ? '实体台账 · 结构化查询'
          : f.grade === 'tool_result' ? '工具链 · Function Calling' : '本体图谱 · 结构化事实',
        title: f.title, summary: f.detail, quote: '', stance: 'fact',
      })) }]
      break
    case 'conflict':
      r.conflicts = evt.conflicts || []
      r.verdict = {
        suggest_label: evt.suggest_label,
        confidence: evt.confidence,
        need_human: evt.need_human,
        comment: evt.comment,
      }
      break
    case 'token':
      // reset=true：合成官引用自检未过、重放修正稿——先清空已渲染的草稿再追加
      if (evt.reset) r.conclusion = ''
      r.conclusion += evt.content || ''
      scrollChat()
      break
    case 'done':
      if (evt.conclusion && !r.conclusion) r.conclusion = evt.conclusion
      r.elapsed = evt.elapsed_ms || 0
      r.reviewing = false
      r.live = false
      r.procCollapsed = true   // 完成后自动收起过程面板（主流做法：结论留在对话流）
      scrollChat()
      break
    case 'error':
      r.error = evt.content || ''
      r.reviewing = false
      break
    default:
      break
  }
}

/** 发起一轮团队协作：任务文本 → 追加聊天轮次 → SSE 增量渲染（多轮续聊）。 */
async function sendWithTask(task, agents = []) {
  if (reviewing.value || !task) return
  abortCtrl?.abort()
  abortCtrl = new AbortController()
  const r = newRound(task)
  r.manual = agents.length > 0   // 手动勾选组合 = 跳过 0.6B 意图路由
  rounds.value = [...rounds.value, r]
  // 关键：SSE 回调必须持有“响应式代理”而非 newRound 的原始对象——
  // 原始引用赋值不触发依赖通知，事件数据虽已写入却不重渲（收起再展开才可见）。
  const rx = rounds.value[rounds.value.length - 1]
  runState.value.reviewing = true
  scrollChat(true)
  try {
    await streamTaskRun(scenario.value?.id || 'universal', task, agents, {
      onEvent: (evt) => handleEvent(rx, evt),
      signal: abortCtrl.signal,
      sessionId: activeSessionId.value || undefined,
    })
  } catch (err) {
    if (err?.name !== 'AbortError') rx.error = err?.message || '协作请求失败'
  } finally {
    rx.reviewing = false
    rx.live = false
    runState.value.reviewing = false
  }
}

/** 底部输入区发送：选中任务 = 提示词模板 + 输入问题；未选 = 自由任务。 */
function send() {
  const task = composeTask()
  if (!task) return
  const agents = pickAgents()
  taskInput.value = ''
  sendWithTask(task, agents)
}

// ── 协作会话：列表刷新 / 多轮回放 / 管理 ──

function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function refreshSessions() {
  try {
    sessions.value = await listMultiSessions()
  } catch {
    /* 历史加载失败不打扰主流程 */
  }
}

/** 开新协作：清空会话锚点与聊天流，下次运行自动新建会话。 */
function newSession() {
  if (reviewing.value) return
  activeSessionId.value = ''
  rounds.value = []
}

/** 会话消息 → 回放轮次：按 user 消息分轮，挂接其后第一条 assistant。 */
function buildRounds(messages) {
  const out = []
  for (const m of messages) {
    if (m.role === 'user') out.push({ task: m.content || '', conclusion: '', meta: null })
    else if (m.role === 'assistant' && out.length) {
      const r = out[out.length - 1]
      r.conclusion = m.content || ''
      r.meta = m.meta || null
    }
  }
  return out
}

/** 点历史会话：拉取消息 → 全部轮次按顺序渲染为聊天流（多轮回放）。 */
async function openSession(s) {
  if (reviewing.value) return
  loadingSession.value = true
  try {
    const detail = await listMultiSessionMessages(s.session_id || s.id)
    activeSessionId.value = detail.session_id
    rounds.value = buildRounds(detail.messages || [])
      .map((x) => roundFromMeta(x.task, x.conclusion, x.meta))
    scrollChat(true)
  } catch (err) {
    loadError.value = err?.message || '加载会话失败'
  } finally {
    loadingSession.value = false
  }
}

/** 轮次一键重跑：以该轮任务文本续聊当前会话（组合沿用当前组队选择）。 */
function rerunRound(r) {
  if (reviewing.value || !r.task) return
  sendWithTask(r.task, pickAgents())
}

async function removeSession(s) {
  if (reviewing.value) return
  try {
    await deleteMultiSession(s.id)
    sessions.value = sessions.value.filter((x) => x.id !== s.id)
    if (activeSessionId.value === s.id) newSession()
  } catch (err) {
    loadError.value = err?.message || '删除会话失败'
  }
}

async function renameSession(s) {
  const title = (window.prompt('重命名协作会话', s.title || '') || '').trim()
  if (!title) return
  try {
    const updated = await renameMultiSession(s.id, title)
    sessions.value = sessions.value.map((x) => (x.id === s.id ? { ...x, title: updated.title } : x))
  } catch (err) {
    loadError.value = err?.message || '重命名失败'
  }
}

const selectedTask = computed(() => tasks.value.find((t) => t.id === selectedTaskId.value) || null)

/** 组合最终任务文本：选中任务 → 提示词模板（{question} 替换为用户问题）；未选 → 纯自由输入。 */
function composeTask() {
  const q = taskInput.value.trim()
  const t = selectedTask.value
  if (!t) return q
  if (!q) return t.prompt
  if ((t.prompt || '').includes('{question}')) return t.prompt.replaceAll('{question}', q)
  return `${t.prompt}\n\n${q}`
}

/** 选中/取消任务模板（组队由自动路由或手动勾选决定，任务仅提供提示词模板）。 */
function applyTask(t) {
  selectedTaskId.value = selectedTaskId.value === t.id ? '' : t.id
}

/** 任务库 ▶：按提示词（+ 已输入的问题）直接起一轮。 */
function quickRun(t) {
  if (reviewing.value) return
  selectedTaskId.value = t.id
  const q = taskInput.value.trim()
  const task = q ? composeTask() : t.prompt
  taskInput.value = ''
  sendWithTask(task, pickAgents())
}

// ── 任务库 CRUD ──
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
      const updated = await updateMultiTask(f.id, { name, prompt, agents: [] })
      tasks.value = tasks.value.map((t) => (t.id === f.id ? updated : t))
    } else {
      const created = await createMultiTask({ name, prompt, agents: [] })
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
  } catch (err) {
    loadError.value = err?.message || '删除任务失败'
  }
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
    if (Math.max(a, b) - Math.min(a, b) <= 50) {
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) nums.add(i)
    }
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

function renderMd(text, ri) {
  try {
    let html = marked.parse(text || '', { breaks: true })
    const r = rounds.value[ri]
    const cards = r ? taggedOf(r) : []
    const nMat = cards.length
    const nFact = cards.filter((c) => c.stance === 'fact').length
    // 引用 chip：单张 [素材3]/[事实5]、连续范围 [事实2-9]、离散列表 [事实2、5]
    html = html.replace(/\[(素材|事实)([0-9、,，·\-~—至\s]+)\]/g, (_, kind, inner) => {
      const total = kind === '事实' ? nFact : nMat
      const ok = parseCiteNums(inner).filter((n) => n >= 1 && n <= total)
      const label = ok.length ? `${kind}${fmtCiteNums(ok)}` : `${kind}${String(inner).trim()}`
      const title = ok.length ? `点击定位${kind}卡：${ok.join('、')}` : '引用编号不存在'
      return `<sup class="ma-cite${ok.length ? '' : ' is-missing'}" data-round="${ri}" data-kind="${kind === '事实' ? 'fact' : 'mat'}" data-n="${ok[0] || ''}" title="${title}">${label}</sup>`
    })
    return html
  } catch {
    return text
  }
}

onMounted(async () => {
  refreshSessions()
  try {
    const scenarios = await listMultiScenarios()
    scenario.value = scenarios.find((s) => s.adhoc) || scenarios[0] || null
    if (!scenario.value) return
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
    <!-- 左栏：协作会话（多轮留痕 / 回放 / 续聊） -->
    <aside class="ma-side">
      <div class="ma-side-head">
        <span>协作历史</span>
        <button class="ma-mini" @click="newSession">＋ 新协作</button>
      </div>
      <div class="ma-side-list">
        <p v-if="loadingSession" class="ma-side-empty">加载中…</p>
        <p v-else-if="!sessions.length" class="ma-side-empty">暂无历史：发起一次协作即自动留痕</p>
        <div v-for="s in sessions" :key="s.id" class="ma-sess"
             :class="{ active: s.id === activeSessionId }" @click="openSession(s)">
          <div class="ma-sess-title">{{ s.title || '未命名协作' }}</div>
          <div class="ma-sess-meta">
            <span>{{ fmtTime(s.updated_at || s.created_at) }}</span>
            <span class="ma-sess-ops" @click.stop>
              <button title="重命名" @click="renameSession(s)">✎</button>
              <button title="删除" @click="removeSession(s)">✕</button>
            </span>
          </div>
        </div>
      </div>
    </aside>

    <!-- 主区：聊天流 + 底部输入 -->
    <section class="ma-main">
      <header class="ma-head">
        <div>
          <h2>{{ scenario?.team || '通用智能体团队' }}</h2>
          <p>{{ scenario?.desc || '任务分解 → 并行取证 → 交叉评审 → 结果合成' }}</p>
        </div>
        <span v-if="activeSessionId" class="ma-head-tag">会话中 · 追问延续上下文</span>
      </header>

      <!-- 协作配置面板（常驻顶部，可收起）：智能体阵容介绍 + 任务库 + 组队 -->
      <div class="ma-setup">
        <button class="ma-setup-bar" @click="setupOpen = !setupOpen">
          <span>协作配置 · 团队阵容 / 任务库 / 组队</span>
          <span class="ma-setup-arrow">{{ setupOpen ? '▾' : '▸' }}</span>
        </button>
        <div v-show="setupOpen" class="ma-setup-body">
          <div class="ma-setup-agents">
            <div v-for="a in allRoster" :key="a.id" class="ma-wagent"
                 :class="{ opt: a.opt, pickable: a.opt, picked: a.opt && manualAgents.includes(a.id) }"
                 :title="a.opt ? '点击勾选 / 取消该智能体（手动组队）' : '核心智能体 · 内置不可取消'"
                 @click="toggleCardAgent(a)">
              <span v-if="a.opt && manualAgents.includes(a.id)" class="ma-pick-badge">✓</span>
              <b>{{ a.name }}</b><span>{{ a.desc }}</span>
            </div>
          </div>
          <div v-if="tasks.length" class="ma-setup-row">
            <span class="ma-comp-label">任务库</span>
            <div class="ma-task-chips">
              <span v-for="t in tasks" :key="t.id" class="ma-task-chip"
                    :class="{ on: selectedTaskId === t.id }">
                <button class="chip-main" :title="t.prompt" @click="applyTask(t)">{{ t.name }}</button>
                <button class="chip-ico" title="按模板直接运行" @click="quickRun(t)">▶</button>
                <button class="chip-ico" title="编辑" @click="openEditTask(t)">✎</button>
                <button class="chip-ico danger" title="删除" @click="removeTask(t)">✕</button>
              </span>
              <button class="ma-mini" @click="openNewTask">＋ 自定义任务</button>
            </div>
          </div>
          <div class="ma-setup-row">
            <span class="ma-comp-label">组队</span>
            <div class="ma-pick">
              <label><input v-model="agentsMode" type="radio" value="auto" /> 自动路由</label>
              <label><input v-model="agentsMode" type="radio" value="manual" /> 手动勾选</label>
              <span class="ma-pick-hint">{{ agentHint }}</span>
            </div>
          </div>
        </div>
      </div>

      <div ref="chatEl" class="ma-chat">
        <div v-if="!rounds.length" class="ma-welcome">
          <h3>发起一次团队协作</h3>
          <p>在上方选择任务模板或组队方式，在下方输入任务，团队自动分工交付。</p>
        </div>

        <p v-if="loadError" class="ma-error">{{ loadError }}</p>

        <!-- 每轮 = 用户任务气泡 + 团队协作卡片 -->
        <div v-for="(r, ri) in rounds" :key="ri" class="ma-turn">
          <div class="ma-user-row">
            <div class="ma-user-bubble">{{ r.task }}</div>
          </div>

          <!-- 执行过程面板：收起 = 一行状态摘要；展开 = 计划/智能体发言/素材/质控/链路耗时 -->
          <div class="ma-proc">
            <button class="ma-proc-bar" @click="r.procCollapsed = !r.procCollapsed">
              <span class="ma-proc-dot" :class="r.error ? 'err' : r.live ? 'live' : 'ok'">
                {{ r.error ? '✕' : r.live ? '◔' : '✓' }}
              </span>
              <span class="ma-proc-sum">{{ procSummary(r) }}</span>
              <span v-if="r.live && activeNodeName(r)" class="ma-proc-cur">{{ activeNodeName(r) }}</span>
              <span class="ma-proc-arrow">{{ r.procCollapsed ? '▸' : '▾' }}</span>
            </button>

            <div v-if="!r.procCollapsed" class="ma-proc-body">
              <p v-if="r.error" class="ma-error">{{ r.error }}</p>

            <ol v-if="r.plan.length" class="ma-plan">
              <li v-for="(p, pi) in r.plan" :key="pi">
                {{ typeof p === 'string' ? p : (p.desc || p.title || p.goal || JSON.stringify(p)) }}
              </li>
            </ol>

            <!-- 执行链路时间线：意图路由 → NL2Filter → 编排节点（每步耗时就地标注） -->
            <ul v-if="r.nodeOrder.length || routeSteps(r).length" class="ma-nodes">
              <li v-for="s in routeSteps(r)" :key="s.key" :class="s.status">
                <span class="ma-dot">{{ s.status === 'done' ? '✓' : '…' }}</span>
                <span class="ma-node-name">{{ s.label }}</span>
                <span v-if="s.ms != null" class="ma-node-ms">{{ fmtMs(s.ms) }}</span>
                <span class="ma-node-sum">{{ s.desc }}</span>
              </li>
              <li v-for="n in r.nodeOrder" :key="n" :class="r.nodes[n]?.status">
                <span class="ma-dot">{{ r.nodes[n]?.status === 'done' ? '✓' : '…' }}</span>
                <span class="ma-node-name">{{ memberName(r, n) }}</span>
                <span v-if="r.nodes[n]?.ms != null" class="ma-node-ms">{{ fmtMs(r.nodes[n].ms) }}</span>
                <span class="ma-node-sum">{{ r.nodes[n]?.summary }}</span>
              </li>
            </ul>
            <!-- 共享黑板素材（常显，tabs 分类过滤） -->
            <div v-if="cardsOf(r).length" class="ma-ev">
              <div class="ma-ev-title">共享黑板素材（{{ cardsOf(r).length }}）</div>
              <div class="ma-ev-tabs">
                <button v-for="t in evTabsOf(r)" :key="t.key"
                        :class="{ on: r.evTab === t.key }" @click="r.evTab = t.key">
                  {{ t.label }} {{ t.count }}
                </button>
              </div>
              <div class="ma-ev-list">
                <div v-for="c in filteredEvOf(r)" :key="c._i"
                     :id="`ma-card-${ri}-${c._i}`" class="ma-card">
                  <div class="ma-card-head">
                    <span class="ma-card-no">#{{ c._i + 1 }}</span>
                    <span class="ma-card-src">{{ c.source }}</span>
                    <span class="ma-card-grade">{{ gradeLabel(c.grade) }}</span>
                    <span v-if="c.stance === 'fact'" class="ma-card-fact">事实</span>
                  </div>
                  <div class="ma-card-title">{{ c.title }}</div>
                  <p class="ma-card-sum">{{ c.summary }}</p>
                  <blockquote v-if="c.quote" class="ma-card-quote">{{ c.quote }}</blockquote>
                </div>
              </div>
            </div>

            <!-- 质控：冲突 + 裁定 -->
            <div v-if="r.conflicts.length || r.verdict" class="ma-qc">
              <div v-for="(cf, ci) in r.conflicts" :key="ci" class="ma-conflict">
                ⚠ 冲突：{{ typeof cf === 'string' ? cf : (cf.detail || cf.summary || cf.reason || '') }}
              </div>
              <div v-if="r.verdict" class="ma-verdict" :class="{ human: r.verdict.need_human }">
                裁定：{{ r.verdict.suggest_label }}
                <template v-if="fmtPct(r.verdict.confidence) != null">
                  （置信 {{ fmtPct(r.verdict.confidence) }}%）
                </template>
                <b v-if="r.verdict.need_human">· 建议人工复核</b>
                <span v-if="r.verdict.comment"> — {{ r.verdict.comment }}</span>
              </div>
            </div>
            </div>
          </div>

          <!-- 成果气泡：干净的最终结论（引用 chip 点击 → 定位素材卡） -->
          <div v-if="r.conclusion || (r.reviewing && !r.error)" class="ma-reply">
            <div class="ma-reply-head">
              <span class="ma-reply-team">{{ r.team || '智能体团队' }}</span>
              <span v-if="r.elapsed" class="ma-reply-ms">总耗时 {{ (r.elapsed / 1000).toFixed(1) }}s</span>
              <button v-if="!r.live" class="ma-mini" @click="rerunRound(r)">↻ 重新运行</button>
            </div>
            <div v-if="r.conclusion" class="ma-reply-body" @click="jumpCite">
              <div class="ma-answer-md" v-html="renderMd(r.conclusion, ri)" />
            </div>
            <p v-else class="ma-typing">团队协作中…</p>
          </div>
        </div>
      </div>
      <!-- 底部输入区（任务库/组队已上移至协作配置面板） -->
      <div class="ma-composer">
        <div class="ma-comp-main">
          <textarea v-model="taskInput" rows="2"
            :placeholder="selectedTask
              ? `已选任务「${selectedTask.name}」，补充具体问题后发送（留空则按模板原文执行）`
              : '输入任务，Enter 发送，Shift+Enter 换行'"
            @keydown.enter.exact.prevent="send" />
          <button class="ma-send" :disabled="reviewing || (!taskInput.trim() && !selectedTask)" @click="send">
            {{ reviewing ? '协作中…' : '发 送' }}
          </button>
        </div>
      </div>

      <!-- 任务编辑弹窗 -->
      <teleport to="body">
        <div v-if="taskForm.show" class="ma-modal-mask" @click.self="taskForm.show = false">
          <div class="ma-modal">
            <h3>{{ taskForm.id ? '编辑任务' : '新建任务' }}</h3>
            <label>名称<input v-model="taskForm.name" placeholder="如：竞品分析" /></label>
            <label>提示词模板
              <textarea v-model="taskForm.prompt" rows="5"
                        placeholder="可含 {question} 占位符，运行时替换为输入框里的问题" />
            </label>
            <div class="ma-modal-ops">
              <button class="ma-btn" @click="taskForm.show = false">取消</button>
              <button class="ma-btn primary" :disabled="savingTask" @click="saveTask">
                {{ savingTask ? '保存中…' : '保存' }}
              </button>
            </div>
          </div>
        </div>
      </teleport>
    </section>
  </div>
</template>

<style scoped>
/* 页面骨架：左协作历史 + 主聊天区（不滚，聊天区内部滚），对齐 AgentView 布局 */
.ma-page {
  display: flex; gap: 14px;
  height: calc(100dvh - 128px); min-height: 520px;
}
.ma-page > * { flex-shrink: 0; }
.ma-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ma-main > * { flex-shrink: 0; }

/* ── 左栏：协作历史 ── */
.ma-side { width: 230px; display: flex; flex-direction: column;
  border: 1px solid var(--c-border); border-radius: 14px;
  background: var(--c-panel); overflow: hidden; }
.ma-side-head { display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; font-size: 12px; font-weight: 700; color: var(--c-secondary);
  border-bottom: 1px solid var(--c-border); }
.ma-side-list { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
.ma-side-empty { font-size: 12px; color: var(--c-secondary); padding: 12px 6px; line-height: 1.6; }
.ma-sess { padding: 8px 10px; border: 1px solid transparent; border-radius: 10px; cursor: pointer; }
.ma-sess:hover { background: var(--c-muted); }
.ma-sess.active { background: var(--c-accent-weak); border-color: var(--c-accent); }
.ma-sess-title { font-size: 13px; color: var(--c-fg); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ma-sess-meta { display: flex; align-items: center; justify-content: space-between;
  margin-top: 3px; font-size: 11px; color: var(--c-secondary); }
.ma-sess-ops { display: none; gap: 4px; }
.ma-sess:hover .ma-sess-ops, .ma-sess.active .ma-sess-ops { display: inline-flex; }
.ma-sess-ops button { border: 0; background: none; cursor: pointer; color: var(--c-secondary);
  font-size: 12px; padding: 0 2px; }
.ma-sess-ops button:hover { color: var(--c-fg); }

/* ── 页头 ── */
.ma-head { display: flex; align-items: center; justify-content: space-between;
  padding: 2px 4px 10px; }
.ma-head h2 { margin: 0; font-size: 18px; color: var(--c-fg); }
.ma-head p { margin: 2px 0 0; font-size: 12px; color: var(--c-secondary); }
.ma-head-tag { font-size: 12px; color: var(--c-accent); background: var(--c-accent-weak);
  border: 1px solid var(--c-accent); border-radius: 999px; padding: 3px 10px; }

/* ── 聊天流 ── */
.ma-chat { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column;
  gap: 16px; padding: 12px 6px 12px 2px; }
.ma-error { margin: 0; padding: 8px 12px; border-radius: 10px; font-size: 13px;
  color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 10%, transparent); }

/* 协作配置面板（顶部常驻：阵容介绍 + 任务库 + 组队，可收起） */
.ma-setup { border: 1px solid var(--c-border); border-radius: 14px;
  background: var(--c-panel); flex-shrink: 0; }
.ma-setup-bar { width: 100%; display: flex; justify-content: space-between; align-items: center;
  padding: 8px 14px; background: none; border: none; cursor: pointer;
  font-size: 12.5px; color: var(--c-secondary); }
.ma-setup-bar:hover { color: var(--c-fg); }
.ma-setup-arrow { font-size: 11px; }
.ma-setup-body { display: flex; flex-direction: column; gap: 10px; padding: 2px 14px 14px; }
.ma-setup-agents { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 8px; }
.ma-wagent { position: relative; border: 1px solid var(--c-border); border-radius: 10px;
  padding: 7px 10px; background: var(--c-panel-elevated);
  display: flex; flex-direction: column; gap: 2px; }
.ma-wagent b { font-size: 12.5px; color: var(--c-fg); }
.ma-wagent span { font-size: 11.5px; color: var(--c-secondary); line-height: 1.5; }
.ma-wagent.opt b { color: var(--c-accent); }
/* 能力卡即勾选：可点、选中高亮 + ✓ 角标 */
.ma-wagent.pickable { cursor: pointer; transition: border-color 120ms, background 120ms; }
.ma-wagent.pickable:hover { border-color: var(--c-accent); }
.ma-wagent.picked { border-color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 7%, var(--c-panel-elevated)); }
.ma-pick-badge { position: absolute; top: 7px; right: 9px;
  font-size: 11px; font-weight: 700; color: var(--c-accent); }
.ma-setup-row { display: flex; gap: 10px; align-items: flex-start; }
.ma-setup-row .ma-comp-label { margin-top: 5px; }

/* 欢迎空状态 */
.ma-welcome { border: 1px dashed var(--c-border); border-radius: 16px;
  background: var(--c-panel); padding: 14px 22px; }
.ma-welcome h3 { margin: 0 0 4px; font-size: 16px; color: var(--c-fg); }
.ma-welcome p { margin: 0; font-size: 13px; color: var(--c-secondary); }

/* 每轮 */
.ma-turn { display: flex; flex-direction: column; gap: 8px; }
.ma-user-row { display: flex; justify-content: flex-end; }
.ma-user-bubble { max-width: 78%; background: var(--c-accent); color: var(--c-panel-elevated);
  border-radius: 14px 14px 4px 14px; padding: 9px 14px; font-size: 14px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word; }

/* ── 执行过程面板（收起 = 一行状态摘要，展开 = 全过程） ── */
.ma-proc { display: flex; flex-direction: column; min-width: 0; }
.ma-proc-bar { display: flex; align-items: center; gap: 8px; width: fit-content; max-width: 100%;
  border: 1px solid var(--c-border); background: var(--c-panel); border-radius: 10px;
  padding: 6px 12px; cursor: pointer; text-align: left; }
.ma-proc-bar:hover { background: var(--c-muted); }
.ma-proc-dot { width: 18px; height: 18px; border-radius: 50%; display: inline-flex;
  align-items: center; justify-content: center; font-size: 11px; flex-shrink: 0;
  color: var(--c-bg); background: var(--c-success); }
.ma-proc-dot.live { background: var(--c-accent); animation: ma-blink 1.1s infinite; }
.ma-proc-dot.err { background: var(--c-danger); }
.ma-proc-sum { font-size: 12.5px; color: var(--c-fg); font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ma-proc-cur { font-size: 12px; color: var(--c-secondary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ma-proc-arrow { font-size: 11px; color: var(--c-secondary); flex-shrink: 0; }
.ma-proc-body { margin: 8px 0 0 9px; padding: 2px 0 2px 16px;
  border-left: 2px solid var(--c-border); display: flex; flex-direction: column;
  gap: 12px; min-width: 0; }
.ma-mini { border: 1px solid var(--c-border); background: var(--c-panel-elevated);
  color: var(--c-secondary); font-size: 11.5px; border-radius: 8px; padding: 2px 8px;
  cursor: pointer; white-space: nowrap; }
.ma-mini:hover { color: var(--c-fg); border-color: var(--c-secondary); }

/* 计划 */
.ma-plan { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 3px; }
.ma-plan li { font-size: 12.5px; color: var(--c-fg); line-height: 1.5; }

/* 节点时间线 */
.ma-nodes { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.ma-nodes li { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; }
.ma-dot { width: 16px; text-align: center; color: var(--c-accent); flex-shrink: 0; }
.ma-nodes li.running .ma-dot { color: var(--c-secondary); animation: ma-blink 1s infinite; }
.ma-node-name { font-weight: 600; color: var(--c-fg); flex-shrink: 0; }
.ma-node-ms { font-size: 11.5px; color: var(--c-accent); flex-shrink: 0; }
.ma-node-sum { color: var(--c-secondary); font-size: 12px; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@keyframes ma-blink { 50% { opacity: 0.25; } }

/* ── 共享黑板素材 ── */
.ma-ev { display: flex; flex-direction: column; gap: 8px; }
.ma-ev-title { font-size: 11.5px; font-weight: 700; color: var(--c-secondary); }
.ma-ev-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.ma-ev-tabs button { border: 1px solid var(--c-border); background: none; color: var(--c-secondary);
  font-size: 12px; border-radius: 999px; padding: 2px 10px; cursor: pointer; }
.ma-ev-tabs button.on { color: var(--c-accent); border-color: var(--c-accent);
  background: var(--c-accent-weak); }
.ma-ev-list { display: flex; flex-direction: column; gap: 6px; }
.ma-card { border: 1px solid var(--c-border); border-radius: 10px; padding: 8px 10px;
  background: var(--c-panel-elevated); display: flex; flex-direction: column; gap: 4px; }
.ma-card.is-flash { animation: ma-flash 1.5s ease; }
@keyframes ma-flash { 0%, 55% { border-color: var(--c-accent); box-shadow: 0 0 0 3px var(--c-accent-weak); }
  100% { border-color: var(--c-border); box-shadow: none; } }
.ma-card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ma-card-no { font-size: 11px; font-weight: 700; color: var(--c-accent); }
.ma-card-src { font-size: 11.5px; color: var(--c-secondary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ma-card-grade, .ma-card-fact { font-size: 10.5px; padding: 0 6px; border-radius: 999px;
  border: 1px solid var(--c-border); color: var(--c-secondary); flex-shrink: 0; }
.ma-card-fact { color: var(--c-success); border-color: var(--c-success); }
.ma-card-title { font-size: 12.5px; font-weight: 600; color: var(--c-fg); line-height: 1.4; }
.ma-card-sum { margin: 0; font-size: 12px; color: var(--c-secondary); line-height: 1.55; }
.ma-card-quote { margin: 0; padding: 5px 9px; border-left: 3px solid var(--c-accent);
  background: var(--c-muted); border-radius: 0 8px 8px 0;
  font-size: 11.5px; color: var(--c-fg); line-height: 1.5; }

/* ── 质控 ── */
.ma-qc { display: flex; flex-direction: column; gap: 5px; }
.ma-conflict { font-size: 12px; color: var(--c-danger); line-height: 1.5; }
.ma-verdict { font-size: 12.5px; color: var(--c-fg); background: var(--c-muted);
  border-radius: 8px; padding: 6px 10px; line-height: 1.5; }
.ma-verdict.human { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 8%, transparent); }

/* ── 成果气泡（干净结论；markdown 由 v-html 注入，须 :deep） ── */
.ma-reply { max-width: 88%; border: 1px solid var(--c-border); border-radius: 4px 14px 14px 14px;
  background: var(--c-panel-elevated); padding: 10px 14px 12px;
  display: flex; flex-direction: column; gap: 6px; }
.ma-reply-head { display: flex; align-items: center; gap: 10px; }
.ma-reply-team { font-size: 12px; font-weight: 700; color: var(--c-accent); }
.ma-reply-ms { font-size: 11.5px; color: var(--c-secondary); }
.ma-reply-head .ma-mini { margin-left: auto; opacity: 0; transition: opacity 0.15s; }
.ma-reply:hover .ma-mini { opacity: 1; }
.ma-answer-md { font-size: 14px; color: var(--c-fg); line-height: 1.7; }
.ma-answer-md :deep(h1), .ma-answer-md :deep(h2), .ma-answer-md :deep(h3) {
  margin: 12px 0 6px; font-size: 15px; }
.ma-answer-md :deep(p) { margin: 6px 0; }
.ma-answer-md :deep(ul), .ma-answer-md :deep(ol) { margin: 6px 0; padding-left: 20px; }
.ma-answer-md :deep(table) { border-collapse: collapse; margin: 8px 0; }
.ma-answer-md :deep(th), .ma-answer-md :deep(td) { border: 1px solid var(--c-border);
  padding: 4px 8px; font-size: 12.5px; }
.ma-answer-md :deep(code) { background: var(--c-muted); border-radius: 4px; padding: 1px 5px; font-size: 12.5px; }
.ma-answer-md :deep(blockquote) { margin: 6px 0; padding: 4px 10px; border-left: 3px solid var(--c-accent);
  background: var(--c-muted); border-radius: 0 8px 8px 0; color: var(--c-secondary); }
.ma-answer-md :deep(.ma-cite) { cursor: pointer; color: var(--c-accent); font-weight: 700;
  padding: 0 2px; }
.ma-answer-md :deep(.ma-cite:hover) { text-decoration: underline; }
.ma-answer-md :deep(.ma-cite.is-missing) { color: var(--c-secondary); cursor: default; }
.ma-typing { margin: 0; font-size: 13px; color: var(--c-secondary); animation: ma-blink 1.2s infinite; }

/* ── 底部输入区 ── */
.ma-composer { border: 1px solid var(--c-border); border-radius: 16px;
  background: var(--c-panel); padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.ma-comp-row { display: flex; align-items: flex-start; gap: 8px; }
.ma-comp-label { font-size: 11.5px; color: var(--c-secondary); flex-shrink: 0;
  padding-top: 4px; width: 34px; }
.ma-task-chips { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.ma-task-chip { display: inline-flex; align-items: center; border: 1px solid var(--c-border);
  border-radius: 999px; overflow: hidden; background: var(--c-panel-elevated); }
.ma-task-chip.on { border-color: var(--c-accent); background: var(--c-accent-weak); }
.ma-task-chip .chip-main { border: 0; background: none; color: var(--c-fg); font-size: 12px;
  padding: 3px 10px; cursor: pointer; max-width: 220px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.ma-task-chip .chip-ico { border: 0; background: none; color: var(--c-secondary); font-size: 10px;
  padding: 3px 5px; cursor: pointer; }
.ma-task-chip .chip-ico:hover { color: var(--c-fg); }
.ma-task-chip .chip-ico.danger:hover { color: var(--c-danger); }
.ma-pick { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; flex: 1; min-width: 0; }
.ma-pick label { font-size: 12.5px; color: var(--c-fg); display: inline-flex;
  align-items: center; gap: 4px; cursor: pointer; }
.ma-agent-chip { border: 1px solid var(--c-border); background: none; color: var(--c-secondary);
  font-size: 12px; border-radius: 999px; padding: 2px 10px; cursor: pointer; }
.ma-agent-chip.on { color: var(--c-accent); border-color: var(--c-accent); background: var(--c-accent-weak); }
.ma-pick-hint { flex-basis: 100%; font-size: 11.5px; color: var(--c-secondary); }
.ma-comp-main { display: flex; align-items: flex-end; gap: 10px; }
.ma-comp-main textarea { flex: 1; resize: none; border: 1px solid var(--c-border); border-radius: 10px;
  background: var(--c-panel-elevated); color: var(--c-fg); font-size: 14px; padding: 9px 12px;
  line-height: 1.5; font-family: inherit; }
.ma-comp-main textarea:focus { outline: none; border-color: var(--c-accent); }
.ma-send { border: 0; border-radius: 10px; background: var(--c-btn-primary-bg);
  color: var(--c-bg); font-size: 14px; font-weight: 700; padding: 10px 22px; cursor: pointer; }
.ma-send:disabled { opacity: 0.45; cursor: not-allowed; }

/* ── 任务编辑弹窗 ── */
.ma-modal-mask { position: fixed; inset: 0; background: var(--c-overlay);
  display: flex; align-items: center; justify-content: center; z-index: 60; }
.ma-modal { width: 460px; max-width: 92vw; background: var(--c-panel-elevated);
  border: 1px solid var(--c-border); border-radius: 16px; padding: 18px;
  display: flex; flex-direction: column; gap: 12px; box-shadow: 0 18px 40px rgba(0, 0, 0, 0.18); }
.ma-modal h3 { margin: 0; font-size: 15px; color: var(--c-fg); }
.ma-modal label { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--c-secondary); }
.ma-modal input, .ma-modal textarea { border: 1px solid var(--c-border); border-radius: 8px;
  background: var(--c-panel); color: var(--c-fg); font-size: 13px; padding: 8px 10px;
  font-family: inherit; resize: vertical; }
.ma-modal input:focus, .ma-modal textarea:focus { outline: none; border-color: var(--c-accent); }
.ma-modal-ops { display: flex; justify-content: flex-end; gap: 8px; }
.ma-btn { border: 1px solid var(--c-border); background: none; color: var(--c-fg);
  font-size: 13px; border-radius: 8px; padding: 7px 16px; cursor: pointer; }
.ma-btn.primary { border: 0; background: var(--c-btn-primary-bg); color: var(--c-bg); font-weight: 700; }
.ma-btn:disabled { opacity: 0.5; cursor: not-allowed; }

@media (max-width: 900px) {
  .ma-side { width: 180px; }
  .ma-user-bubble { max-width: 92%; }
}
</style>
