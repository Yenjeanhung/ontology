<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { TYPE_META } from './nodeMeta.js'
import { marked } from 'marked'

const FIXED_KEYS = ['answer', 'chunks', 'entities', 'subgraph', 'success', 'data', 'error', 'stdout', 'duration_ms', 'text', 'result',
  // 人工节点固定输出
  'approved', 'decision', 'comment', 'operator', 'decided_at', 'task_id', 'timed_out',
  // HTTP 节点固定输出
  'status_code', 'reason', 'headers', 'attempts']

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const type = computed(() => props.data?.nodeType || 'start')
const meta = computed(() => TYPE_META[type.value] || TYPE_META.start)
const hasReasoning = computed(() => type.value === 'agent' || type.value === 'llm')
const title = computed(() => props.data?.title || meta.value.name)
const isCondition = computed(() => type.value === 'condition')
const isHuman = computed(() => type.value === 'human')
// 人工节点：审批模式提供 true/false 双出口；表单模式仅单出口
const humanMode = computed(() => props.data?.config?.mode || 'approve')
const isBranch = computed(() => isCondition.value || (isHuman.value && humanMode.value === 'approve'))

// 人工节点挂起时挂在节点上的待办任务 id（由 node_waiting 事件写入）
const pendingTaskId = computed(() => props.data?.taskId || props.data?.task_id || '')

// 人工节点处理结果：卡片上直接展示「通过 / 驳回 / 已提交 + 处理人 + 意见」
const humanResult = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object') return null
  const decision = out.decision
  if (!decision) return null
  const map = { approved: '通过', rejected: '驳回', submitted: '已提交' }
  return {
    decision,
    label: map[decision] || decision,
    operator: out.operator || '',
    comment: out.comment || '',
  }
})

// 条件节点卡片规则预览
function rulePreviewText(node) {
  if (!node || node.combinator !== 'and' && node.combinator !== 'or') return ''
  const parts = (node.rules || []).map(rulePreviewText).filter(Boolean)
  if (!parts.length) return ''
  const join = node.combinator === 'and' ? ' AND ' : ' OR '
  let text = parts.join(join)
  if (parts.length > 1) text = `(${text})`
  return node.negate ? `NOT ${text}` : text
}
const conditionPreview = computed(() => {
  const cfg = props.data?.config || {}
  if (cfg.mode === 'advanced' && cfg.rule) return rulePreviewText(cfg.rule)
  if (cfg.mode !== 'advanced') return `${cfg.operator || '=='} ${cfg.left || '...'}`
  return ''
})
const status = computed(() => props.data?.status || '')
const elapsedText = computed(() => props.data?.elapsedText || '')
const currentStep = computed(() => props.data?.step || '')
const runningSteps = computed(() => props.data?.steps || [])

const STATUS_LABEL = { running: '运行中', succeeded: '完成', failed: '失败', skipped: '跳过', waiting: '待处理' }

function stripMd(s) {
  if (typeof s !== 'string') return String(s ?? '')
  return s
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/!\[.*?\]\(.+?\)/g, '[图片]')
    .replace(/\n+/g, ' ')
    .trim()
}

function renderMd(s) {
  if (typeof s !== 'string') return String(s ?? '')
  return marked.parse(s, { breaks: true, gfm: true })
}

function bodyText(t, cfg = {}) {
  if (t === 'agent') return cfg.agent_id ? '引用已配置智能体' : (cfg.kb_id ? '内联 · KB + 技能' : '未配置知识库')
  if (t === 'service') return cfg.entity_id ? '实体服务' : (cfg.service_id ? '本体服务' : '未配置服务')
  if (t === 'llm') return cfg.prompt_template ? '自定义提示词' : '通用补全'
  if (t === 'condition') {
    if (cfg.mode === 'advanced' && cfg.rule) return conditionPreview.value || '规则未配置'
    return `${cfg.operator || '=='} ${cfg.left || '...'}`
  }
  if (t === 'code') return '沙箱 Python'
  if (t === 'http') {
    const method = (cfg.method || 'GET').toUpperCase()
    let host = ''
    try { host = new URL((cfg.url || '').replace(/\{\{[^{}]*\}\}/g, 'x')).host } catch { host = cfg.url || '' }
    return `${method} ${host}`
  }
  if (t === 'human') {
    const mode = cfg.mode || 'approve'
    if (mode === 'form') {
      const n = (cfg.form_fields || []).length
      return n ? `人工填写 · ${n} 个字段` : '人工填写（未配置字段）'
    }
    return `人工审批 · ${(cfg.display_fields || []).length} 项待审`
  }
  if (t === 'end') return '汇总输出'
  return '运行入口'
}

