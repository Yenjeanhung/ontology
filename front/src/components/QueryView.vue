<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, reactive, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { marked } from 'marked'
import { fetchKbs, queryRagStream, queryAgentStream, fetchAgentSkills } from '../api'
import PreviewModal from './PreviewModal.vue'

const router = useRouter()
const kbs = ref([])
const queryKbId = ref('')
const queryText = ref('')
const querying = ref(false)
const answerRaw = ref('')
const chunks = ref([])
const kbSelectRef = ref(null)
const kbDropdownOpen = ref(false)

// ---------- 问答模式：rag = 知识库检索 / agent = 智能体 ----------
const mode = ref('rag')
const isAgent = computed(() => mode.value === 'agent')

// ---------- 技能（智能体模式） ----------
const allSkills = ref([])
const selectedSkillIds = ref([])
const activeSkills = ref([])     // SSE 实际生效的技能
const enabledSkills = computed(() => allSkills.value.filter(s => s.is_enabled))
function toggleSkill(id) {
  const idx = selectedSkillIds.value.indexOf(id)
  if (idx >= 0) selectedSkillIds.value.splice(idx, 1)
  else selectedSkillIds.value.push(id)
}
async function loadSkills() {
  try {
    allSkills.value = await fetchAgentSkills()
    selectedSkillIds.value = enabledSkills.value.map(s => s.id)
  } catch {}
}

// ---------- 智能体推理过程 ----------
const entities = ref([])        // 识别到的种子实体
const subgraph = ref(null)      // {facts, entities, relations, retrieval_path}
const reasonOpen = ref(true)
const isDegraded = computed(() => !!subgraph.value?.retrieval_path?.degraded)
const factRelations = computed(() => subgraph.value?.relations || [])
const pathInfo = computed(() => subgraph.value?.retrieval_path || {})

function switchMode(m) {
  if (mode.value === m || querying.value) return
  mode.value = m
  answerRaw.value = ''
  chunks.value = []
  entities.value = []
  subgraph.value = null
  activeSkills.value = []
  thinkBlocks.value = []
  thinkExpanded.value = false
  hoveredChunk.value = null
  Object.keys(expandedSources).forEach(k => delete expandedSources[k])
  if (m === 'agent' && !allSkills.value.length) loadSkills()
}
function gotoEntity(id) {
  if (id) router.push(`/entities/${id}`)
}

const queryKbList = computed(() => kbs.value.filter(kb => kb.file_count > 0))
const selectedKb = computed(() => queryKbList.value.find(kb => kb.id === queryKbId.value) || null)
const selectedKbLabel = computed(() => selectedKb.value
  ? `${selectedKb.value.name} (${selectedKb.value.file_count} 个文件)`
  : '请选择知识库...')

function renderMd(text) {
  if (!text) return ''
  return marked.parse(text)
}

const CITE_COLORS = ['#6366f1', '#ec4899', '#f59e0b', '#10b981', '#3b82f6', '#ef4444', '#8b5cf6', '#14b8a6']
function chunkColor(idx) { return CITE_COLORS[idx % CITE_COLORS.length] }

// ---------- 智能体模式：召回来源标记 ----------
const RETRIEVAL_META = {
  vector: { label: '向量', color: '#6366f1' },
  graph: { label: '图谱', color: '#10b981' },
  both: { label: '交集', color: '#8b5cf6' },
}
function retrievalMeta(c) { return RETRIEVAL_META[c.retrieval] || RETRIEVAL_META.vector }
function accentFor(c, idx) { return isAgent.value ? retrievalMeta(c).color : sourceAccent(idx) }
function sourceAccent(idx) {
  const total = Math.max(chunks.value.length - 1, 1)
  const t = Math.min(idx / total, 1)
  const lightness = 60 + t * 24
  return `hsl(244 72% ${lightness}%)`
}

// ---------- 思考过程 ----------
// 兼容 Qwen <think>...</think>，DeepSeek 不用此格式
const thinkBlocks = ref([])
const answerExThink = computed(() => {
  let s = answerRaw.value
  thinkBlocks.value = []
  const re = /<think>([\s\S]*?)<\/think>/g
  const blocks = []
  let m
  while ((m = re.exec(s)) !== null) {
    blocks.push(m[1].trim())
  }
  if (blocks.length) {
    thinkBlocks.value = blocks.map(c => ({ content: c }))
    s = s.replace(/<think>[\s\S]*?<\/think>/g, '').trim()
  }
  return s
})

