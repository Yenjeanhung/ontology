<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { TYPE_META } from './nodeMeta.js'
import { marked } from 'marked'

const FIXED_KEYS = ['answer', 'chunks', 'entities', 'subgraph', 'success', 'data', 'error', 'stdout', 'duration_ms', 'text', 'result']

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const type = computed(() => props.data?.nodeType || 'start')
const meta = computed(() => TYPE_META[type.value] || TYPE_META.start)
const title = computed(() => props.data?.title || meta.value.name)
const isCondition = computed(() => type.value === 'condition')
const status = computed(() => props.data?.status || '')
const elapsedText = computed(() => props.data?.elapsedText || '')
const currentStep = computed(() => props.data?.step || '')
const runningSteps = computed(() => props.data?.steps || [])

const STATUS_LABEL = { running: '运行中', succeeded: '完成', failed: '失败', skipped: '跳过' }

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
  if (t === 'condition') return `${cfg.operator || '=='} ${cfg.left || '...'}`
  if (t === 'code') return '沙箱 Python'
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
    .filter(([k]) => !FIXED_KEYS.includes(k) && !k.startsWith('_'))
    .map(([k, v]) => ({ k, v: fmtVal(v) }))
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
  if (status.value !== 'running') return ''
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
const reasoningText = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object') return ''
  const s = out.reasoning ?? ''
  return typeof s === 'string' ? s : ''
})

// 完整输出浮层
const outOpen = ref(false)
const fixedPopExpanded = ref(false)
const rawJsonExpanded = ref(false)
const reasoningExpanded = ref(true)
const copied = ref(false)
const expandedPopKey = ref('')

function togglePopKey(key) {
  expandedPopKey.value = expandedPopKey.value === key ? '' : key
}
let copiedTimer = null

// 弹窗用：固定输出分组（默认折叠），排除 answer/text/reasoning 避免重复展示
const popFixedOuts = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object' || Array.isArray(out)) return []
  return Object.entries(out)
    .filter(([k]) => FIXED_KEYS.includes(k) && !k.startsWith('_') && !['answer', 'text', 'reasoning'].includes(k))
    .map(([k, v]) => ({ k, v: fmtValPop(v) }))
})