// ── 运行结果摘要：节点卡片上直接展示关键输出 ───
function fmtVal(v, maxLen = 40) {
  if (v == null) return 'null'
  if (typeof v === 'string') return v.length > maxLen ? v.slice(0, maxLen) + '…' : v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return `[${v.length} 项]`
  try { return JSON.stringify(v).slice(0, maxLen) } catch { return String(v) }
}

// 弹窗内展示值：字符串/数字直接显示；数组/对象展示实际 JSON，便于查看结构化内容
function fmtValPop(v, maxLen = 300) {
  if (v == null) return 'null'
  if (typeof v === 'string') return v.length > maxLen ? v.slice(0, maxLen) + '…' : v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    const s = JSON.stringify(v)
    return s.length > maxLen ? s.slice(0, maxLen) + '…' : s
  } catch { return String(v) }
}
// 弹窗内完整值：双击展开后使用，支持滚动与复制
function fmtValPopFull(v) {
  if (v == null) return 'null'
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

// 自定义（非固定）字段的键值对：智能体结构化输出（count/names 等）一眼可见
const customOuts = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object' || Array.isArray(out)) return []
  return Object.entries(out)
    .filter(([k]) => !FIXED_KEYS.includes(k) && !k.startsWith('_') && (hasReasoning.value || k !== 'reasoning'))
    .map(([k, v]) => ({ k, v: fmtVal(v) }))
    .filter(({ v }) => v != null && v !== '')
    .slice(0, 6)
})

// answer/text 摘要一行（去除 markdown 标记，卡片上更清爽）
const answerPreview = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object') return ''
  const s = out.answer ?? out.text ?? ''
  const plain = typeof s === 'string' ? stripMd(s) : ''
  return plain.length > 60 ? plain.slice(0, 60) + '…' : plain
})

// 运行中流式输出预览（取 answer/text 当前累积内容，不截断，用于容器内滚动）
const streamPreview = computed(() => {
  if (status.value !== 'running') return ''
  const out = props.data?.output
  if (!out || typeof out !== 'object') return ''
  const s = out.answer ?? out.text ?? ''
  return typeof s === 'string' ? stripMd(s) : ''
})

// 运行中反思/思考过程预览（与答案并行流式展示，不截断）
const reasoningPreview = computed(() => {
  if (status.value !== 'running' || !hasReasoning.value) return ''
  const out = props.data?.output
  if (!out || typeof out !== 'object') return ''
  const s = out.reasoning ?? ''
  return typeof s === 'string' ? stripMd(s) : ''
})

// 运行中预览容器自动滚到最底部，保证始终看到最新输出
const answerScrollRef = ref(null)
const reasoningScrollRef = ref(null)
function scrollToBottom(el) {
  if (!el) return
  nextTick(() => {
    el.scrollTop = el.scrollHeight
  })
}
watch(streamPreview, () => scrollToBottom(answerScrollRef.value))
watch(reasoningPreview, () => scrollToBottom(reasoningScrollRef.value))

const showTooltip = computed(() => !!props.data?.output && typeof props.data.output === 'object')

// 主输出文本（answer/text）与思考过程
const mainText = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object') return ''
  const s = out.answer ?? out.text ?? ''
  return typeof s === 'string' ? s : ''
})

// 代码/服务节点：卡片上直接展示 data 返回值（无 answer/text 时）
const codePreview = computed(() => {
  if (type.value !== 'code' && type.value !== 'service') return ''
  const out = props.data?.output
  if (!out || typeof out !== 'object') return ''
  if (status.value === 'failed') {
    const e = out.error ?? ''
    return typeof e === 'string' ? e : ''
  }
  if (out.data !== undefined) return fmtVal(out.data, 60)
  if (out.stdout) return fmtVal(out.stdout, 60)
  return ''
})

