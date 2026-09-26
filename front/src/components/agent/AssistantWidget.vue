<script setup>
/**
 * 全局智能体浮标（AssistantWidget）：
 * - 右下角 FAB，系统所有页面可见；点击弹出轻量对话抽屉
 * - 单智能体架构：LLM + Function Calling 工具循环（/api/agent/assistant/run），
 *   与多智能体协作链路无耦合；技能 / 工具白名单 / 人设在智能体配置页维护
 * - SSE 事件：session / tools / token(可带 reasoning) / tool_call / tool_result /
 *   tool_calls / done / error
 * 设计文档：doc/智能体/单智能体/智能体浮标_功能设计.md
 */
import { computed, nextTick, ref } from 'vue'
import { marked } from 'marked'
import { streamAssistantRun } from '../../api'
import { isLoggedIn } from '../../api/auth'
import { hasPerm } from '../../stores/auth'
import { useEscClose } from '../../composables/useEscClose'

const open = ref(false)
const input = ref('')
const running = ref(false)
const sessionId = ref('')          // session 事件回传后续聊锚点
const msgs = ref([])               // {role:'user'|'assistant', content, reasoning, streaming, status, error, elapsed}
let abortCtrl = null
const listEl = ref(null)
const inputEl = ref(null)

const visible = computed(() => isLoggedIn() && hasPerm('agent:view'))

// 工具名 → 过程状态行中文（工具循环调用的轻量展示）
const TOOL_LABELS = {
  kb_search: '知识检索',
  graph_search: '图谱检索',
  data_query: '数据查询',
}

function renderMd(t) {
  try { return marked.parse(t || '', { breaks: true }) } catch { return t || '' }
}