const thinkExpanded = ref(false)

// ---------- 分片展开 ----------
const expandedSources = reactive({})

// ---------- hover ----------
const hoveredChunk = ref(null)

// ---------- PDF 预览弹窗 ----------
const previewVisible = ref(false)
const previewFileId = ref('')
const previewFileName = ref('')
const previewFileExt = ref('')
const previewPageNumber = ref(1)
const previewStartOffset = ref(0)
const previewEndOffset = ref(0)
const previewChunkText = ref('')

// ---------- 核心：先替换 [来源N] 再渲染 markdown ----------
const processedAnswerHtml = computed(() => {
  let text = answerExThink.value
  if (!text) return ''

  // 保护代码块（反引号包裹的）
  text = text.replace(/(```[\s\S]*?```|`[^`]*`)/g, (m) => {
    return m.replace(/\[来源(\d+)\]/g, '\x00CITE$1\x00').replace(/\[事实\]/g, '\x00FACT\x00')
  })

  // 替换 [来源N]
  text = text.replace(/\[来源(\d+)\]/g, (_, num) => {
    const idx = parseInt(num) - 1
    const color = accentFor(chunks.value[idx] || {}, idx)
    return `<span class="cite-ref" data-chunk="${num}" style="--c:${color}">[${num}]</span>`
  })

  // 智能体模式：替换 [事实]
  if (isAgent.value) text = text.replace(/\[事实\]/g, '<span class="cite-fact">事实</span>')

  // 恢复代码块中被保护的
  text = text.replace(/\x00CITE(\d+)\x00/g, '[来源$1]').replace(/\x00FACT\x00/g, '[事实]')

  return renderMd(text)
})

// ---------- 点击回答区域（事件委托）----------
function onAnswerClick(e) {
  const cite = e.target.closest('[data-chunk]')
  if (!cite) return
  const num = +cite.dataset.chunk
  // 展开对应分片
  expandedSources[num - 1] = true
  hoveredChunk.value = num
  nextTick(() => {
    const el = document.getElementById(`src-${num}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })
  setTimeout(() => { hoveredChunk.value = null }, 1500)
}

// ---------- hover 回答 → 联动分片 ----------
function onAnswerHover(e) {
  const cite = e.target.closest('[data-chunk]')
  hoveredChunk.value = cite ? +cite.dataset.chunk : null
}