const reasoningText = computed(() => {
  if (!hasReasoning.value) return ''
  const out = props.data?.output
  if (!out || typeof out !== 'object') return ''
  const s = out.reasoning ?? ''
  return typeof s === 'string' ? s : ''
})

const nodeRef = ref(null)

// 完整输出浮层
const outOpen = ref(false)
const fixedPopExpanded = ref(false)
const rawJsonExpanded = ref(false)
const reasoningExpanded = ref(true)
const mainExpanded = ref(true)
const customExpanded = ref(true)
const copied = ref(false)
const expandedPopKey = ref('')

const popX = ref(0)
const popY = ref(0)
function updatePopPos() {
  const nodeEl = nodeRef.value
  if (!nodeEl) return
  const rect = nodeEl.getBoundingClientRect()
  popX.value = rect.left
  popY.value = rect.bottom + 6
}

function togglePopKey(key) {
  expandedPopKey.value = expandedPopKey.value === key ? '' : key
}

watch(outOpen, (open) => {
  if (open) updatePopPos()
})
let copiedTimer = null

function isMultiLine(v) {
  const full = fmtValPopFull(v)
  return typeof full === 'string' && full.includes('\n')
}

// 弹窗用：固定输出分组（默认折叠），排除 answer/text/reasoning 避免重复展示
const popFixedOuts = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object' || Array.isArray(out)) return []
  return Object.entries(out)
    .filter(([k]) => FIXED_KEYS.includes(k) && !k.startsWith('_') && !['answer', 'text', 'reasoning'].includes(k))
    .map(([k, v]) => ({ k, v: fmtValPop(v), full: fmtValPopFull(v), multiLine: isMultiLine(v) }))
})

// 弹窗用：自定义输出分组（默认展开，优先展示）
const popCustomOuts = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object' || Array.isArray(out)) return []
  return Object.entries(out)
    .filter(([k]) => !FIXED_KEYS.includes(k) && !k.startsWith('_') && (hasReasoning.value || k !== 'reasoning'))
    .map(([k, v]) => ({ k, v: fmtValPop(v), full: fmtValPopFull(v), multiLine: isMultiLine(v) }))
})

function fullOutputJson() {
  try { return JSON.stringify(props.data.output, null, 2) } catch { return String(props.data.output) }
}

async function copyOutputJson() {
  const text = fullOutputJson()
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    copied.value = true
    clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => { copied.value = false }, 1500)
  } catch (e) {
    alert('复制失败：' + (e?.message || '未知错误'))
  }
}
</script>

