<script setup>
import { ref, computed, onMounted, onActivated, onBeforeUnmount, watch, reactive, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { marked } from 'marked'
import { fetchKbs, queryAgentStream, fetchAgentSkills, fetchAgents, updateAgent, fetchChatSessions, fetchSessionMessages, renameChatSession, deleteChatSession } from '../api'
import { useToast } from '../composables/useToast'
import PreviewModal from './PreviewModal.vue'

const router = useRouter()
const toast = useToast()
const kbs = ref([])
const queryKbId = ref('')

// ---------- 技能 ----------
const allSkills = ref([])
const selectedSkillIds = ref([])
const activeSkills = ref([])     // SSE 实际生效的技能

const enabledSkills = computed(() => allSkills.value.filter(s => s.is_enabled))
async function loadSkills() {
  try { allSkills.value = await fetchAgentSkills() } catch {}
}

// ---------- L2 工具循环（agent loop + tools） ----------
const useTools = ref(false)   // 开关：本轮问答允许 LLM 自主调用平台工具（台账/图谱/全库检索）
const toolCalls = ref([])     // 本轮工具调用记录 [{name, arguments, ok, duration_ms, error}]

// ---------- 智能体（下拉只列自定义；「系统默认」= 内置 agent_default） ----------
const agents = ref([])
const selectedAgentId = ref('')
// 内置「系统默认」智能体（后端 seed，不可删除；配置可在智能体配置页修改）
const defaultAgentId = computed(() => agents.value.find(a => a.is_preset)?.id || '')
// 下拉只展示自定义且启用的智能体；「系统默认」作为首选项单独渲染
const enabledAgents = computed(() => agents.value.filter(a => a.is_enabled && !a.is_preset))
const selectedAgent = computed(() => agents.value.find(x => x.id === selectedAgentId.value))
// 选中自定义智能体：KB/技能/人设完全由智能体配置决定，页面不再重复选择；
// 「系统默认」例外：保持页面 KB/技能选择器（其未绑定的项回退页面选择）
const agentPresetActive = computed(() => !!selectedAgent.value && selectedAgentId.value !== defaultAgentId.value)
// agents 异步加载完成后，把默认选中值锚到内置智能体（避免 select 与 option 不匹配）
watch(defaultAgentId, (id) => { if (id && !selectedAgentId.value) selectedAgentId.value = id }, { immediate: true })
// 自定义智能体：切换时预填 kb+技能；「系统默认」kb 跟随页面选择，技能跟随自身配置
function onAgentChange() {
  const a = selectedAgent.value
  // 仅在智能体绑定的 KB 仍存在时预填；KB 已被删除则保持页面当前选择
  if (a && a.kb_id && kbs.value.some(k => k.id === a.kb_id)) {
    queryKbId.value = a.kb_id
  }
  applyAgentSkills(a)
  // 会话按智能体过滤，切换后重载
  sessionId.value = ''
  loadSessions()
}
// 技能预填 = 选中智能体绑定的 skill_ids（与配置页 editForm.skill_ids 同一份数据；未绑定 = 全不选）
function applyAgentSkills(a) {
  selectedSkillIds.value = [...(a?.skill_ids || [])]
}

// ---------- 问答页技能编辑：勾选即同步写回智能体配置 ----------
const syncingSkills = ref(false)
function toggleSkill(id) {
  if (syncingSkills.value) return
  selectedSkillIds.value = selectedSkillIds.value.includes(id)
    ? selectedSkillIds.value.filter(x => x !== id)
    : [...selectedSkillIds.value, id]
  syncSkillsToAgent()
}
async function syncSkillsToAgent() {
  if (!selectedAgentId.value) return
  syncingSkills.value = true
  try {
    const updated = await updateAgent(selectedAgentId.value, { skillIds: [...selectedSkillIds.value] })
    // 同步本地缓存，保持「已使用 N 项技能」等展示即时正确
    const idx = agents.value.findIndex(x => x.id === selectedAgentId.value)
    if (idx !== -1 && updated) {
      agents.value[idx] = { ...agents.value[idx], skill_ids: updated.skill_ids ?? [...selectedSkillIds.value] }
    }
    toast.success('技能已同步至智能体配置')
  } catch (e) {
    toast.error(`技能同步失败：${e.message}`)
  } finally {
    syncingSkills.value = false
  }
}
async function loadAgents() {
  try { agents.value = await fetchAgents() } catch {}
}

// ---------- 会话（短期记忆：多轮上下文 + 历史回放，doc/智能体/智能体会话_功能设计.md） ----------
const sessionId = ref('')
const sessions = ref([])
const sessionsOpen = ref(false)
const sessionSelectRef = ref(null)
const historyTurns = ref([])   // 当前会话已完成的历史轮次 [{q, answer}]
const currentQ = ref('')       // 正在富面板展示的最近一轮问题

const currentSession = computed(() => sessions.value.find(s => s.id === sessionId.value) || null)
const currentSessionLabel = computed(() => currentSession.value?.title || '历史会话')

function resetTurnPanel() {
  answerRaw.value = ''
  chunks.value = []
  entities.value = []
  subgraph.value = null
  factsExpanded.value = false
  activeSkills.value = []
  thinkBlocks.value = []
  liveThink.value = ''
  liveThinkDone.value = false
  liveThinkExpanded.value = false
  thinkExpanded.value = false
  reasonOpen.value = true
  hoveredChunk.value = null
  toolCalls.value = []
  Object.keys(expandedSources).forEach(k => delete expandedSources[k])
}

const queryInputRef = ref(null)

// ---------- 会话流滚动：固定高度内部滚动，输入区常驻底部 ----------
const chatScrollRef = ref(null)
function scrollChatToBottom(force = false) {
  nextTick(() => {
    const el = chatScrollRef.value
    if (!el) return
    // 非强制时仅在用户处于底部附近才跟随（避免打断向上翻阅）
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 140
    if (force || nearBottom) el.scrollTop = el.scrollHeight
  })
}

function startNewSession() {
  const hadSession = !!sessionId.value
  sessionId.value = ''
  historyTurns.value = []
  currentQ.value = ''
  resetTurnPanel()
  queryText.value = ''
  // 新会话是「懒创建」：提问时才真正建会话，这里给出明确反馈避免像没响应
  toast.info(hadSession ? '已开始新会话' : '已是新会话，提问后自动保存')
  nextTick(() => queryInputRef.value?.focus())
}

async function loadSessions() {
  try { sessions.value = await fetchChatSessions(selectedAgentId.value || null) } catch {}
}

async function selectSession(s) {
  if (querying.value) return
  sessionsOpen.value = false
  if (s.id === sessionId.value) return
  sessionId.value = s.id
  historyTurns.value = []
  currentQ.value = ''
  resetTurnPanel()
  try {
    const data = await fetchSessionMessages(s.id)
    const msgs = data.messages || []
    // 消息两两折叠为「一问一答」轮次
    for (let i = 0; i + 1 < msgs.length; i += 2) {
      if (msgs[i].role === 'user' && msgs[i + 1].role === 'assistant') {
        historyTurns.value.push({ q: msgs[i].content, answer: msgs[i + 1].content })
      }
    }
    // 历史回放定位到最新一轮
    scrollChatToBottom(true)
  } catch {}
}

async function renameSession(s) {
  const t = prompt('重命名会话', s.title || '')
  if (t === null) return
  try { await renameChatSession(s.id, t); loadSessions() } catch {}
}

async function removeSession(s) {
  if (!confirm(`删除会话「${s.title || '未命名会话'}」？`)) return
  try { await deleteChatSession(s.id) } catch {}
  if (s.id === sessionId.value) startNewSession()
  loadSessions()
}

watch(selectedAgentId, () => { startNewSession(); loadSessions() })

const queryText = ref('')
const querying = ref(false)
const answerRaw = ref('')
const chunks = ref([])
const entities = ref([])        // 识别到的种子实体
const subgraph = ref(null)      // {facts, entities, relations, retrieval_path}
const reasonOpen = ref(true)

const kbSelectRef = ref(null)
const kbDropdownOpen = ref(false)

const queryKbList = computed(() => kbs.value.filter(kb => kb.file_count > 0))
const selectedKb = computed(() => queryKbList.value.find(kb => kb.id === queryKbId.value) || null)
// 空选择 = 全局模式：不做 KB 语料检索，靠工具循环全局取数（台账/图谱/全库）
const selectedKbLabel = computed(() => selectedKb.value
  ? `${selectedKb.value.name} (${selectedKb.value.file_count} 个文件)`
  : '全局模式 · 不限知识库')

const hasReasoning = computed(() => (entities.value.length > 0) || (subgraph.value && (subgraph.value.relations?.length || subgraph.value.entities?.length)) || activeSkills.value.length > 0)
const isDegraded = computed(() => !!subgraph.value?.retrieval_path?.degraded)
const facts = computed(() => subgraph.value?.facts || '')
const factRelations = computed(() => subgraph.value?.relations || [])
const FACT_PREVIEW_COUNT = 8
const factsExpanded = ref(false)
const visibleFacts = computed(() => factsExpanded.value ? factRelations.value : factRelations.value.slice(0, FACT_PREVIEW_COUNT))
const pathInfo = computed(() => subgraph.value?.retrieval_path || {})
const pipelineSteps = computed(() => pathInfo.value.steps || [])

// 实体来源标识 → 中文（chip 提示与导出共用）
const SOURCE_LABEL = { lexical: '词面匹配', mention: '分片反查', 'lexical+mention': '词面+分片反查' }
function sourceLabel(s) { return SOURCE_LABEL[s] || s || '' }

// ---------- 导出检索流程（Markdown 下载） ----------
function exportPipeline() {
  const lines = []
  const now = new Date()
  const p2 = n => String(n).padStart(2, '0')
  const ts = `${now.getFullYear()}-${p2(now.getMonth() + 1)}-${p2(now.getDate())} ${p2(now.getHours())}:${p2(now.getMinutes())}:${p2(now.getSeconds())}`
  lines.push('# 智能体问答 · 检索流程导出', '')
  lines.push(`- 时间：${ts}`)
  if (currentQ.value) lines.push(`- 问题：${currentQ.value}`)
  lines.push(`- 智能体：${selectedAgent.value?.name || '系统默认'}`)
  if (selectedKb.value) lines.push(`- 知识库：${selectedKb.value.name}`)
  if (activeSkills.value.length) lines.push(`- 已加载技能：${activeSkills.value.map(s => s.name).join('、')}`)
  lines.push('')
  if (pipelineSteps.value.length) {
    lines.push('## 检索流程', '')
    pipelineSteps.value.forEach((s, i) => {
      lines.push(`${i + 1}. **${s.name}**：命中 ${s.count} ${s.unit || ''}`)
      if (s.detail) lines.push(`   - ${s.detail}`)
    })
    lines.push('')
  }
  if (entities.value.length) {
    lines.push('## 识别实体', '')
    entities.value.forEach(e => {
      const src = sourceLabel(e.source)
      lines.push(`- ${e.type ? `[${e.type}] ` : ''}${e.name}${src ? `（来源：${src}）` : ''}`)
    })
    lines.push('')
  }
  if (factRelations.value.length) {
    lines.push(`## 图谱事实（${factRelations.value.length} 条）`, '')
    factRelations.value.forEach(r => lines.push(`- ${r.source_name} ─ ${r.relation_type} → ${r.target_name}`))
    lines.push('')
  }
  if (chunks.value.length) {
    lines.push(`## 引用来源（${chunks.value.length} 条）`, '')
    chunks.value.forEach((c, i) => {
      const score = c.score != null ? `，相似度 ${Math.round(c.score * 100)}%` : ''
      lines.push(`${i + 1}. ${c.file_name}（${retrievalMeta(c).label}${score}）`)
    })
    lines.push('')
  }
  if (answerRaw.value) {
    lines.push('## 回答', '', answerRaw.value, '')
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `agent-pipeline-${now.getFullYear()}${p2(now.getMonth() + 1)}${p2(now.getDate())}-${p2(now.getHours())}${p2(now.getMinutes())}${p2(now.getSeconds())}.md`
  a.click()
  URL.revokeObjectURL(url)
}

function renderMd(text) {
  if (!text) return ''
  return marked.parse(text)
}

// ---------- 来源标记 ----------
const RETRIEVAL_META = {
  vector: { label: '向量', color: '#6366f1' },
  bm25: { label: '关键词', color: '#f59e0b' },
  graph: { label: '图谱', color: '#10b981' },
  both: { label: '交集', color: '#8b5cf6' },
  'vector+bm25': { label: '向量+关键词', color: '#8b7cf6' },
  'vector+graph': { label: '向量+图谱', color: '#3a9f8f' },
  'bm25+graph': { label: '关键词+图谱', color: '#cba34a' },
  'vector+bm25+graph': { label: '三路命中', color: '#7c5cf0' },
}
function retrievalMeta(c) { return RETRIEVAL_META[c.retrieval] || RETRIEVAL_META.vector }

// ---------- 实时思考（reasoning 事件流式显示） ----------
const liveThink = ref('')
const liveThinkDone = ref(false)
const liveThinkExpanded = ref(false)
const liveThinkBoxRef = ref(null)
const liveThinkHtml = computed(() => renderMd(liveThink.value))
watch(liveThink, () => {
  if (liveThinkDone.value) return
  nextTick(() => {
    const el = liveThinkBoxRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
})

// ---------- 思考过程（兼容 <think>） ----------
const thinkBlocks = ref([])
const answerExThink = computed(() => {
  let s = answerRaw.value
  thinkBlocks.value = []
  const re = /<think>([\s\S]*?)<\/think>/g
  const blocks = []
  let m
  while ((m = re.exec(s)) !== null) blocks.push(m[1].trim())
  if (blocks.length) {
    thinkBlocks.value = blocks.map(c => ({ content: c }))
    s = s.replace(/<think>[\s\S]*?<\/think>/g, '').trim()
  }
  return s
})
const thinkExpanded = ref(false)

// ---------- 分片展开 ----------
const expandedSources = reactive({})
const hoveredChunk = ref(null)

// ---------- PDF 预览 ----------
const previewVisible = ref(false)
const previewFileId = ref('')
const previewFileName = ref('')
const previewFileExt = ref('')
const previewPageNumber = ref(1)
const previewStartOffset = ref(0)
const previewEndOffset = ref(0)
const previewChunkText = ref('')

// ---------- 回答渲染：替换 [来源N] 与 [事实] ----------
const processedAnswerHtml = computed(() => {
  let text = answerExThink.value
  if (!text) return ''
  // 保护代码块
  text = text.replace(/(```[\s\S]*?```|`[^`]*`)/g, m =>
    m.replace(/\[来源(\d+)\]/g, '\x00CITE$1\x00').replace(/\[事实\]/g, '\x00FACT\x00'))
  // [来源N]
  text = text.replace(/\[来源(\d+)\]/g, (_, num) => {
    const idx = parseInt(num) - 1
    const c = chunks.value[idx]
    const color = c ? retrievalMeta(c).color : '#6366f1'
    return `<span class="cite-ref" data-chunk="${num}" style="--c:${color}">[${num}]</span>`
  })
  // [事实]
  text = text.replace(/\[事实\]/g, '<span class="cite-fact">事实</span>')
  // 恢复代码块
  text = text.replace(/\x00CITE(\d+)\x00/g, '[来源$1]').replace(/\x00FACT\x00/g, '[事实]')
  return renderMd(text)
})

function onAnswerClick(e) {
  const cite = e.target.closest('[data-chunk]')
  if (!cite) return
  const num = +cite.dataset.chunk
  expandedSources[num - 1] = true
  hoveredChunk.value = num
  nextTick(() => {
    const el = document.getElementById(`src-${num}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })
  setTimeout(() => { hoveredChunk.value = null }, 1500)
}
function onAnswerHover(e) {
  const cite = e.target.closest('[data-chunk]')
  hoveredChunk.value = cite ? +cite.dataset.chunk : null
}
function onSourceClick(idx) {
  const num = idx + 1
  expandedSources[idx] = !expandedSources[idx]
  hoveredChunk.value = num
  nextTick(() => {
    const el = document.getElementById(`src-${num}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    const c = document.querySelector(`.answer-text [data-chunk="${num}"]`)
    if (c) c.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
  setTimeout(() => { hoveredChunk.value = null }, 1500)
}
function onSourceDblClick(c) {
  if (!c.file_id) return
  previewFileId.value = c.file_id
  previewFileName.value = c.file_name
  previewFileExt.value = c.file_ext || ''
  previewPageNumber.value = c.page_number || 1
  previewStartOffset.value = c.start_offset || 0
  previewEndOffset.value = c.end_offset || 0
  previewChunkText.value = c.text || ''
  previewVisible.value = true
}
function gotoEntity(id) {
  if (id) router.push(`/entities/${id}`)
}

function pct(c) { return c.score == null ? null : Math.round(c.score * 100) }
function pctBg(idx, score) {
  if (score == null) return { background: 'var(--c-muted)', color: 'var(--c-secondary)' }
  const t = Math.max(0.1, Math.min(1, score))
  const total = Math.max(chunks.value.length - 1, 1)
  const i = Math.min(idx / total, 1)
  const baseL = 60 + i * 24
  const l = Math.min(baseL + (1 - t) * 18, 90)
  return { background: `hsl(244 72% ${l}%)`, color: l > 75 ? 'hsl(244 72% 35%)' : '#fff' }
}

async function loadKbs() {
  try { kbs.value = await fetchKbs() } catch {}
}

async function runQuery() {
  const q = queryText.value.trim()
  // 全局模式：不选 KB 也可提问（无语料检索，靠人设+技能+工具循环全局取数）
  if (!q) return
  // 上一轮已完整回答：滚入历史气泡，让本轮独占富面板
  if (currentQ.value && answerRaw.value) {
    historyTurns.value = [...historyTurns.value, { q: currentQ.value, answer: answerExThink.value || answerRaw.value }]
  }
  currentQ.value = q
  resetTurnPanel()
  querying.value = true
  scrollChatToBottom(true)
  try {
    await queryAgentStream(queryKbId.value, q, {
      skillIds: selectedSkillIds.value,
      agentId: selectedAgentId.value || null,
      sessionId: sessionId.value || null,
      useTools: useTools.value,
      onToolEvent(data) {
        // 过程事件即时上屏；tool_calls 汇总（含权威记录）到达后整体替换
        if (data.type === 'tool_call') {
          toolCalls.value.push({ name: data.name, arguments: data.arguments, ok: null })
        } else if (data.type === 'tool_result') {
          const last = [...toolCalls.value].reverse().find(c => c.name === data.name && c.ok === null)
          if (last) {
            last.ok = !!data.ok
            last.duration_ms = data.duration_ms
            if (!data.ok && data.summary) last.error = data.summary
          }
        } else if (data.type === 'tool_calls') {
          toolCalls.value = (data.calls || []).map(c => ({ ...c }))
        }
      },
      onSession(data) {
        // 新建会话时后端回传 session_id：锚定后续轮次并刷新会话列表
        if (data?.session_id && data.session_id !== sessionId.value) {
          sessionId.value = data.session_id
          loadSessions()
        }
      },
      onSkills(data) { activeSkills.value = data || [] },
      onEntities(data) { entities.value = data || [] },
      onSubgraph(data) { subgraph.value = data },
      onChunks(data) { chunks.value = data || [] },
      onReasoning(piece) { liveThink.value += piece; scrollChatToBottom() },
      onToken(token) {
        // 正文首个 token 到达 → 思考结束，自动折叠实时思考
        if (!answerRaw.value && liveThink.value && !liveThinkDone.value) liveThinkDone.value = true
        answerRaw.value += token
        scrollChatToBottom()
      },
    })
  } catch (err) {
    answerRaw.value = `错误: ${err.message}`
  }
  querying.value = false
}

function toggleKbDropdown() { kbDropdownOpen.value = !kbDropdownOpen.value }
function selectKb(kbId) { queryKbId.value = kbId; kbDropdownOpen.value = false }
function onWindowPointerDown(e) {
  if (kbSelectRef.value && !kbSelectRef.value.contains(e.target)) kbDropdownOpen.value = false
  if (sessionSelectRef.value && !sessionSelectRef.value.contains(e.target)) sessionsOpen.value = false
}

onMounted(loadKbs)
// 技能与智能体都加载完成后按选中智能体预填技能勾选
onMounted(bootstrapSkills)
onMounted(loadSessions)
// keepAlive 缓存页：从配置页/技能页返回时重新拉取，保证两处技能选择同步（同一份数据）
let skillsPageActivated = false
onActivated(() => {
  if (!skillsPageActivated) { skillsPageActivated = true; return }  // 首次激活与 onMounted 重合，跳过
  bootstrapSkills()
})
async function bootstrapSkills() {
  await Promise.all([loadSkills(), loadAgents()])
  applyAgentSkills(selectedAgent.value)
}
onMounted(() => window.addEventListener('pointerdown', onWindowPointerDown))
onBeforeUnmount(() => window.removeEventListener('pointerdown', onWindowPointerDown))
</script>

<template>
  <div class="agent-section">
    <!-- 紧凑配置区：智能体 + 技能同一行（页面名顶栏已有，不重复大标题） -->
    <div class="cfg-row">
      <template v-if="enabledAgents.length">
        <span class="cfg-label">智能体</span>
        <select class="cfg-select" v-model="selectedAgentId" @change="onAgentChange">
          <option :value="defaultAgentId">系统默认</option>
          <option v-for="a in enabledAgents" :key="a.id" :value="a.id">{{ a.name }}{{ a.kb_name ? ' · ' + a.kb_name : '' }}</option>
        </select>
      </template>
      <span class="cfg-label">技能</span>
      <div class="skill-chips-editor">
        <button v-for="s in enabledSkills" :key="s.id" type="button"
          class="skill-chip" :class="{ active: selectedSkillIds.includes(s.id) }"
          :title="s.description || s.name"
          @click="toggleSkill(s.id)">
          <span class="chip-ic" v-if="selectedSkillIds.includes(s.id)">✓</span>
          <span class="chip-ic" v-else>+</span>
          {{ s.name }}
        </button>
        <span v-if="!enabledSkills.length" class="skill-empty">暂无启用技能，可在「智能体技能」页启用</span>
      </div>
      <button type="button" class="skill-chip" :class="{ active: useTools }"
        title="开启后 LLM 可自主调用平台工具（台账统计 / 图谱 / 全库检索）补充回答"
        @click="useTools = !useTools">
        <span class="chip-ic" v-if="useTools">✓</span>
        <span class="chip-ic" v-else>⚡</span>
        工具调用
      </button>
    </div>
    <div class="cfg-sub">
      <span class="agent-pick-hint">技能与「{{ agentPresetActive ? selectedAgent.name : '系统默认' }}」配置实时同步（同一份数据）</span>
      <span v-if="agentPresetActive" class="cfg-note">
        已使用「{{ selectedAgent.name }}」配置：{{ selectedAgent.kb_name ? `知识库 ${selectedAgent.kb_name}` : '未绑定知识库' }} · 人设由智能体提供 ·
        <router-link to="/agent/configs">去修改</router-link>
      </span>
    </div>

    <!-- 知识库：仅在未选自定义智能体时需要选择 -->
    <div class="cfg-row" v-if="!agentPresetActive">
      <span class="cfg-label">知识库</span>
      <div class="kb-picker" ref="kbSelectRef">
        <button type="button" class="field-shell select-shell select-trigger" :class="{ open: kbDropdownOpen }" @click="toggleKbDropdown">
          <span class="select-value" :class="{ placeholder: !selectedKb }">{{ selectedKbLabel }}</span>
          <span class="field-caret" aria-hidden="true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          </span>
        </button>
        <div v-if="kbDropdownOpen" class="kb-dropdown">
          <button type="button" class="kb-option kb-option-placeholder" :class="{ active: !queryKbId }" @click="selectKb('')">
            <span class="kb-option-name">全局模式 · 不限知识库</span>
            <span class="kb-option-meta">工具取数不限库</span>
          </button>
          <button v-for="kb in queryKbList" :key="kb.id" type="button" class="kb-option" :class="{ active: kb.id === queryKbId }" @click="selectKb(kb.id)">
            <span class="kb-option-name">{{ kb.name }}</span>
            <span class="kb-option-meta">{{ kb.file_count }} 个文件</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 会话流：占满剩余高度、内部滚动（右侧滚动条）；输入区固定在下方 -->
    <div class="chat-scroll" ref="chatScrollRef">
      <!-- 会话历史（短期记忆回放：已完成轮次的简洁气泡；最近一轮见下方富面板） -->
      <div class="chat-turns" v-if="historyTurns.length || currentQ">
      <template v-for="(t, i) in historyTurns" :key="`h${i}`">
        <div class="chat-q">{{ t.q }}</div>
        <div class="chat-a markdown-body" v-html="renderMd(t.answer)"></div>
      </template>
      <div class="chat-q" id="chat-current-q" v-if="currentQ">{{ currentQ }}</div>
    </div>

    <div class="results" v-if="answerRaw || chunks.length || hasReasoning">
      <!-- 推理过程 -->
      <div class="reason-card" v-if="subgraph">
        <div class="reason-toggle" @click="reasonOpen = !reasonOpen">
          <svg class="reason-icon" :class="{ open: reasonOpen }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          <span>推理过程</span>
          <span class="reason-path">
            <span class="rp" v-if="pathInfo.vector != null">向量 {{ pathInfo.vector }}</span>
            <span class="rp" v-if="pathInfo.bm25 != null">关键词 {{ pathInfo.bm25 }}</span>
            <span class="rp" v-if="pathInfo.graph != null">图谱 {{ pathInfo.graph }}</span>
            <span class="rp rp-both" v-if="pathInfo.both">交集 {{ pathInfo.both }}</span>
            <span class="rp rp-deg" v-if="isDegraded">向量模式（未识别到图谱实体）</span>
          </span>
          <button type="button" class="export-btn" title="导出检索流程、识别实体、引用来源与回答全文为 Markdown" @click.stop="exportPipeline">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            导出流程
          </button>
        </div>
        <div class="reason-body" v-show="reasonOpen">
          <!-- 检索流程 -->
          <div class="reason-block" v-if="pipelineSteps.length">
            <div class="reason-label">检索流程</div>
            <ol class="pipeline-list">
              <li v-for="(st, i) in pipelineSteps" :key="st.key" class="pipeline-item">
                <span class="pl-idx">{{ i + 1 }}</span>
                <span class="pl-name">{{ st.name }}</span>
                <span class="pl-count">{{ st.count }} {{ st.unit }}</span>
                <span class="pl-detail">{{ st.detail }}</span>
              </li>
            </ol>
          </div>
          <div class="reason-legend">
            引用标记：<span class="lg-ref">[来源N]</span> = 知识库文档原文片段 ·
            <span class="lg-fact">[事实]</span> = 知识图谱结构化事实（实体属性 / 关系）
          </div>
          <!-- 已加载技能 -->
          <div class="reason-block" v-if="activeSkills.length">
            <div class="reason-label">已加载技能</div>
            <div class="skill-chips-inline">
              <span v-for="s in activeSkills" :key="s.id" class="skill-tag">{{ s.name }}</span>
            </div>
          </div>
          <!-- 识别实体 -->
          <div class="reason-block" v-if="entities.length">
            <div class="reason-label">识别实体</div>
            <div class="entity-chips">
              <button v-for="e in entities" :key="e.id" class="entity-chip" :title="`${e.type || ''}${e.type && e.source ? ' · ' : ''}${sourceLabel(e.source)}`" @click="gotoEntity(e.id)">
                <span class="entity-type" v-if="e.type">{{ e.type }}</span>
                <span class="entity-name">{{ e.name }}</span>
              </button>
            </div>
          </div>
          <!-- 图谱事实 -->
          <div class="reason-block" v-if="factRelations.length">
            <div class="reason-label">图谱事实（{{ factRelations.length }}）</div>
            <div class="fact-list" :class="{ expanded: factsExpanded }">
              <div class="fact-item" v-for="(r, i) in visibleFacts" :key="i">
                <span class="fact-node">{{ r.source_name }}</span>
                <span class="fact-rel">─ {{ r.relation_type }} →</span>
                <span class="fact-node">{{ r.target_name }}</span>
              </div>
            </div>
            <button v-if="factRelations.length > FACT_PREVIEW_COUNT" type="button" class="fact-toggle" @click="factsExpanded = !factsExpanded">
              {{ factsExpanded ? '收起' : `展开全部 ${factRelations.length} 条` }}
            </button>
          </div>
          <div class="reason-block reason-empty" v-if="!entities.length && !factRelations.length">
            未识别到图谱实体或关系，已使用向量模式回答。
          </div>
        </div>
      </div>

      <!-- 实时思考（reasoning 事件流式） -->
      <div class="think-card" v-if="liveThink">
        <div class="think-toggle" @click="liveThinkExpanded = !liveThinkExpanded">
          <svg class="think-icon" :class="{ open: liveThinkExpanded || !liveThinkDone }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          <span>{{ liveThinkDone ? '查看思考过程' : '深度思考中' }}</span>
          <span v-if="!liveThinkDone" class="thinking-dots"><i></i><i></i><i></i></span>
        </div>
        <div class="think-content live markdown-body" v-show="liveThinkExpanded || !liveThinkDone" ref="liveThinkBoxRef" v-html="liveThinkHtml"></div>
      </div>

      <!-- Think -->
      <div class="think-card" v-for="(b, i) in thinkBlocks" :key="`t${i}`">
        <div class="think-toggle" @click="thinkExpanded = !thinkExpanded">
          <svg class="think-icon" :class="{ open: thinkExpanded }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          <span>{{ thinkExpanded ? '收起思考过程' : '查看思考过程' }}</span>
        </div>
        <div class="think-content markdown-body" v-show="thinkExpanded" v-html="renderMd(b.content)"></div>
      </div>

      <!-- 工具调用（L2 工具循环） -->
      <div class="tool-card" v-if="toolCalls.length">
        <div class="tool-card-title">工具调用 · {{ toolCalls.length }}</div>
        <div class="tool-list">
          <div v-for="(c, i) in toolCalls" :key="`tool${i}`" class="tool-item">
            <span class="tool-name">{{ c.name }}</span>
            <span class="tool-args" v-if="c.arguments && Object.keys(c.arguments).length">{{ JSON.stringify(c.arguments) }}</span>
            <span class="tool-status" :class="{ ok: c.ok, fail: c.ok === false }">
              {{ c.ok === null ? '执行中…' : (c.ok ? `${c.duration_ms || 0}ms` : `失败：${c.error || '未知错误'}`) }}
            </span>
          </div>
        </div>
      </div>

      <div class="content-row">
        <!-- Answer -->
        <div class="answer-col">
          <div class="answer-card" :class="{ streaming: querying }">
            <h4>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/></svg>
              回答
            </h4>
            <div class="answer-text" v-if="answerExThink">
              <div class="markdown-body" v-html="processedAnswerHtml" @click="onAnswerClick" @mouseover="onAnswerHover" @mouseleave="hoveredChunk = null"></div>
            </div>
            <div class="answer-text empty-hint" v-else-if="querying"><span class="spinner"></span> 思考中...</div>
          </div>
        </div>

        <!-- Sources -->
        <div class="sources-col" v-if="chunks.length">
          <div class="sources-header">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/></svg>
            来源 · {{ chunks.length }}
          </div>
          <div class="sources-hint">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 16 12 12"/><polyline points="12 8 12.01 8"/></svg>
            展开分片后双击内容可预览原文
          </div>
          <div class="sources-scroll">
            <div v-for="(c, i) in chunks" :key="i" :id="`src-${i + 1}`"
              class="source-chip" :class="{ active: expandedSources[i], highlight: hoveredChunk === (i + 1) }"
              :style="{ '--src-color': retrievalMeta(c).color }"
              @mouseenter="hoveredChunk = i + 1" @mouseleave="hoveredChunk = null">
              <div class="source-chip-top" @click="onSourceClick(i)">
                <span class="source-idx" :style="{ background: retrievalMeta(c).color }">{{ i + 1 }}</span>
                <span class="source-name">{{ c.file_name }}</span>
                <span class="source-tag" :style="{ color: retrievalMeta(c).color, borderColor: retrievalMeta(c).color }">{{ retrievalMeta(c).label }}</span>
                <span class="source-pct" v-if="pct(c) != null" :style="pctBg(i, c.score)">{{ pct(c) }}%</span>
                <svg class="source-chevron" :class="{ open: expandedSources[i] }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
              </div>
              <div class="source-text" v-show="expandedSources[i]" @dblclick.stop="onSourceDblClick(c)">{{ c.text }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
    </div><!-- /chat-scroll -->

    <!-- 会话（短期记忆）：新建 / 切换 / 重命名 / 删除，与输入框一起固定在底部 -->
    <div class="session-bar">
      <button type="button" class="session-new" @click="startNewSession" :disabled="querying">＋ 新会话</button>
      <div class="session-picker" ref="sessionSelectRef" v-if="sessions.length">
        <button type="button" class="session-trigger" @click="sessionsOpen = !sessionsOpen">
          <span class="session-trigger-label">{{ sessionId ? currentSessionLabel : '历史会话' }}</span>
          <svg class="session-caret" :class="{ open: sessionsOpen }" width="10" height="10" viewBox="0 0 10 10"><path d="M2 4l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
        <div v-if="sessionsOpen" class="session-dropdown">
          <div v-for="s in sessions" :key="s.id" class="session-item" :class="{ active: s.id === sessionId }">
            <button type="button" class="session-item-title" @click="selectSession(s)">{{ s.title || '未命名会话' }}</button>
            <span class="session-item-actions">
              <button type="button" class="session-act" @click.stop="renameSession(s)" title="重命名">✎</button>
              <button type="button" class="session-act danger" @click.stop="removeSession(s)" title="删除">✕</button>
            </span>
          </div>
        </div>
      </div>
    </div>

    <div class="query-row">
      <div class="field-shell search-shell" :class="{ disabled: querying }">
        <span class="field-icon" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16.65" y2="16.65"/></svg>
        </span>
        <input ref="queryInputRef" type="text" v-model="queryText" placeholder="输入问题，智能体将结合图谱与本体回答..." @keydown.enter="runQuery" :disabled="querying">
        <button class="query-submit" @click="runQuery" :disabled="!queryText.trim() || querying">
          <span class="spinner" v-if="querying"></span>
          <template v-else>提问</template>
        </button>
      </div>
    </div>

    <PreviewModal
      :visible="previewVisible"
      :file-id="previewFileId"
      :file-name="previewFileName"
      :file-ext="previewFileExt"
      :page-number="previewPageNumber"
      :start-offset="previewStartOffset"
      :end-offset="previewEndOffset"
      :chunk-text="previewChunkText"
      @close="previewVisible = false"
    />
  </div>
</template>

<style scoped>
.agent-section {
  display: flex; flex-direction: column; gap: 10px;
  /* 视口高度 - 顶栏 52px - 页面上下留白 76px：页面不滚，聊天区内部滚 */
  height: calc(100dvh - 128px); min-height: 520px;
}
.agent-section > * { flex-shrink: 0; }

/* 会话流滚动区：右侧滚动条，消息多时内部滚动，输入区常驻底部 */
.chat-scroll {
  flex: 1 1 0; min-height: 220px;
  display: flex; flex-direction: column; gap: 12px;
  overflow-y: auto; overscroll-behavior: contain;
  padding-right: 8px;
  scrollbar-width: thin; scrollbar-color: var(--c-border) transparent;
}
.chat-scroll::-webkit-scrollbar { width: 8px; }
.chat-scroll::-webkit-scrollbar-track { background: transparent; }
.chat-scroll::-webkit-scrollbar-thumb { background: var(--c-border); border-radius: 4px; }
.chat-scroll::-webkit-scrollbar-thumb:hover { background: var(--c-secondary); }

@media (max-width: 640px) {
  .agent-section { height: calc(100dvh - 112px); }
  .chat-scroll { padding-right: 4px; }
}
/* ── 紧凑配置区：智能体 + 技能一行，知识库一行 ── */
.cfg-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cfg-label { flex-shrink: 0; font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.cfg-select {
  flex-shrink: 0; max-width: 240px; padding: 4px 8px; border: 1px solid var(--c-border); border-radius: 8px;
  font-size: 12px; font-family: var(--font); background: var(--c-panel); color: var(--c-fg); outline: none;
  transition: border-color 150ms;
}
.cfg-select:focus { border-color: var(--c-accent); }
.cfg-sub { display: flex; align-items: center; gap: 12px; margin-top: -6px; }
.agent-pick-hint { font-size: 10px; color: var(--c-accent); }
.cfg-note { font-size: 10px; color: var(--c-secondary); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cfg-note a { color: #a78bfa; text-decoration: none; font-weight: 600; }
.cfg-note a:hover { text-decoration: underline; }

/* ── 技能编辑器（与智能体配置页 skill-chip 同款式，紧凑版） ── */
.skill-chips-editor { display: flex; flex-wrap: wrap; gap: 4px; flex: 1; min-width: 0; }
.skill-chip {
  display: inline-flex; align-items: center; gap: 3px; padding: 2px 9px;
  border-radius: 20px; font-size: 11px; font-weight: 500; cursor: pointer; user-select: none;
  font-family: var(--font);
  border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-secondary);
  transition: all 150ms;
}
.skill-chip:hover { border-color: var(--c-accent); color: var(--c-fg); }
.skill-chip.active { background: var(--c-muted); border-color: var(--c-accent); color: var(--c-accent); }
.chip-ic { font-size: 10px; line-height: 1; }
.skill-empty { font-size: 11px; color: var(--c-secondary); }
.field-shell {
  display: flex; align-items: center; gap: 8px;
  min-height: 38px; border: 1px solid var(--c-border); border-radius: 10px;
  background: var(--c-panel);
  transition: border-color 180ms, box-shadow 180ms;
}
.field-shell:hover { border-color: var(--c-muted-hover); }
.field-shell:focus-within { border-color: var(--c-accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--c-accent) 14%, transparent); }
.field-shell.disabled { opacity: 0.72; }
.field-icon { display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; margin-left: 8px; flex-shrink: 0; border-radius: 8px; color: var(--c-secondary); background: var(--c-muted); border: 1px solid var(--c-border); }

.kb-row .kb-picker { flex: 1; min-width: 0; }
.kb-picker { position: relative; }
.cfg-row .kb-picker { flex: 1 1 auto; min-width: 240px; max-width: 360px; }
.select-shell { position: relative; padding-right: 10px; }
.select-trigger { width: 100%; justify-content: flex-start; text-align: left; padding: 0 12px; cursor: pointer; }
.select-trigger.open .field-caret { transform: translateY(-50%) rotate(180deg); }
.select-value { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; color: var(--c-fg); }
.select-value.placeholder { color: var(--c-secondary); opacity: 0.75; }
.field-caret { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); color: var(--c-secondary); pointer-events: none; transition: transform 180ms ease; }
.kb-dropdown { position: absolute; top: calc(100% + 6px); left: 0; width: max-content; min-width: 100%; max-width: min(360px, calc(100vw - 48px)); z-index: 20; padding: 6px; border: 1px solid var(--c-border); border-radius: 12px; background: var(--c-panel-elevated); box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45); backdrop-filter: blur(10px); }
.kb-option { width: 100%; border: 0; background: transparent; cursor: pointer; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 10px; border-radius: 8px; text-align: left; color: var(--c-fg); transition: background 150ms, color 150ms; }
.kb-option:hover { background: var(--c-muted); }
.kb-option.active { background: var(--c-muted-hover); color: var(--c-accent); font-weight: 600; }
.kb-option-placeholder { color: var(--c-secondary); font-weight: 500; }
.kb-option-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; }
.kb-option-meta { flex-shrink: 0; font-size: 11px; color: var(--c-secondary); }

.query-row { display: flex; }
.search-shell { width: 100%; padding-right: 8px; }
.query-row input { flex: 1; min-width: 0; border: 0; outline: none; box-shadow: none; background: transparent; padding: 0; font-size: 13.5px; }
.query-row input::placeholder { color: var(--c-secondary); opacity: 0.75; }
.query-submit { border: 0; outline: none; cursor: pointer; flex-shrink: 0; min-width: 80px; height: 34px; padding: 0 16px; border-radius: 10px; background: var(--c-accent); color: var(--c-bg); font-size: 13px; font-weight: 700; font-family: var(--font); box-shadow: 0 8px 20px color-mix(in srgb, var(--c-accent) 22%, transparent); transition: transform 150ms, box-shadow 150ms, opacity 150ms, filter 150ms; }
.query-submit:hover:not(:disabled) { transform: translateY(-1px); filter: brightness(1.06); box-shadow: 0 12px 26px color-mix(in srgb, var(--c-accent) 28%, transparent); }
.query-submit:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }

.results { display: flex; flex-direction: column; gap: 14px; }

/* 工具调用（L2）—— 全部使用主题变量，自动适配深色模式 */
.tool-card { border: 1px solid var(--c-border); border-radius: 14px; background: var(--c-panel-elevated); padding: 10px 14px; }
.tool-card-title { font-size: 13px; font-weight: 700; color: var(--c-fg); margin-bottom: 8px; }
.tool-list { display: flex; flex-direction: column; gap: 6px; }
.tool-item { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
.tool-name { font-family: monospace; background: var(--c-muted); border-radius: 6px; padding: 2px 8px; color: var(--c-fg); font-weight: 700; }
.tool-args { color: var(--c-secondary); font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }
.tool-status { margin-left: auto; color: var(--c-secondary); }
.tool-status.ok { color: #16a34a; }
.tool-status.fail { color: #dc2626; }

/* 推理过程 —— 全部使用主题变量，自动适配深色模式 */
.reason-card { border: 1px solid var(--c-border); border-radius: 18px; overflow: hidden; background: var(--c-panel-elevated); box-shadow: 0 10px 30px rgba(0, 0, 0, 0.05); }
.reason-toggle { display: flex; align-items: center; gap: 8px; padding: 12px 16px; cursor: pointer; user-select: none; font-size: 13px; color: var(--c-fg); font-weight: 700; }
.reason-toggle:hover { background: var(--c-muted); }
.reason-icon { transition: transform 200ms; color: var(--c-secondary); }
.reason-icon.open { transform: rotate(180deg); }
.reason-path { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
.rp { font-size: 11px; font-weight: 600; color: var(--c-secondary); background: var(--c-muted); padding: 2px 8px; border-radius: 999px; }
.rp-both { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 16%, transparent); }
.export-btn { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 10px; font-size: 11px; font-weight: 600; font-family: var(--font); color: var(--c-secondary); background: transparent; border: 1px solid var(--c-border); cursor: pointer; transition: all 150ms; flex-shrink: 0; }
.export-btn:hover { color: var(--c-accent); border-color: var(--c-accent); }
.pipeline-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 7px; }
.pipeline-item { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }
.pl-idx { width: 18px; height: 18px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; flex-shrink: 0; background: var(--c-muted); color: var(--c-accent); border: 1px solid color-mix(in srgb, var(--c-accent) 20%, transparent); align-self: flex-start; }
.pl-name { font-size: 12px; font-weight: 700; color: var(--c-fg); }
.pl-count { font-size: 11px; font-weight: 600; color: var(--c-accent); background: var(--c-muted); padding: 1px 8px; border-radius: 999px; }
.pl-detail { flex-basis: 100%; font-size: 11.5px; line-height: 1.6; color: var(--c-secondary); padding-left: 26px; }
.rp-deg { color: #f59e0b; background: rgba(245, 158, 11, 0.12); }
.reason-body { padding: 4px 16px 14px; display: flex; flex-direction: column; gap: 12px; border-top: 1px solid var(--c-border); }
.reason-block { display: flex; flex-direction: column; gap: 6px; }
.reason-label { font-size: 11px; font-weight: 700; color: var(--c-secondary); text-transform: uppercase; letter-spacing: 0.4px; }
.reason-empty { font-size: 13px; color: var(--c-secondary); }
.reason-legend { font-size: 11px; color: var(--c-secondary); line-height: 1.7; padding: 2px 0; }
.reason-legend .lg-ref { color: #6366f1; font-weight: 700; }
.reason-legend .lg-fact { color: var(--c-accent); font-weight: 700; }

.entity-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.entity-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; border: 1px solid color-mix(in srgb, var(--c-accent) 38%, transparent); background: var(--c-panel); color: var(--c-accent); font-size: 12px; font-weight: 600; cursor: pointer; transition: background 150ms, border-color 150ms, transform 150ms; }
.entity-chip:hover { background: color-mix(in srgb, var(--c-accent) 14%, transparent); border-color: var(--c-accent); transform: translateY(-1px); }
.entity-type { font-size: 10px; color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 16%, transparent); padding: 1px 6px; border-radius: 999px; }

.fact-list { display: flex; flex-direction: column; gap: 4px; }
.fact-list.expanded { max-height: 260px; overflow-y: auto; padding-right: 4px; }
.fact-item { font-size: 13px; color: var(--c-secondary); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.fact-node { font-weight: 600; color: var(--c-fg); }
.fact-rel { color: var(--c-accent); font-size: 12px; }
.fact-toggle { align-self: flex-start; border: 0; background: transparent; color: var(--c-accent); font-size: 12px; font-weight: 600; cursor: pointer; padding: 2px 0; }
.fact-toggle:hover { text-decoration: underline; }

/* Think */
.think-card { border: 1px solid color-mix(in srgb, #7c3aed 30%, var(--c-border)); border-radius: 18px; overflow: hidden; background: color-mix(in srgb, #7c3aed 8%, var(--c-panel)); box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15); }
.think-toggle { display: flex; align-items: center; gap: 6px; padding: 12px 16px; cursor: pointer; user-select: none; font-size: 13px; color: #a78bfa; font-weight: 600; transition: background 150ms; }
.think-toggle:hover { background: rgba(124, 58, 237, 0.14); }
.think-icon { transition: transform 200ms; color: #a78bfa; }
.think-icon.open { transform: rotate(180deg); }
.think-content { padding: 0 16px 14px; font-size: 13px; line-height: 1.65; color: var(--c-secondary); border-top: 1px solid color-mix(in srgb, #7c3aed 30%, var(--c-border)); padding-top: 12px; }
.think-content.live { max-height: 200px; overflow-y: auto; }
.thinking-dots { display: inline-flex; gap: 3px; align-items: center; margin-left: 2px; }
.thinking-dots i { width: 4px; height: 4px; border-radius: 50%; background: #7c5cf0; animation: thinkdot 1.2s ease-in-out infinite; }
.thinking-dots i:nth-child(2) { animation-delay: 0.2s; }
.thinking-dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes thinkdot { 0%, 60%, 100% { opacity: 0.25; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-2px); } }

.content-row { display: flex; gap: 20px; align-items: flex-start; }
.answer-col { flex: 1; min-width: 0; }
.answer-card { border: 1px solid var(--c-border); border-radius: 22px; padding: 18px 18px 16px; background: var(--c-panel-elevated); box-shadow: 0 18px 40px rgba(0, 0, 0, 0.18); }
.answer-card h4 { font-size: 13px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; color: var(--c-secondary); letter-spacing: 0.2px; }
.answer-card h4 svg { width: 28px; height: 28px; padding: 6px; border-radius: 10px; background: var(--c-muted); border: 1px solid var(--c-border); color: var(--c-secondary); }
.answer-card .answer-text { font-size: 14px; line-height: 1.7; overflow-y: auto; }
.answer-text.empty-hint { color: var(--c-secondary); font-size: 13px; display: flex; align-items: center; gap: 8px; }
.answer-card.streaming .markdown-body::after { content: '|'; animation: blink 0.7s step-end infinite; font-weight: 100; color: var(--c-secondary); }
@keyframes blink { 50% { opacity: 0; } }

.sources-col { width: 280px; flex-shrink: 0; overflow: hidden; max-height: calc(100vh - 200px); display: flex; flex-direction: column; border: 1px solid var(--c-border); border-radius: 22px; background: var(--c-panel-elevated); box-shadow: 0 18px 40px rgba(0, 0, 0, 0.18); }
.sources-header { display: flex; align-items: center; gap: 8px; padding: 14px 16px; font-size: 12px; color: var(--c-secondary); font-weight: 700; border-bottom: 1px solid var(--c-border); flex-shrink: 0; }
.sources-header svg { width: 28px; height: 28px; padding: 6px; border-radius: 10px; background: var(--c-muted); border: 1px solid var(--c-border); color: var(--c-secondary); }
.sources-hint { display: flex; align-items: center; gap: 4px; padding: 8px 16px; font-size: 10px; color: var(--c-secondary); border-bottom: 1px solid var(--c-border); background: var(--c-muted); }
.sources-scroll { overflow-y: auto; flex: 1; padding: 10px; display: flex; flex-direction: column; gap: 8px; }

.source-chip { border: 1px solid var(--c-border); border-radius: 16px; background: var(--c-panel); transition: border-color 150ms, background 150ms, box-shadow 150ms, transform 150ms; border-left: 3px solid var(--src-color); }
.source-chip:hover { border-color: var(--c-muted-hover); transform: translateY(-1px); }
.source-chip.active { border-color: var(--src-color); background: var(--c-muted); }
.source-chip.highlight { border-color: var(--src-color); background: var(--c-muted); box-shadow: 0 0 0 2px color-mix(in srgb, var(--src-color) 20%, transparent), 0 10px 24px rgba(0, 0, 0, 0.3); }
.source-chip-top { display: flex; align-items: center; gap: 8px; padding: 10px 12px; font-size: 12px; cursor: pointer; user-select: none; }
.source-idx { width: 20px; height: 20px; border-radius: 6px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; color: #fff; }
.source-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-fg); font-weight: 600; font-size: 11px; }
.source-tag { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 999px; border: 1px solid; flex-shrink: 0; }
.source-pct { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 999px; flex-shrink: 0; }
.source-chevron { flex-shrink: 0; color: var(--c-secondary); transition: transform 200ms; }
.source-chevron.open { transform: rotate(180deg); }
.source-text { font-size: 12px; line-height: 1.6; color: var(--c-secondary); padding: 0 12px 12px; border-top: 1px solid var(--c-border); padding-top: 10px; white-space: pre-wrap; max-height: 140px; overflow-y: auto; }

.spinner { width: 14px; height: 14px; border: 2px solid color-mix(in srgb, var(--c-bg) 35%, transparent); border-top-color: var(--c-bg); border-radius: 50%; animation: spin 0.7s linear infinite; display: inline-block; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ── 推理卡片中的技能标签 ── */
.skill-chips-inline { display: flex; flex-wrap: wrap; gap: 6px; }
.skill-tag {
  display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 500;
  background: var(--c-muted); color: var(--c-accent); border: 1px solid color-mix(in srgb, var(--c-accent) 20%, transparent);
}

@media (max-width: 720px) {
  .field-shell { min-height: 48px; border-radius: 14px; }
  .field-icon { width: 34px; height: 34px; margin-left: 8px; }
  .query-submit { min-width: 78px; height: 36px; padding: 0 14px; }
  .content-row { flex-direction: column; }
  .sources-col { width: 100%; max-height: 360px; }
}
</style>

<style>
.cite-ref {
  display: inline-block; cursor: pointer;
  color: var(--c); font-weight: 700; font-size: 0.75em;
  background: color-mix(in srgb, var(--c) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--c) 30%, transparent);
  padding: 0 4px; border-radius: 3px; margin: 0 1px;
  vertical-align: super; line-height: 1.4;
  transition: background 150ms, box-shadow 150ms;
}
.cite-ref:hover { background: color-mix(in srgb, var(--c) 25%, transparent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c) 20%, transparent); }
.cite-fact {
  display: inline-block; font-size: 0.72em; font-weight: 700; color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 14%, transparent); border: 1px solid color-mix(in srgb, var(--c-accent) 36%, transparent);
  padding: 0 4px; border-radius: 3px; margin: 0 1px; vertical-align: super;
}

.markdown-body h1, .markdown-body h2, .markdown-body h3 { margin: 12px 0 6px; font-weight: 600; color: var(--c-fg); }
.markdown-body h1 { font-size: 1.25em; }
.markdown-body h2 { font-size: 1.15em; }
.markdown-body h3 { font-size: 1.05em; }
.markdown-body p { margin: 6px 0; }
.markdown-body ul, .markdown-body ol { padding-left: 1.5em; margin: 6px 0; }
.markdown-body li { margin: 2px 0; }
.markdown-body code { background: var(--c-muted); padding: 2px 6px; border-radius: 3px; font-size: 0.9em; font-family: var(--font-mono, 'Consolas', monospace); }
.markdown-body pre { background: #1e1e1e; color: #d4d4d4; padding: 12px 16px; border-radius: 6px; overflow-x: auto; margin: 8px 0; line-height: 1.5; }
.markdown-body pre code { background: none; padding: 0; color: inherit; font-size: 13px; }
.markdown-body table { border-collapse: collapse; width: 100%; margin: 8px 0; }
.markdown-body th, .markdown-body td { border: 1px solid var(--c-border); padding: 6px 10px; text-align: left; font-size: 13px; }
.markdown-body th { background: var(--c-muted); font-weight: 600; }
.markdown-body blockquote { border-left: 3px solid #7c3aed; padding: 4px 12px; margin: 8px 0; color: var(--c-secondary); background: color-mix(in srgb, #7c3aed 8%, transparent); }
.markdown-body hr { border: none; border-top: 1px solid var(--c-border); margin: 12px 0; }
.markdown-body a { color: #a78bfa; }
.markdown-body strong { font-weight: 600; }
.markdown-body img { max-width: 100%; border-radius: 4px; }

/* ── 智能体配置摘要（选中智能体后替代页面 KB/技能选择）── */


/* ── 会话（短期记忆）── */
.session-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.session-new { border: 1px dashed var(--c-border); background: transparent; color: var(--c-secondary); border-radius: 12px; padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; transition: border-color 150ms, color 150ms; }
.session-new:hover:not(:disabled) { border-color: var(--c-accent); color: var(--c-fg); }
.session-new:disabled { opacity: 0.5; cursor: not-allowed; }
.session-picker { position: relative; }
.session-trigger { display: flex; align-items: center; gap: 8px; border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-fg); border-radius: 12px; padding: 8px 12px; font-size: 13px; font-weight: 600; cursor: pointer; max-width: 420px; }
.session-trigger-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-caret { transition: transform 150ms; color: var(--c-secondary); flex-shrink: 0; }
.session-caret.open { transform: rotate(180deg); }
.session-dropdown { position: absolute; bottom: calc(100% + 8px); left: 0; z-index: 20; min-width: 320px; max-width: 460px; max-height: 320px; overflow-y: auto; padding: 8px; border: 1px solid var(--c-border); border-radius: 14px; background: var(--c-panel-elevated); box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45); }
.session-item { display: flex; align-items: center; gap: 4px; border-radius: 10px; }
.session-item:hover { background: var(--c-muted); }
.session-item.active { background: var(--c-muted-hover); }
.session-item-title { flex: 1; min-width: 0; border: 0; background: transparent; text-align: left; padding: 9px 10px; font-size: 13px; color: var(--c-fg); cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-item.active .session-item-title { font-weight: 700; }
.session-item-actions { display: none; flex-shrink: 0; gap: 2px; padding-right: 6px; }
.session-item:hover .session-item-actions { display: inline-flex; }
.session-act { border: 0; background: transparent; color: var(--c-secondary); cursor: pointer; padding: 4px 6px; border-radius: 6px; font-size: 12px; }
.session-act:hover { background: var(--c-muted-hover); color: var(--c-fg); }
.session-act.danger:hover { background: rgba(248, 113, 113, 0.12); color: var(--c-danger); }

/* ── 会话历史气泡 ── */
.chat-turns { display: flex; flex-direction: column; gap: 10px; }
.chat-q { align-self: flex-end; max-width: 78%; background: color-mix(in srgb, var(--c-accent) 20%, var(--c-panel)); border: 1px solid color-mix(in srgb, var(--c-accent) 32%, transparent); color: var(--c-fg); padding: 9px 16px; border-radius: 16px 16px 4px 16px; font-size: 13.5px; line-height: 1.6; box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25); }
.chat-a { align-self: flex-start; max-width: 90%; background: var(--c-panel); border: 1px solid var(--c-border); padding: 6px 16px; border-radius: 16px 16px 16px 4px; font-size: 13.5px; }
.chat-a :first-child { margin-top: 0; }
.chat-a :last-child { margin-bottom: 0; }
</style>