function scrollBottom() {
  nextTick(() => {
    const el = listEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function toggle() {
  open.value = !open.value
  if (open.value) nextTick(() => inputEl.value?.focus())
}

function newChat() {
  if (running.value) return
  msgs.value = []
  sessionId.value = ''
}

function stop() {
  abortCtrl?.abort()
}

useEscClose(() => [[open.value, () => { open.value = false }]])

// ── SSE 事件 → 轻量消息状态 ──
function handleEvent(m, evt) {
  switch (evt.type) {
    case 'session':
      sessionId.value = evt.session_id || ''
      break
    case 'tools': {
      const n = (evt.tools || []).length
      m.status = n ? `就绪 · ${n} 个可用工具` : '就绪'
      break
    }
    case 'tool_call':
      m.status = `正在${TOOL_LABELS[evt.name] || evt.name}…`
      break
    case 'tool_result': {
      const ok = evt.ok !== false
      m.status = `${TOOL_LABELS[evt.name] || evt.name}${ok ? ' ✓' : ' 失败'}`
      break
    }
    case 'token':
      m.status = ''
      if (evt.reasoning) {
        // 思考链：灰色思考块流式展示，不进正文
        m.reasoning = (m.reasoning || '') + (evt.content || '')
      } else {
        if (evt.reset) m.content = ''
        m.content += evt.content || ''
      }
      scrollBottom()
      break
    case 'done':
      m.status = ''
      if (evt.conclusion && !m.content) m.content = evt.conclusion
      m.elapsed = evt.elapsed_ms || 0
      m.streaming = false
      scrollBottom()
      break
    case 'error':
      m.status = ''
      m.error = evt.content || '处理失败'
      m.streaming = false
      break
    default:
      break
  }
}

/** 发送一轮：文本 → 用户气泡 + 助手占位 → SSE 增量渲染。 */
async function send() {
  const task = input.value.trim()
  if (!task || running.value) return
  input.value = ''
  running.value = true
  abortCtrl?.abort()
  abortCtrl = new AbortController()
  const userMsg = { role: 'user', content: task }
  const ai = {
    role: 'assistant', content: '', reasoning: '',
    streaming: true,
    status: '思考中…',
    error: '', elapsed: 0,
  }
  msgs.value = [...msgs.value, userMsg, ai]
  scrollBottom()
  try {
    await streamAssistantRun(task, {
      onEvent: (evt) => handleEvent(ai, evt),
      signal: abortCtrl.signal,
      sessionId: sessionId.value || undefined,
    })
  } catch (err) {
    if (err?.name !== 'AbortError') ai.error = err?.message || '请求失败'
  } finally {
    ai.streaming = false
    running.value = false
    scrollBottom()
  }
}

function onInputKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

const canSend = computed(() => !running.value && !!input.value.trim())
</script>

<template>
  <template v-if="visible">
    <!-- 对话抽屉 -->
    <transition name="aw-pop">
      <div v-if="open" class="aw-panel">
        <div class="aw-head">
          <div class="aw-head-txt">
            <div class="aw-title">智能助手</div>
            <div class="aw-sub">功能咨询 · 查数据 · 查图谱 · 知识检索</div>
          </div>
          <div class="aw-head-ops">
            <button class="aw-icon-btn" title="新会话" :disabled="running" @click="newChat">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
            </button>
            <button class="aw-icon-btn" title="收起" @click="open = false">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
            </button>
          </div>
        </div>

        <div ref="listEl" class="aw-list">
          <div v-if="!msgs.length" class="aw-empty">
            <div class="aw-empty-title">有什么可以帮你？</div>
            <div class="aw-empty-hint">试试问我：</div>
            <button class="aw-chip" @click="send('这个系统有哪些功能？怎么使用？')">这个系统有哪些功能？</button>
            <button class="aw-chip" @click="send('介绍一下系统的本体管理模块')">本体管理模块怎么用？</button>
            <button class="aw-chip" @click="send('帮我看一下系统里有多少实体和关系')">系统里有多少实体和关系？</button>
          </div>

          <template v-for="(m, i) in msgs" :key="i">
            <div v-if="m.role === 'user'" class="aw-row aw-row-user">
              <div class="aw-bubble aw-bubble-user">{{ m.content }}</div>
            </div>
            <div v-else class="aw-row">
              <div class="aw-bubble aw-bubble-ai">
                <div v-if="m.status" class="aw-status"><span class="aw-dot-spin"></span>{{ m.status }}</div>
                <div v-if="m.reasoning" class="aw-think">{{ m.reasoning }}</div>
                <div v-if="m.content" class="aw-md" v-html="renderMd(m.content)"></div>
                <div v-if="m.streaming && !m.content && !m.status" class="aw-status"><span class="aw-dot-spin"></span>思考中…</div>

                <div v-if="m.error" class="aw-err">{{ m.error }}</div>
                <div v-if="m.elapsed" class="aw-meta">耗时 {{ (m.elapsed / 1000).toFixed(1) }}s</div>
              </div>
            </div>
          </template>
        </div>

        <div class="aw-input">
          <textarea
            ref="inputEl"
            v-model="input"
            class="aw-textarea"
            rows="1"
            placeholder="输入问题，Enter 发送 / Shift+Enter 换行"
            @keydown="onInputKey"
          ></textarea>
          <button v-if="running" class="aw-send aw-send-stop" title="停止" @click="stop">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
          </button>
          <button v-else class="aw-send" :disabled="!canSend" title="发送" @click="send()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>
          </button>
        </div>
      </div>
    </transition>

    <!-- 浮标 -->
    <button class="aw-fab" :class="{ on: open }" title="智能助手" @click="toggle">
      <span class="aw-fab-ring"></span>
      <svg v-if="!open" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="7" width="16" height="12" rx="3"/>
        <path d="M12 7V4"/><circle cx="12" cy="3" r="1" fill="currentColor" stroke="none"/>
        <circle cx="9" cy="13" r="1.2" fill="currentColor" stroke="none"/>
        <circle cx="15" cy="13" r="1.2" fill="currentColor" stroke="none"/>
      </svg>
      <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      <span v-if="running && !open" class="aw-fab-live"></span>
    </button>
  </template>
</template>

<style scoped>
/* ── 浮标 FAB ── */
.aw-fab {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 2400;
  width: 54px;
  height: 54px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 50%;
  color: #fff;
  cursor: pointer;
  background: linear-gradient(135deg, var(--c-accent), color-mix(in srgb, var(--c-accent) 62%, #3b82f6));
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  transition: transform 150ms;
}
.aw-fab:hover { transform: translateY(-2px); }
.aw-fab.on { background: var(--c-panel-2, var(--c-panel)); color: var(--c-accent); border: 1px solid var(--c-border); }
.aw-fab-ring {
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--c-accent) 55%, transparent);
  animation: aw-breath 2.4s ease-out infinite;
  pointer-events: none;
}
.aw-fab.on .aw-fab-ring { animation: none; opacity: 0; }
@keyframes aw-breath {
  0% { transform: scale(0.94); opacity: 0.9; }
  70% { transform: scale(1.12); opacity: 0; }
  100% { transform: scale(1.12); opacity: 0; }
}
.aw-fab-live {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #22c55e;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.25);
  animation: aw-pulse 1.2s ease-in-out infinite;
}
@keyframes aw-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

/* ── 抽屉 ── */
.aw-panel {
  position: fixed;
  right: 24px;
  bottom: 90px;
  z-index: 2400;
  width: 400px;
  max-width: calc(100vw - 32px);
  height: min(620px, 72vh);
  display: flex;
  flex-direction: column;
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 12px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.45);
  overflow: hidden;
}
.aw-pop-enter-active, .aw-pop-leave-active { transition: opacity 140ms, transform 140ms; }
.aw-pop-enter-from, .aw-pop-leave-to { opacity: 0; transform: translateY(12px) scale(0.98); }

.aw-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--c-border);
  background: var(--c-panel-2, transparent);
}
.aw-title { font-size: 14px; font-weight: 700; color: var(--c-fg); }
.aw-sub { font-size: 11px; color: var(--c-secondary); margin-top: 2px; }
.aw-head-ops { display: flex; gap: 4px; }
.aw-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--c-secondary);
  cursor: pointer;
}
.aw-icon-btn:hover:not(:disabled) { background: rgba(148, 163, 184, 0.12); color: var(--c-fg); }
.aw-icon-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── 消息区 ── */
.aw-list { flex: 1; min-height: 0; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.aw-row { display: flex; }
.aw-row-user { justify-content: flex-end; }
.aw-bubble { max-width: 86%; padding: 8px 12px; border-radius: 10px; font-size: 13px; line-height: 1.6; word-break: break-word; }
.aw-bubble-user { background: color-mix(in srgb, var(--c-accent) 18%, transparent); border: 1px solid color-mix(in srgb, var(--c-accent) 32%, transparent); color: var(--c-fg); white-space: pre-wrap; }
.aw-bubble-ai { background: var(--c-panel-2, rgba(148, 163, 184, 0.06)); border: 1px solid var(--c-border); color: var(--c-fg); }

.aw-status { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-secondary); margin-bottom: 4px; }
.aw-dot-spin {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--c-accent);
  animation: aw-blink 1s ease-in-out infinite;
  flex-shrink: 0;
}
@keyframes aw-blink { 0%, 100% { opacity: 0.25; } 50% { opacity: 1; } }