<template>
  <div ref="nodeRef" class="wf-node" :class="[status, { selected, condition: isCondition, human: isHuman }]" :style="{ '--nc': meta.color }">
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <template v-if="isBranch">
      <Handle id="true" type="source" :position="Position.Right" class="wf-handle wf-handle-true" style="top: 34%" />
      <Handle id="false" type="source" :position="Position.Right" class="wf-handle wf-handle-false" style="top: 66%" />
    </template>
    <Handle v-else type="source" :position="Position.Right" class="wf-handle" />

    <div class="wf-head">
      <div class="wf-head-row">
        <span class="wf-ico" :style="{ background: meta.color }" v-html="meta.icon"></span>
        <div class="wf-title" :title="title">{{ title }}</div>
      </div>
      <div class="wf-head-meta">
        <span v-if="status" class="wf-status-chip" :class="'sc-' + status">
          <span v-if="status === 'running'" class="wf-pulse"></span>
          {{ STATUS_LABEL[status] }}<template v-if="elapsedText"> · {{ elapsedText }}</template>
        </span>
      </div>
    </div>
    <div class="wf-body">{{ bodyText(type, props.data?.config) }}</div>

    <!-- 人工节点：等待人工处理时的提示条 -->
    <div class="wf-waiting" v-if="status === 'waiting'">
      <span class="wf-pulse"></span>
      {{ humanMode === 'form' ? '等待填写并提交' : '等待人工审批' }}
      <span v-if="pendingTaskId" class="wf-waiting-task">#{{ pendingTaskId }}</span>
    </div>

    <!-- 人工节点处理结果：通过 / 驳回 / 已提交 + 处理人 + 意见 -->
    <div class="wf-human-result" v-if="status === 'succeeded' && humanResult">
      <span class="wf-hr-badge" :class="'wf-hr-' + humanResult.decision">✓ {{ humanResult.label }}</span>
      <span v-if="humanResult.operator" class="wf-hr-op">{{ humanResult.operator }}</span>
      <span v-if="humanResult.comment" class="wf-hr-cm" :title="humanResult.comment">{{ humanResult.comment }}</span>
    </div>

    <!-- 运行时显示全部步骤 + 流式输出预览；运行后：卡片只展示自定义输出；固定输出进弹窗查看 -->
    <div class="wf-out" v-if="status === 'succeeded' || status === 'failed' || status === 'running'">
      <div v-if="status === 'running'" class="wf-out-running" @click.stop="outOpen = !outOpen" title="点击展开完整输出">
        <div class="wf-out-steps">
          <div v-for="(s, i) in runningSteps" :key="i" class="wf-step-line" :class="{ 'wf-step-current': i === runningSteps.length - 1 }">
            <span class="wf-step-dot"></span>
            <span class="wf-step-label">{{ s }}</span>
          </div>
        </div>
        <div v-if="reasoningPreview" ref="reasoningScrollRef" class="wf-stream-reasoning" title="模型思考过程">
          <span class="wf-stream-r-label">思考</span>
          <span class="wf-stream-r-text">{{ reasoningPreview }}</span>
        </div>
        <div v-if="streamPreview" ref="answerScrollRef" class="wf-stream-answer">
          <span class="wf-stream-text">{{ streamPreview }}</span>
        </div>
      </div>
      <template v-else-if="status !== 'running'">
        <div class="wf-out-kvs" v-if="customOuts.length">
          <span v-for="o in customOuts" :key="o.k" class="wf-out-kv" @click.stop="outOpen = !outOpen" :title="`${o.k}（点击查看完整输出）`">
            <i>{{ o.k }}</i><b>{{ o.v }}</b>
          </span>
        </div>
        <div v-else-if="answerPreview" class="wf-out-answer" @click.stop="outOpen = !outOpen">{{ answerPreview }}</div>
        <div v-else-if="codePreview" class="wf-out-answer wf-out-code" @click.stop="outOpen = !outOpen">{{ codePreview }}</div>
        <div v-else class="wf-out-raw" @click.stop="outOpen = !outOpen">✓ 已执行</div>
      </template>

      <!-- 点击输出区弹出的完整输出浮层：通过 Teleport 到 body 避免被其它节点遮挡 -->
      <Teleport v-if="outOpen" to="body">
        <div class="wf-out-pop" :style="{ left: popX + 'px', top: popY + 'px' }" @click.stop @mousedown.stop @pointerdown.stop @wheel.stop>
          <div class="wf-pop-head">
            <span>输出 · {{ title }}</span>
            <span class="wf-pop-close" @click.stop="outOpen = false">×</span>
          </div>

        <!-- 思考过程（模型反思/推理内容）-->
        <div v-if="reasoningText" class="wf-pop-section">
          <div class="wf-pop-sec-title wf-pop-toggle" @click.stop="reasoningExpanded = !reasoningExpanded">
            <span>思考过程</span>
            <span class="wf-toggle-ico">{{ reasoningExpanded ? '▲' : '▼' }}</span>
          </div>
          <pre v-if="reasoningExpanded" class="wf-pop-reasoning">{{ reasoningText }}</pre>
        </div>

        <!-- 主输出：渲染为优雅 Markdown -->
        <div v-if="mainText" class="wf-pop-section">
          <div class="wf-pop-sec-title wf-pop-toggle" @click.stop="mainExpanded = !mainExpanded">
            <span>模型输出</span>
            <span class="wf-toggle-ico">{{ mainExpanded ? '▲' : '▼' }}</span>
          </div>
          <div v-if="mainExpanded" class="wf-pop-md" v-html="renderMd(mainText)"></div>
        </div>

        <!-- 自定义输出：默认展开，优先展示 -->
        <div v-if="popCustomOuts.length" class="wf-pop-section">
          <div class="wf-pop-sec-title wf-pop-toggle" @click.stop="customExpanded = !customExpanded">
            <span>自定义输出（{{ popCustomOuts.length }} 项）</span>
            <span class="wf-toggle-ico">{{ customExpanded ? '▲' : '▼' }}</span>
          </div>
          <div v-if="customExpanded" class="wf-pop-kvs">
            <div v-for="o in popCustomOuts" :key="o.k" class="wf-pop-kv">
              <span class="wf-pop-k">{{ o.k }}</span>
              <span
                class="wf-pop-v"
                :class="{ 'wf-pop-v-expanded': expandedPopKey === 'custom:' + o.k, 'wf-pop-v-collapsed': expandedPopKey !== 'custom:' + o.k && o.multiLine }"
                :title="expandedPopKey === 'custom:' + o.k ? '' : String(o.full)"
                @dblclick.stop="o.multiLine && togglePopKey('custom:' + o.k)"
              >
                <span class="wf-pop-v-text">{{ expandedPopKey === 'custom:' + o.k ? o.full : o.v }}</span>
              </span>
            </div>
          </div>
        </div>

        <!-- 固定输出：默认折叠 -->
        <div v-if="popFixedOuts.length" class="wf-pop-section">
          <div class="wf-pop-sec-title wf-pop-toggle" @click.stop="fixedPopExpanded = !fixedPopExpanded">
            <span>固定输出（{{ popFixedOuts.length }} 项）</span>
            <span class="wf-toggle-ico">{{ fixedPopExpanded ? '▲' : '▼' }}</span>
          </div>
          <div v-if="fixedPopExpanded" class="wf-pop-kvs">
            <div v-for="o in popFixedOuts" :key="o.k" class="wf-pop-kv">
              <span class="wf-pop-k">{{ o.k }}</span>
              <span
                class="wf-pop-v"
                :class="{ 'wf-pop-v-expanded': expandedPopKey === 'fixed:' + o.k, 'wf-pop-v-collapsed': expandedPopKey !== 'fixed:' + o.k && o.multiLine }"
                :title="expandedPopKey === 'fixed:' + o.k ? '' : String(o.full)"
                @dblclick.stop="o.multiLine && togglePopKey('fixed:' + o.k)"
              >
                <span class="wf-pop-v-text">{{ expandedPopKey === 'fixed:' + o.k ? o.full : o.v }}</span>
              </span>
            </div>
          </div>
        </div>

        <!-- 原始 JSON：默认折叠，支持一键复制 -->
        <div class="wf-pop-section">
          <div class="wf-pop-sec-title wf-pop-toggle" @click.stop="rawJsonExpanded = !rawJsonExpanded">
            <span>原始 JSON</span>
            <span class="wf-toggle-ico">{{ rawJsonExpanded ? '▲' : '▼' }}</span>
          </div>
          <template v-if="rawJsonExpanded">
            <div class="wf-pop-json-bar">
              <button type="button" class="wf-pop-copy" @click.stop="copyOutputJson">{{ copied ? '已复制' : '复制 JSON' }}</button>
            </div>
            <pre class="wf-pop-pre">{{ fullOutputJson() }}</pre>
          </template>
        </div>
        </div>
      </Teleport>
    </div>
  </div>