// 弹窗用：自定义输出分组（默认展开，优先展示）
const popCustomOuts = computed(() => {
  const out = props.data?.output
  if (!out || typeof out !== 'object' || Array.isArray(out)) return []
  return Object.entries(out)
    .filter(([k]) => !FIXED_KEYS.includes(k) && !k.startsWith('_'))
    .map(([k, v]) => ({ k, v: fmtValPop(v) }))
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
  <div class="wf-node" :class="[status, { selected, condition: isCondition }]" :style="{ '--nc': meta.color }">
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <template v-if="isCondition">
      <Handle id="true" type="source" :position="Position.Right" class="wf-handle wf-handle-true" style="top: 34%" />
      <Handle id="false" type="source" :position="Position.Right" class="wf-handle wf-handle-false" style="top: 66%" />
    </template>
    <Handle v-else type="source" :position="Position.Right" class="wf-handle" />

    <div class="wf-head">
      <span class="wf-ico" :style="{ background: meta.color }" v-html="meta.icon"></span>
      <div class="wf-title">{{ title }}</div>
      <span v-if="status" class="wf-status-chip" :class="'sc-' + status">
        <span v-if="status === 'running'" class="wf-pulse"></span>
        {{ STATUS_LABEL[status] }}<template v-if="elapsedText"> · {{ elapsedText }}</template>
      </span>
    </div>
    <div class="wf-body">{{ bodyText(type, props.data?.config) }}</div>

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
        <div v-else class="wf-out-raw" @click.stop="outOpen = !outOpen">✓ 已执行</div>
      </template>

      <!-- 点击输出区弹出的完整输出浮层：自定义输出优先，固定输出默认折叠 -->
      <div v-if="outOpen" class="wf-out-pop" @click.stop @mousedown.stop @pointerdown.stop @wheel.stop>
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
          <div class="wf-pop-sec-title">模型输出</div>
          <div class="wf-pop-md" v-html="renderMd(mainText)"></div>
        </div>

        <!-- 自定义输出：默认展开，优先展示 -->
        <div v-if="popCustomOuts.length" class="wf-pop-section">
          <div class="wf-pop-sec-title">自定义输出</div>
          <div class="wf-pop-kvs">
            <div v-for="o in popCustomOuts" :key="o.k" class="wf-pop-kv">
              <span class="wf-pop-k">{{ o.k }}</span>
              <span
                class="wf-pop-v"
                :class="{ 'wf-pop-v-expanded': expandedPopKey === 'custom:' + o.k }"
                :title="expandedPopKey === 'custom:' + o.k ? '' : String(o.full)"
                @dblclick.stop="togglePopKey('custom:' + o.k)"
              >{{ expandedPopKey === 'custom:' + o.k ? o.full : o.v }}</span>
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
                :class="{ 'wf-pop-v-expanded': expandedPopKey === 'fixed:' + o.k }"
                :title="expandedPopKey === 'fixed:' + o.k ? '' : String(o.full)"
                @dblclick.stop="togglePopKey('fixed:' + o.k)"
              >{{ expandedPopKey === 'fixed:' + o.k ? o.full : o.v }}</span>
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
  display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
  padding: 1px 7px; border-radius: 999px; font-size: 9.5px; font-weight: 700; line-height: 1.5;
}
.wf-status-chip.sc-running { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 14%, transparent); }
.wf-status-chip.sc-succeeded { color: var(--c-success); background: color-mix(in srgb, var(--c-success) 14%, transparent); }
.wf-status-chip.sc-failed { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 14%, transparent); }
.wf-status-chip.sc-skipped { color: var(--c-secondary); background: var(--c-muted); }
.wf-pulse {
  width: 7px; height: 7px; border-radius: 50%; background: currentColor;
  animation: wf-dot 1s ease-in-out infinite;
}
@keyframes wf-dot { 50% { opacity: .2; } }
.wf-node.succeeded { border-color: var(--c-success); }
.wf-node.failed { border-color: var(--c-danger); }
.wf-node.skipped { opacity: .45; }

.wf-head {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border-bottom: 1px solid var(--c-border); border-radius: var(--radius, 8px) var(--radius, 8px) 0 0;
}
.wf-node.succeeded .wf-head { background: rgba(22,163,74,.08); }
.wf-node.failed .wf-head { background: rgba(220,38,38,.08); }
.wf-node.running .wf-head { background: var(--c-accent-weak, rgba(161,98,7,.10)); }

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
  max-height: 100px; overflow-y: auto;
  padding: 4px 6px; border-radius: 6px;
  background: var(--c-bg-soft, rgba(255,255,255,.05));
  font-size: 10px; line-height: 1.45; color: var(--c-fg);
  scroll-behavior: smooth;
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
  max-height: 100px; overflow-y: auto;
  padding: 4px 6px; border-radius: 6px;
  background: var(--c-accent-weak, rgba(161,98,7,.08));
  font-size: 10px; line-height: 1.45; color: var(--c-secondary);
  scroll-behavior: smooth;
}
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
  position: absolute; top: calc(100% + 6px); right: 0; z-index: 30;
  width: 420px; max-width: 80vw;
  background: var(--c-panel-elevated, var(--c-panel));
  border: 1px solid var(--c-border); border-radius: 10px;
  box-shadow: 0 12px 32px rgba(0,0,0,.18);
  overflow: hidden;
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
  display: flex; align-items: flex-start; gap: 8px;
  font-size: 10.5px; font-family: ui-monospace, monospace;
}
.wf-pop-k { flex-shrink: 0; color: var(--c-accent); font-weight: 700; }
.wf-pop-v { color: var(--c-fg); word-break: break-all; }
.wf-pop-v-expanded {
  display: block;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
  cursor: text;
  background: rgba(255,255,255,0.04);
  border-radius: 4px;
  padding: 4px 6px;
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

.wf-handle { width: 9px; height: 9px; border: 2px solid var(--c-border-strong, #d8cdbb); background: var(--c-panel); }
.wf-node:hover .wf-handle { border-color: var(--c-accent); }
.wf-handle-true { border-color: var(--c-success) !important; }
.wf-handle-false { border-color: var(--c-danger) !important; }
</style>