.aw-md :deep(p) { margin: 0 0 6px; }
.aw-md :deep(p:last-child) { margin-bottom: 0; }
.aw-md :deep(pre) { background: rgba(0, 0, 0, 0.25); border: 1px solid var(--c-border); border-radius: 6px; padding: 8px 10px; overflow-x: auto; font-size: 12px; margin: 6px 0; }
.aw-md :deep(code) { font-family: var(--font-mono, monospace); font-size: 12px; }
.aw-md :deep(ul), .aw-md :deep(ol) { margin: 4px 0; padding-left: 18px; }
.aw-md :deep(table) { border-collapse: collapse; margin: 6px 0; font-size: 12px; }
.aw-md :deep(th), .aw-md :deep(td) { border: 1px solid var(--c-border); padding: 4px 8px; }
.aw-md :deep(a) { color: var(--c-accent); }

.aw-chip {
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid color-mix(in srgb, var(--c-accent) 40%, var(--c-border));
  border-radius: 999px;
  background: transparent;
  color: var(--c-fg);
  cursor: pointer;
  text-align: left;
}
.aw-chip:hover:not(:disabled) { background: color-mix(in srgb, var(--c-accent) 16%, transparent); }
.aw-chip:disabled { opacity: 0.5; cursor: not-allowed; }

.aw-err { margin-top: 6px; font-size: 12.5px; color: #f87171; }
.aw-think {
  margin: 2px 0 6px;
  padding: 6px 8px;
  max-height: 150px;
  overflow-y: auto;
  font-size: 12px;
  line-height: 1.6;
  color: var(--c-secondary);
  background: color-mix(in srgb, var(--c-secondary) 8%, transparent);
  border-left: 2px solid var(--c-border);
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-word;
}
.aw-meta { margin-top: 6px; font-size: 11px; color: var(--c-secondary); opacity: 0.7; }

/* ── 空态 ── */
.aw-empty { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; padding: 8px 2px; }
.aw-empty-title { font-size: 14px; font-weight: 600; color: var(--c-fg); }
.aw-empty-hint { font-size: 12px; color: var(--c-secondary); }

/* ── 输入区 ── */
.aw-input { display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--c-border); background: var(--c-panel-2, transparent); }
.aw-textarea {
  flex: 1;
  min-width: 0;
  max-height: 96px;
  resize: none;
  padding: 8px 10px;
  font-size: 13px;
  font-family: var(--font);
  line-height: 1.5;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: transparent;
  color: var(--c-fg);
  outline: none;
  box-sizing: border-box;
}
.aw-textarea:focus { border-color: var(--c-accent); }
.aw-send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 8px;
  background: var(--c-accent);
  color: #fff;
  cursor: pointer;
  flex-shrink: 0;
}
.aw-send:disabled { opacity: 0.4; cursor: not-allowed; }
.aw-send-stop { background: #ef4444; }
</style>