</template>

<style scoped>
.wf-node {
  position: relative;
  width: 200px;
  background: var(--c-panel);
  border: 1px solid var(--c-border-strong, #d8cdbb);
  border-radius: var(--radius, 8px);
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
  font-family: var(--font);
  transition: box-shadow .15s, border-color .15s;
}
.wf-node:hover { box-shadow: 0 4px 14px rgba(0,0,0,.12); }
.wf-node.selected { border-color: var(--c-accent); box-shadow: 0 0 0 2px var(--c-accent-weak, rgba(161,98,7,.10)); }
/* 运行中：只有边框变色 + 徽章圆点闪，卡片本体完全静止（不闪不呼吸） */
.wf-node.running { border-color: var(--c-accent); border-width: 1.5px; }
.wf-node.running .n-head { background: var(--c-accent-weak, rgba(161,98,7,.10)); }

.wf-status-chip {
  position: absolute; top: -11px; right: 8px; z-index: 5;
  display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
  padding: 2px 8px; border-radius: 999px; font-size: 9.5px; font-weight: 700; line-height: 1.5;
  box-shadow: 0 1px 3px rgba(0,0,0,.12);
}
.wf-status-chip.sc-running { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 16%, var(--c-panel)); border: 1px solid var(--c-accent); }
.wf-status-chip.sc-succeeded { color: var(--c-success); background: color-mix(in srgb, var(--c-success) 16%, var(--c-panel)); border: 1px solid var(--c-success); }
.wf-status-chip.sc-failed { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 16%, var(--c-panel)); border: 1px solid var(--c-danger); }
.wf-status-chip.sc-skipped { color: var(--c-secondary); background: var(--c-panel); border: 1px solid var(--c-border); }
.wf-status-chip.sc-waiting { color: #b45309; background: color-mix(in srgb, #d97706 16%, var(--c-panel)); border: 1px solid #d97706; }
.wf-pulse {
  width: 7px; height: 7px; border-radius: 50%; background: currentColor;
  animation: wf-dot 1s ease-in-out infinite;
}
@keyframes wf-dot { 50% { opacity: .2; } }
.wf-node.succeeded { border-color: var(--c-success); }
.wf-node.failed { border-color: var(--c-danger); }
.wf-node.skipped { opacity: .45; }

.wf-head {
  display: flex; flex-direction: column; gap: 4px; padding: 8px 10px;
  border-bottom: 1px solid var(--c-border); border-radius: var(--radius, 8px) var(--radius, 8px) 0 0;
}
.wf-node.succeeded .wf-head { background: rgba(22,163,74,.08); }
.wf-node.failed .wf-head { background: rgba(220,38,38,.08); }
.wf-node.running .wf-head { background: var(--c-accent-weak, rgba(161,98,7,.10)); }

.wf-head-row {
  display: flex; align-items: center; gap: 8px; min-width: 0;
}
.wf-head-meta {
  display: flex; align-items: center; justify-content: flex-end; gap: 6px;
  min-height: 16px;
}

.wf-ico {
  width: 22px; height: 22px; border-radius: 6px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; color: #fff; font-size: 12px;
}
.wf-ico svg { width: 14px; height: 14px; }
.wf-title {
  font-size: 12.5px; font-weight: 700; color: var(--c-fg);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;
}
.wf-body { padding: 7px 10px; font-size: 11px; color: var(--c-secondary); min-height: 20px; }

/* 运行结果展示区 */
.wf-out { border-top: 1px dashed var(--c-border); padding: 6px 10px 8px; display: flex; flex-direction: column; gap: 5px; background: color-mix(in srgb, var(--c-accent) 4%, var(--c-panel)); border-radius: 0 0 var(--radius, 8px) var(--radius, 8px); }
.wf-out-answer {
  font-size: 9.5px; color: var(--c-secondary); line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  cursor: pointer;
}
.wf-out-answer:hover { color: var(--c-accent); }
.wf-out-code { font-family: ui-monospace, monospace; white-space: normal; word-break: break-all; }
.wf-out-kv { cursor: pointer; }
.wf-out-running { display: flex; flex-direction: column; gap: 6px; cursor: pointer; }
.wf-out-steps { display: flex; flex-direction: column; gap: 3px; }
.wf-step-line {
  display: flex; align-items: center; gap: 5px;
  font-size: 10px; line-height: 1.4; color: var(--c-secondary);
}
.wf-step-line.wf-step-current { color: var(--c-fg); }
.wf-step-dot {
  flex-shrink: 0; width: 5px; height: 5px; border-radius: 50%;
  background: var(--c-accent); opacity: .7;
}
.wf-step-current .wf-step-dot { opacity: 1; animation: wf-pulse 1.2s infinite; }
.wf-step-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wf-stream-text {
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden; word-break: break-all;
}
.wf-stream-answer {
  max-height: 100px; overflow-y: scroll;
  padding: 4px 6px; border-radius: 6px;
  background: var(--c-bg-soft, rgba(255,255,255,.05));
  font-size: 10px; line-height: 1.45; color: var(--c-fg);
  scroll-behavior: auto;
}
.wf-stream-answer .wf-stream-text {
  display: block; white-space: normal; word-break: break-all;
  -webkit-line-clamp: unset;
}
@keyframes wf-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: .35; }
}
.wf-stream-reasoning {
  display: flex; align-items: flex-start; gap: 5px;
  max-height: 100px; overflow-y: scroll;
  padding: 4px 6px; border-radius: 6px;
  background: var(--c-accent-weak, rgba(161,98,7,.08));
  font-size: 10px; line-height: 1.45; color: var(--c-secondary);
  scroll-behavior: auto;
}
.wf-stream-reasoning::-webkit-scrollbar,
.wf-stream-answer::-webkit-scrollbar { width: 3px; }
.wf-stream-reasoning::-webkit-scrollbar-thumb,
.wf-stream-answer::-webkit-scrollbar-thumb { background: rgba(255,255,255,.15); border-radius: 2px; }
.wf-stream-r-label {
  flex-shrink: 0; font-weight: 600; color: var(--c-accent);
  border: 1px solid var(--c-accent, transparent); border-radius: 4px;
  padding: 0 3px; font-size: 9px; line-height: 1.5;
}
.wf-stream-r-text {
  white-space: normal; word-break: break-all;
}
.wf-out-kv:hover { border-color: var(--c-accent); }