// ---------- 点击分片 → 定位回答 ----------
function onSourceClick(idx) {
  const num = idx + 1
  expandedSources[idx] = !expandedSources[idx]
  hoveredChunk.value = num
  nextTick(() => {
    const el = document.getElementById(`src-${num}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })
  // 定位回答中第一个对应引用
  nextTick(() => {
    const c = document.querySelector(`.answer-text [data-chunk="${num}"]`)
    if (c) c.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
  setTimeout(() => { hoveredChunk.value = null }, 1500)
}

// ---------- 双击分片 → PDF 预览弹窗 ----------
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

// ---------- 动态高度 ----------
const answerBoxRef = ref(null)
const answerMaxH = ref('50vh')

function updateAnswerHeight() {
  if (!answerBoxRef.value) return
  const rect = answerBoxRef.value.getBoundingClientRect()
  const spaceBelow = window.innerHeight - rect.top - 24
  answerMaxH.value = Math.max(120, spaceBelow) + 'px'
}

watch([answerExThink, querying], () => {
  if (!querying.value) nextTick(updateAnswerHeight)
})

function pct(c) { return c.score == null ? null : Math.round(c.score * 100) }
function pctBg(idx, score) {
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
  if (!q || !queryKbId.value) return
  querying.value = true
  answerRaw.value = ''
  chunks.value = []
  entities.value = []
  subgraph.value = null
  activeSkills.value = []
  thinkBlocks.value = []
  thinkExpanded.value = false
  hoveredChunk.value = null
  reasonOpen.value = true
  Object.keys(expandedSources).forEach(k => delete expandedSources[k])
  try {
    if (isAgent.value) {
      await queryAgentStream(queryKbId.value, q, {
        skillIds: selectedSkillIds.value,
        onSkills(data) { activeSkills.value = data || [] },
        onEntities(data) { entities.value = data || [] },
        onSubgraph(data) { subgraph.value = data },
        onChunks(data) { chunks.value = data || [] },
        onToken(token) { answerRaw.value += token },
      })
    } else {
      await queryRagStream(queryKbId.value, q, {
        onChunks(data) { chunks.value = data },
        onToken(token) { answerRaw.value += token },
      })
    }
  } catch (err) {
    answerRaw.value = `错误: ${err.message}`
  }
  querying.value = false
}

function toggleKbDropdown() {
  kbDropdownOpen.value = !kbDropdownOpen.value
}

function selectKb(kbId) {
  queryKbId.value = kbId
  kbDropdownOpen.value = false
}

function onWindowPointerDown(e) {
  if (kbSelectRef.value && !kbSelectRef.value.contains(e.target)) {
    kbDropdownOpen.value = false
  }
}

onMounted(loadKbs)
onMounted(() => window.addEventListener('pointerdown', onWindowPointerDown))
onBeforeUnmount(() => window.removeEventListener('pointerdown', onWindowPointerDown))
</script>

<template>
  <div class="query-section">
    <!-- 模式切换：知识库检索 / 智能体 -->
    <div class="mode-tabs" role="tablist" aria-label="问答模式">
      <button
        type="button" role="tab" class="mode-tab" :class="{ active: mode === 'rag' }"
        :aria-selected="mode === 'rag'" :disabled="querying"
        @click="switchMode('rag')"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16.65" y2="16.65"/></svg>
        知识库检索
      </button>
      <button
        type="button" role="tab" class="mode-tab" :class="{ active: mode === 'agent' }"
        :aria-selected="mode === 'agent'" :disabled="querying"
        @click="switchMode('agent')"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/><circle cx="12" cy="12" r="3"/></svg>
        智能体
      </button>
    </div>

    <div class="kb-select">
      <label>选择知识库</label>
      <div class="kb-picker" ref="kbSelectRef">
        <button
          type="button"
          class="field-shell select-shell select-trigger"
          :class="{ open: kbDropdownOpen }"
          @click="toggleKbDropdown"
        >
          <span class="field-icon" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.75 7.25A2.25 2.25 0 0 1 6 5h4.2c.6 0 1.16.24 1.58.66l1.06 1.09c.42.42.98.66 1.58.66H18A2.25 2.25 0 0 1 20.25 9.66v7.09A2.25 2.25 0 0 1 18 19H6a2.25 2.25 0 0 1-2.25-2.25V7.25Z"/><path d="M3.75 9.25h16.5"/></svg>
          </span>
          <span class="select-value" :class="{ placeholder: !selectedKb }">{{ selectedKbLabel }}</span>
          <span class="field-caret" aria-hidden="true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          </span>
        </button>

        <div v-if="kbDropdownOpen" class="kb-dropdown">
          <button type="button" class="kb-option kb-option-placeholder" @click="selectKb('')">请选择知识库...</button>
          <button
            v-for="kb in queryKbList"
            :key="kb.id"
            type="button"
            class="kb-option"
            :class="{ active: kb.id === queryKbId }"
            @click="selectKb(kb.id)"
          >
            <span class="kb-option-name">{{ kb.name }}</span>
            <span class="kb-option-meta">{{ kb.file_count }} 个文件</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 技能选择（智能体模式） -->
    <div class="skill-row" v-if="isAgent && enabledSkills.length">
      <span class="skill-row-label">技能</span>
      <div class="skill-chips">
        <button
          v-for="s in enabledSkills" :key="s.id"
          type="button"
          class="skill-chip" :class="{ active: selectedSkillIds.includes(s.id) }"
          :title="s.description"
          :disabled="querying"
          @click="toggleSkill(s.id)"
        >
          <span class="skill-chip-icon" v-if="selectedSkillIds.includes(s.id)">✓</span>
          <span class="skill-chip-icon" v-else>+</span>
          {{ s.name }}
        </button>
      </div>
    </div>

    <div class="query-row">
      <div class="field-shell search-shell" :class="{ disabled: !queryKbId || querying }">
        <span class="field-icon" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16.65" y2="16.65"/></svg>
        </span>
        <input type="text" v-model="queryText" :placeholder="isAgent ? '输入问题，智能体将结合图谱与技能回答...' : '输入问题...'" @keydown.enter="runQuery" :disabled="!queryKbId || querying">
        <button class="query-submit" @click="runQuery" :disabled="!queryKbId || !queryText.trim() || querying">
          <span class="spinner" v-if="querying"></span>
          <template v-else>{{ isAgent ? '提问' : '搜索' }}</template>
        </button>
      </div>
    </div>

    <div class="results" v-if="answerRaw || chunks.length || (isAgent && subgraph)">

      <!-- 推理过程（智能体模式） -->
      <div class="reason-card" v-if="isAgent && subgraph">
        <div class="reason-toggle" @click="reasonOpen = !reasonOpen">
          <svg class="reason-icon" :class="{ open: reasonOpen }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          <span>推理过程</span>
          <span class="reason-path">
            <span class="rp" v-if="pathInfo.vector != null">向量 {{ pathInfo.vector }}</span>
            <span class="rp" v-if="pathInfo.graph != null">图谱 {{ pathInfo.graph }}</span>
            <span class="rp rp-both" v-if="pathInfo.both">交集 {{ pathInfo.both }}</span>
            <span class="rp rp-deg" v-if="isDegraded">向量模式（未识别到图谱实体）</span>
          </span>
        </div>
        <div class="reason-body" v-show="reasonOpen">
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
              <button v-for="e in entities" :key="e.id" class="entity-chip" :title="`${e.type || ''} · ${e.source || ''}`" @click="gotoEntity(e.id)">
                <span class="entity-type" v-if="e.type">{{ e.type }}</span>
                <span class="entity-name">{{ e.name }}</span>
              </button>
            </div>
          </div>
          <!-- 图谱事实 -->
          <div class="reason-block" v-if="factRelations.length">
            <div class="reason-label">图谱事实</div>
            <div class="fact-list">
              <div class="fact-item" v-for="(r, i) in factRelations" :key="i">
                <span class="fact-node">{{ r.source_name }}</span>
                <span class="fact-rel">─ {{ r.relation_type }} →</span>
                <span class="fact-node">{{ r.target_name }}</span>
              </div>
            </div>
          </div>
          <div class="reason-block reason-empty" v-if="!entities.length && !factRelations.length">
            未识别到图谱实体或关系，已使用向量模式回答。
          </div>
        </div>
      </div>

      <!-- Think -->
      <div class="think-card" v-for="(b, i) in thinkBlocks" :key="i">
        <div class="think-toggle" @click="thinkExpanded = !thinkExpanded">
          <svg class="think-icon" :class="{ open: thinkExpanded }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          <span>{{ thinkExpanded ? '收起思考过程' : '查看思考过程' }}</span>
        </div>
        <div class="think-content markdown-body" v-show="thinkExpanded" v-html="renderMd(b.content)"></div>
      </div>

      <div class="content-row">
        <!-- Answer -->
        <div class="answer-col">
          <div class="answer-card" ref="answerBoxRef" :class="{ streaming: querying }">
            <h4>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5"/></svg>
              回答
            </h4>
            <div class="answer-text" v-if="answerExThink" :style="{ maxHeight: answerMaxH }">
              <div
                class="markdown-body"
                v-html="processedAnswerHtml"
                @click="onAnswerClick"
                @mouseover="onAnswerHover"
                @mouseleave="hoveredChunk = null"
              ></div>
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
            <div
              v-for="(c, i) in chunks" :key="i"
              :id="`src-${i + 1}`"
              class="source-chip"
              :class="{ active: expandedSources[i], highlight: hoveredChunk === (i + 1) }"
              :style="{ '--src-color': accentFor(c, i) }"
              @mouseenter="hoveredChunk = i + 1"
              @mouseleave="hoveredChunk = null"
            >
              <div class="source-chip-top" @click="onSourceClick(i)">
                <span class="source-idx" :style="{ background: accentFor(c, i) }">{{ i + 1 }}</span>
                <span class="source-name">{{ c.file_name }}</span>
                <span v-if="isAgent" class="source-tag" :style="{ color: retrievalMeta(c).color, borderColor: retrievalMeta(c).color }">{{ retrievalMeta(c).label }}</span>
                <span class="source-pct" v-if="pct(c) != null" :style="pctBg(i, c.score)">{{ pct(c) }}%</span>
                <svg class="source-chevron" :class="{ open: expandedSources[i] }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
              </div>
              <div class="source-text" v-show="expandedSources[i]" @dblclick.stop="onSourceDblClick(c)">{{ c.text }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- PDF 预览弹窗 -->
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
.query-section { display: flex; flex-direction: column; gap: 16px; }

/* ── 模式切换 ── */
.mode-tabs {
  display: inline-flex; gap: 4px; padding: 4px; width: fit-content;
  border: 1px solid #e8e5df; border-radius: 14px;
  background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(249,247,243,0.98));
  box-shadow: 0 1px 0 rgba(255,255,255,0.92) inset, 0 10px 28px rgba(23, 23, 23, 0.035);
}
.mode-tab {
  display: inline-flex; align-items: center; gap: 6px;
  min-height: 36px; padding: 0 16px; border: 0; border-radius: 10px;
  background: transparent; color: var(--c-secondary);
  font-size: 13px; font-weight: 600; font-family: var(--font); cursor: pointer;
  transition: background 150ms, color 150ms, box-shadow 150ms;
}
.mode-tab:hover:not(:disabled) { color: var(--c-fg); background: rgba(23, 23, 23, 0.04); }
.mode-tab.active {
  color: #fff;
  background: linear-gradient(135deg, #171717, #3a342b);
  box-shadow: 0 8px 20px rgba(23, 23, 23, 0.18);
}
.mode-tab:disabled { opacity: 0.55; cursor: not-allowed; }

/* ── 技能行 ── */
.skill-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.skill-row-label { font-size: 13px; font-weight: 600; color: var(--c-secondary); }
.skill-chips { display: flex; flex-wrap: wrap; gap: 6px; flex: 1; }
.skill-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 500;
  border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-secondary);
  cursor: pointer; user-select: none; transition: all 150ms;
}
.skill-chip:hover:not(:disabled) { border-color: var(--c-accent); color: var(--c-fg); }
.skill-chip.active { background: var(--c-muted); border-color: var(--c-accent); color: var(--c-accent); }
.skill-chip:disabled { opacity: 0.6; cursor: not-allowed; }
.skill-chip-icon { font-size: 11px; line-height: 1; }
.skill-chips-inline { display: flex; flex-wrap: wrap; gap: 6px; }
.skill-tag {
  display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 500;
  background: var(--c-muted); color: var(--c-accent); border: 1px solid color-mix(in srgb, var(--c-accent) 20%, transparent);
}

/* ── 推理过程（智能体模式） ── */
.reason-card { border: 1px solid var(--c-border); border-radius: 18px; overflow: hidden; background: var(--c-panel-elevated); box-shadow: 0 10px 30px rgba(0, 0, 0, 0.05); }
.reason-toggle { display: flex; align-items: center; gap: 8px; padding: 12px 16px; cursor: pointer; user-select: none; font-size: 13px; color: var(--c-fg); font-weight: 700; }
.reason-toggle:hover { background: var(--c-muted); }
.reason-icon { transition: transform 200ms; color: var(--c-secondary); }
.reason-icon.open { transform: rotate(180deg); }
.reason-path { margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; }
.rp { font-size: 11px; font-weight: 600; color: var(--c-secondary); background: var(--c-muted); padding: 2px 8px; border-radius: 999px; }
.rp-both { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 16%, transparent); }
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
.fact-item { font-size: 13px; color: var(--c-secondary); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.fact-node { font-weight: 600; color: var(--c-fg); }
.fact-rel { color: var(--c-accent); font-size: 12px; }

.kb-select { display: flex; flex-direction: column; gap: 6px; }
.kb-select label { font-size: 13px; font-weight: 600; color: var(--c-secondary); }
.field-shell {
  display: flex; align-items: center; gap: 10px;
  min-height: 52px; border: 1px solid #e8e5df; border-radius: 16px;
  background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(249,247,243,0.98));
  box-shadow: 0 1px 0 rgba(255,255,255,0.92) inset, 0 10px 28px rgba(23, 23, 23, 0.035);
  transition: border-color 180ms, box-shadow 180ms, transform 180ms;
}
.field-shell:hover {
  border-color: #d9d2c7;
  box-shadow: 0 1px 0 rgba(255,255,255,0.96) inset, 0 14px 32px rgba(23, 23, 23, 0.05);
}
.field-shell:focus-within {
  border-color: #c9a46a;
  box-shadow:
    0 1px 0 rgba(255,255,255,0.96) inset,
    0 0 0 4px rgba(161, 98, 7, 0.08),
    0 16px 36px rgba(161, 98, 7, 0.08);
}
.field-shell.disabled { opacity: 0.72; }
.field-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 38px; height: 38px; margin-left: 10px; flex-shrink: 0;
  border-radius: 12px; color: #8b7c67;
  background: linear-gradient(180deg, #fff, #f4efe6);
  border: 1px solid rgba(161, 98, 7, 0.12);
}

.kb-picker { position: relative; }
.select-shell { position: relative; padding-right: 12px; }
.select-trigger {
  width: 100%; justify-content: flex-start; text-align: left;
  padding: 0 12px 0 0; cursor: pointer;
}
.select-trigger.open .field-caret { transform: translateY(-50%) rotate(180deg); }
.select-value {
  flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 15px; color: var(--c-fg);
}
.select-value.placeholder { color: #b3ab9f; }
.field-caret {
  position: absolute; right: 16px; top: 50%; transform: translateY(-50%);
  color: #8b7c67; pointer-events: none; transition: transform 180ms ease;
}
.kb-dropdown {
  position: absolute; top: calc(100% + 8px); left: 0; right: 0; z-index: 20;
  padding: 8px; border: 1px solid #ebe6dc; border-radius: 18px;
  background: rgba(255,255,255,0.98);
  box-shadow: 0 18px 40px rgba(23, 23, 23, 0.08);
  backdrop-filter: blur(10px);
}
.kb-option {
  width: 100%; border: 0; background: transparent; cursor: pointer;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 12px 14px; border-radius: 12px; text-align: left;
  color: var(--c-fg); transition: background 150ms, color 150ms;
}
.kb-option:hover { background: #f7f4ee; }
.kb-option.active {
  background: #f3ede3; color: #171717; font-weight: 600;
}
.kb-option-placeholder {
  color: #a8a091; font-weight: 500;
}
.kb-option-name {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.kb-option-meta {
  flex-shrink: 0; font-size: 12px; color: #9a8f7e;
}

.query-row { display: flex; }
.search-shell { width: 100%; padding-right: 8px; }
.query-row input {
  flex: 1; min-width: 0; border: 0; outline: none; box-shadow: none;
  background: transparent; padding: 0; font-size: 15px;
}
.query-row input::placeholder { color: #b3ab9f; }
.query-submit {
  border: 0; outline: none; cursor: pointer; flex-shrink: 0;
  min-width: 92px; height: 40px; padding: 0 18px; border-radius: 12px;
  background: linear-gradient(135deg, #171717, #3a342b);
  color: #fff; font-size: 14px; font-weight: 700; font-family: var(--font);
  box-shadow: 0 10px 24px rgba(23, 23, 23, 0.16);
  transition: transform 150ms, box-shadow 150ms, opacity 150ms;
}
.query-submit:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 14px 28px rgba(23, 23, 23, 0.22);
}
.query-submit:disabled {
  opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none;
}

.results { display: flex; flex-direction: column; gap: 14px; }

.think-card {
  border: 1px solid #e6ddf5; border-radius: 18px; overflow: hidden;
  background: linear-gradient(180deg, #fbf8ff, #f6f1ff);
  box-shadow: 0 10px 30px rgba(124, 58, 237, 0.06);
}
.think-toggle { display: flex; align-items: center; gap: 6px; padding: 12px 16px; cursor: pointer; user-select: none; font-size: 13px; color: #7c3aed; font-weight: 600; transition: background 150ms; }
.think-toggle:hover { background: rgba(124, 58, 237, 0.04); }
.think-icon { transition: transform 200ms; color: #7c3aed; }
.think-icon.open { transform: rotate(180deg); }
.think-content { padding: 0 16px 14px; font-size: 13px; line-height: 1.65; color: #6b7280; border-top: 1px solid #e6ddf5; padding-top: 12px; }

.content-row { display: flex; gap: 20px; align-items: flex-start; }

.answer-col { flex: 1; min-width: 0; }
.answer-card {
  border: 1px solid #ebe6dc; border-radius: 22px; padding: 18px 18px 16px;
  background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,246,241,0.95));
  box-shadow:
    0 1px 0 rgba(255,255,255,0.95) inset,
    0 18px 40px rgba(23, 23, 23, 0.04);
}
.answer-card h4 {
  font-size: 13px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
  color: #7c6f5b; letter-spacing: 0.2px;
}
.answer-card h4 svg {
  width: 28px; height: 28px; padding: 6px; border-radius: 10px;
  background: linear-gradient(180deg, #fff, #f3ede3);
  border: 1px solid rgba(161, 98, 7, 0.12); color: #8b7c67;
}
.answer-card .answer-text { font-size: 14px; line-height: 1.7; overflow-y: auto; }
.answer-text.empty-hint { color: var(--c-secondary); font-size: 13px; display: flex; align-items: center; gap: 8px; }

.answer-card.streaming .markdown-body::after { content: '|'; animation: blink 0.7s step-end infinite; font-weight: 100; color: var(--c-secondary); }
@keyframes blink { 50% { opacity: 0; } }

.sources-col {
  width: 260px; flex-shrink: 0; overflow: hidden; max-height: calc(100vh - 200px); display: flex; flex-direction: column;
  border: 1px solid #ebe6dc; border-radius: 22px;
  background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,246,241,0.95));
  box-shadow:
    0 1px 0 rgba(255,255,255,0.95) inset,
    0 18px 40px rgba(23, 23, 23, 0.04);
}
.sources-header {
  display: flex; align-items: center; gap: 8px; padding: 14px 16px; font-size: 12px;
  color: #7c6f5b; font-weight: 700; border-bottom: 1px solid #eee7da; flex-shrink: 0;
}
.sources-header svg {
  width: 28px; height: 28px; padding: 6px; border-radius: 10px;
  background: linear-gradient(180deg, #fff, #f3ede3);
  border: 1px solid rgba(161, 98, 7, 0.12); color: #8b7c67;
}
.sources-hint {
  display: flex; align-items: center; gap: 4px;
  padding: 8px 16px; font-size: 10px; color: #948674;
  border-bottom: 1px solid #eee7da; background: rgba(255,255,255,0.55);
}
.sources-scroll { overflow-y: auto; flex: 1; padding: 10px; display: flex; flex-direction: column; gap: 8px; }

.source-chip {
  border: 1px solid #ece6db; border-radius: 16px; background: rgba(255,255,255,0.82);
  transition: border-color 150ms, background 150ms, box-shadow 150ms, transform 150ms;
  border-left: 3px solid var(--src-color);
}
.source-chip:hover { border-color: #d9d2c7; transform: translateY(-1px); }
.source-chip.active { border-color: var(--src-color); background: #fff; }
.source-chip.highlight { border-color: var(--src-color); background: #fff; box-shadow: 0 0 0 2px color-mix(in srgb, var(--src-color) 20%, transparent), 0 10px 24px rgba(23, 23, 23, 0.06); }

.source-chip-top { display: flex; align-items: center; gap: 8px; padding: 10px 12px; font-size: 12px; cursor: pointer; user-select: none; }
.source-idx { width: 20px; height: 20px; border-radius: 6px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; color: #fff; }
.source-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-fg); font-weight: 600; font-size: 11px; }
.source-tag { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 999px; border: 1px solid; flex-shrink: 0; }
.source-pct { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 999px; flex-shrink: 0; }
.source-chevron { flex-shrink: 0; color: var(--c-secondary); transition: transform 200ms; }
.source-chevron.open { transform: rotate(180deg); }

.source-text {
  font-size: 12px; line-height: 1.6; color: var(--c-secondary); padding: 0 12px 12px;
  border-top: 1px solid #eee7da; padding-top: 10px; white-space: pre-wrap; max-height: 140px; overflow-y: auto;
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
.markdown-body code { background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; font-family: var(--font-mono, 'Consolas', monospace); }
.markdown-body pre { background: #1e1e1e; color: #d4d4d4; padding: 12px 16px; border-radius: 6px; overflow-x: auto; margin: 8px 0; line-height: 1.5; }
.markdown-body pre code { background: none; padding: 0; color: inherit; font-size: 13px; }
.markdown-body table { border-collapse: collapse; width: 100%; margin: 8px 0; }
.markdown-body th, .markdown-body td { border: 1px solid var(--c-border); padding: 6px 10px; text-align: left; font-size: 13px; }
.markdown-body th { background: #f9fafb; font-weight: 600; }
.markdown-body blockquote { border-left: 3px solid #7c3aed; padding: 4px 12px; margin: 8px 0; color: #6b7280; background: #f8f5ff; }
.markdown-body hr { border: none; border-top: 1px solid var(--c-border); margin: 12px 0; }
.markdown-body a { color: #7c3aed; }
.markdown-body strong { font-weight: 600; }
.markdown-body img { max-width: 100%; border-radius: 4px; }
</style>