/* 完整输出浮层：卡片下方弹出，主题化样式 */
.wf-out-pop {
  position: fixed; z-index: 1000;
  width: 420px; max-width: 80vw; max-height: 70vh;
  background: var(--c-panel-elevated, var(--c-panel));
  border: 1px solid var(--c-border); border-radius: 10px;
  box-shadow: 0 12px 32px rgba(0,0,0,.18);
  overflow-x: hidden; overflow-y: auto;
  animation: wf-pop-in .12s ease-out;
}
@keyframes wf-pop-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
.wf-pop-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 10px; font-size: 11px; font-weight: 700; color: var(--c-secondary);
  border-bottom: 1px solid var(--c-border); background: var(--c-muted);
}
.wf-pop-close {
  cursor: pointer; color: var(--c-secondary); font-size: 18px; font-weight: 700;
  line-height: 1; padding: 6px 10px; margin: -4px -6px -4px 0;
  border-radius: 6px; user-select: none;
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 28px; min-height: 28px;
}
.wf-pop-close:hover { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 10%, transparent); }
.wf-pop-pre {
  margin: 0; padding: 10px; max-height: 300px; overflow-y: auto;
  font-family: ui-monospace, monospace; font-size: 10.5px; line-height: 1.55;
  white-space: pre-wrap; word-break: break-all; color: var(--c-fg);
  background: color-mix(in srgb, var(--c-fg) 4%, var(--c-panel));
  border-top: 1px solid var(--c-border);
  cursor: text;
  user-select: text;
}
.wf-pop-json-bar {
  display: flex; justify-content: flex-end;
  padding: 6px 10px; background: var(--c-panel);
}
.wf-pop-copy {
  padding: 3px 10px; border-radius: 5px;
  font-size: 10.5px; color: var(--c-accent);
  background: color-mix(in srgb, var(--c-accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--c-accent) 25%, transparent);
  cursor: pointer;
}
.wf-pop-copy:hover { background: color-mix(in srgb, var(--c-accent) 18%, transparent); }
.wf-pop-section { border-bottom: 1px solid var(--c-border); }
.wf-pop-section:last-child { border-bottom: none; }
.wf-pop-sec-title {
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 10px; font-size: 11px; font-weight: 700;
  color: var(--c-secondary); background: var(--c-muted);
}
.wf-pop-toggle { cursor: pointer; user-select: none; }
.wf-pop-toggle:hover { color: var(--c-fg); }
.wf-toggle-ico { font-size: 10px; }
.wf-pop-kvs {
  padding: 8px 10px; display: flex; flex-direction: column; gap: 6px;
  cursor: text; user-select: text;
}
.wf-pop-kv {
  display: grid; grid-template-columns: auto 1fr; align-items: start; gap: 8px;
  font-size: 10.5px; font-family: ui-monospace, monospace;
}
.wf-pop-k { flex-shrink: 0; color: var(--c-accent); font-weight: 700; }
.wf-pop-v {
  display: flex; align-items: flex-start; gap: 4px;
  flex: 1 1 auto; min-width: 0;
  color: var(--c-fg); overflow-wrap: anywhere;
}
.wf-pop-v-text { flex: 1 1 auto; min-width: 0; }
.wf-pop-v-collapsed .wf-pop-v-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.wf-pop-v-expanded {
  display: block;
  background: rgba(255,255,255,0.04);
  border-radius: 4px;
  padding: 4px 6px;
}
.wf-pop-v-expanded .wf-pop-v-text {
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  user-select: text;
  cursor: text;
}
.wf-pop-reasoning {
  margin: 0; padding: 10px; max-height: 260px; overflow-y: auto;
  font-family: ui-monospace, monospace; font-size: 11px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-all; color: var(--c-secondary);
  background: color-mix(in srgb, var(--c-fg) 3%, var(--c-panel));
  border-top: 1px solid var(--c-border);
}
.wf-pop-md {
  padding: 10px; font-size: 13px; line-height: 1.7; color: var(--c-fg);
  max-height: 320px; overflow-y: auto; user-select: text; cursor: text;
}
.wf-pop-md :deep(p) { margin: 0 0 8px; }
.wf-pop-md :deep(p:last-child) { margin-bottom: 0; }
.wf-pop-md :deep(strong) { font-weight: 700; color: var(--c-fg); }
.wf-pop-md :deep(code) { font-family: ui-monospace, monospace; font-size: 11.5px; padding: 1px 4px; border-radius: 4px; background: color-mix(in srgb, var(--c-fg) 8%, var(--c-panel)); color: var(--c-accent); }
.wf-pop-md :deep(pre) { margin: 6px 0; padding: 8px; border-radius: 6px; background: color-mix(in srgb, var(--c-fg) 6%, var(--c-panel)); overflow-x: auto; }
.wf-pop-md :deep(pre code) { background: transparent; padding: 0; color: var(--c-fg); }
.wf-pop-md :deep(ul), .wf-pop-md :deep(ol) { margin: 6px 0; padding-left: 18px; }
.wf-pop-md :deep(li) { margin: 2px 0; }
.wf-pop-md :deep(h1), .wf-pop-md :deep(h2), .wf-pop-md :deep(h3), .wf-pop-md :deep(h4) { font-size: 13px; margin: 10px 0 6px; color: var(--c-fg); }
.wf-pop-md :deep(blockquote) { margin: 6px 0; padding-left: 10px; border-left: 2px solid var(--c-border); color: var(--c-secondary); }
.wf-out-kvs { display: flex; flex-wrap: wrap; gap: 4px; }
.wf-out-kv {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 10.5px; font-family: ui-monospace, monospace;
  padding: 1px 7px; border-radius: 5px;
  background: color-mix(in srgb, var(--c-accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--c-accent) 25%, transparent);
  max-width: 100%;
}
.wf-out-kv i { font-style: normal; color: var(--c-accent); font-weight: 700; }
.wf-out-kv b { font-weight: 600; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 110px; }
.wf-out-raw { font-size: 10px; color: var(--c-secondary); }

/* 人工节点：等待人工处理态（琥珀色 + 呼吸） */
.wf-node.waiting { border-color: #d97706; border-width: 1.5px; }
.wf-node.waiting .wf-head { background: rgba(217,119,6,.10); }
.wf-waiting {
  display: flex; align-items: center; gap: 6px;
  margin: 0 10px 8px; padding: 4px 8px; border-radius: 6px;
  font-size: 10.5px; font-weight: 600; color: #b45309;
  background: rgba(217,119,6,.10); border: 1px dashed #d97706;
}
.wf-waiting .wf-pulse { color: #d97706; }
.wf-waiting-task { margin-left: auto; font-family: ui-monospace, monospace; font-weight: 500; opacity: .8; }

/* 人工节点处理结果 */
.wf-human-result {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 6px 10px; border-top: 1px dashed var(--c-border);
  font-size: 10.5px;
}
.wf-hr-badge {
  padding: 1px 7px; border-radius: 999px; font-weight: 700; font-size: 10px;
}
.wf-hr-approved { color: var(--c-success); background: color-mix(in srgb, var(--c-success) 15%, transparent); border: 1px solid var(--c-success); }
.wf-hr-rejected { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 15%, transparent); border: 1px solid var(--c-danger); }
.wf-hr-submitted { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 15%, transparent); border: 1px solid var(--c-accent); }
.wf-hr-op { color: var(--c-secondary); }
.wf-hr-cm {
  flex: 1 1 100%; color: var(--c-secondary); line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.wf-handle { width: 14px; height: 14px; border: 2px solid var(--c-border-strong, #d8cdbb); background: var(--c-panel); }
.wf-node:hover .wf-handle { border-color: var(--c-accent); }
.wf-handle-true { border-color: var(--c-success) !important; }
.wf-handle-false { border-color: var(--c-danger) !important; }
</style>
