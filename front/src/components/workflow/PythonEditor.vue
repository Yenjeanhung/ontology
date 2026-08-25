<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, gutter, GutterMarker } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { bracketMatching, indentOnInput, foldGutter, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { closeBrackets, closeBracketsKeymap, autocompletion, CompletionContext } from '@codemirror/autocomplete'

const props = defineProps({
  modelValue: { type: String, default: '' },
  height: { type: [Number, String], default: 280 },
  maxLength: { type: Number, default: 50000 },
  /** service param names, used as completion source */
  params: { type: Array, default: () => [] },
  /** 允许 import 的模块白名单 */
  allowedImports: {
    type: Array,
    default: () => ['json', 're', 'math', 'datetime', 'random', 'collections', 'urllib', 'hashlib', 'base64', 'requests', 'httpx', 'time', 'os', 'sys', 'functools', 'itertools'],
  },
  /** 标准库高频成员补全 */
  memberImports: {
    type: Object,
    default: () => ({
      json: ['loads', 'dumps', 'load', 'dump'],
      re: ['match', 'search', 'findall', 'sub', 'compile', 'split'],
      math: ['sqrt', 'pow', 'floor', 'ceil', 'log', 'sin', 'cos', 'pi', 'e'],
      datetime: ['datetime', 'date', 'timedelta', 'timezone', 'now'],
      random: ['randint', 'choice', 'random', 'shuffle', 'sample', 'uniform'],
      collections: ['Counter', 'defaultdict', 'OrderedDict', 'deque'],
      requests: ['get', 'post', 'put', 'delete', 'Session'],
      httpx: ['get', 'post', 'AsyncClient', 'Client'],
      time: ['time', 'sleep', 'strftime', 'strptime', 'localtime'],
      hashlib: ['md5', 'sha256', 'sha1'],
      base64: ['b64encode', 'b64decode'],
    }),
  },
  enableLinter: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'selection-change', 'lint', 'drop-text'])

const host = ref(null)
let view = null

function insertAtCursor(text) {
  if (!view) return
  const pos = view.state.selection.main.head
  view.dispatch({
    changes: { from: pos, to: pos, insert: text },
    selection: { anchor: pos + text.length },
  })
  view.focus()
}

function onDrop(ev) {
  ev.preventDefault()
  ev.stopPropagation()
  const text = ev.dataTransfer.getData('text/plain')
  if (text) insertAtCursor(text)
}

function varPyRef(id, field) { return `var("${id}", "${field}")` }

const dropHandlers = EditorView.domEventHandlers({
  drop(ev) {
    // 工作流变量拖拽：优先读取元数据生成 var(...) 插入代码
    const hasVar = ev.dataTransfer?.types.includes('application/x-wf-var')
    if (hasVar) {
      const raw = ev.dataTransfer?.getData('application/x-wf-var')
      try {
        const { node, field } = JSON.parse(raw || '{}')
        if (node && field !== undefined) {
          ev.preventDefault()
          ev.stopPropagation()
          insertAtCursor(varPyRef(node, field))
          return true
        }
      } catch {}
    }
    const text = ev.dataTransfer?.getData('text/plain')
    if (text) {
      ev.preventDefault()
      ev.stopPropagation()
      insertAtCursor(text)
      return true
    }
    return false
  },
})

defineExpose({
  getSelection() {
    if (!view) return { from: 0, to: 0, text: '' }
    const sel = view.state.selection.main
    return { from: sel.from, to: sel.to, text: view.state.doc.sliceString(sel.from, sel.to) }
  },
  insertAtCursor,
})

// completion
const PY_KEYWORDS = ['def', 'return', 'import', 'from', 'for', 'if', 'elif', 'else', 'while', 'try', 'except', 'finally', 'with', 'lambda', 'class', 'yield', 'in', 'not', 'and', 'or', 'is', 'None', 'True', 'False', 'async', 'await', 'pass', 'break', 'continue', 'raise', 'assert', 'global', 'nonlocal', 'del']

function buildSnippets(word) {
  const out = []
  if (word === 'def') out.push({ label: 'def', type: 'keyword', apply: 'def ${1:name}(${2:params, entity, context}):\n    ${0:return {}}' })
  if (word === 'if') out.push({ label: 'if', type: 'keyword', apply: 'if ${1:cond}:\n    ${0:pass}' })
  if (word === 'for') out.push({ label: 'for', type: 'keyword', apply: 'for ${1:item} in ${2:items}:\n    ${0:pass}' })
  if (word === 'try') out.push({ label: 'try', type: 'keyword', apply: 'try:\n    ${1:pass}\nexcept Exception as e:\n    ${0:raise}' })
  if (word === 'with') out.push({ label: 'with', type: 'keyword', apply: 'with ${1:expr} as ${2:var}:\n    ${0:pass}' })
  return out
}

function myCompletions(ctx) {
  const before = ctx.matchBefore(/[\w.]+/)
  if (!before) return null
  const word = before.text
  const isDot = word.endsWith('.')

  // 模块成员补全： requests. / json. ...
  if (isDot) {
    const mod = word.slice(0, -1)
    const members = props.memberImports[mod]
    if (members) {
      return {
        from: before.from + mod.length + 1,
        options: members.map((m) => ({ label: m, type: 'property' })),
        validFor: /^\w*$/,
      }
    }
    return null
  }

  const options = []
  // 关键字
  for (const kw of PY_KEYWORDS) if (kw.startsWith(word)) options.push({ label: kw, type: 'keyword' })
  // snippets
  for (const s of buildSnippets(word)) options.push(s)
  // 参数名（params prop）
  for (const p of props.params) {
    const name = typeof p === 'string' ? p : (p?.name || p?.label || '')
    if (name && name.startsWith(word)) options.push({ label: name, type: 'variable', detail: '参数' })
  }
  // 允许 import 模块
  if (word && /^[a-z]/.test(word)) {
    for (const m of props.allowedImports) if (m.startsWith(word)) options.push({ label: m, type: 'module' })
  }
  if (!options.length) return null
  return { from: before.from, options, validFor: /^\w*$/ }
}

// ───── 轻量 Lint ─────
function runLint(text) {
  const diags = []
  const lines = text.split('\n')
  // 1. def run( 入口
  if (!/def\s+run\s*\(/.test(text)) {
    diags.push({ line: 0, sev: 'error', msg: '缺少入口函数 def run(params, entity, context)' })
  }
  // 2. 括号 / 引号匹配（栈扫描）
  const pairs = { ')': '(', ']': '[', '}': '{' }
  const opens = new Set(['(', '[', '{'])
  let stack = []
  let quote = null
  let lineNo = 0
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (ch === '\n') { lineNo++; continue }
    if (quote) {
      if (ch === '\\') { i++; continue }
      if (ch === quote) quote = null
      continue
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; continue }
    if (opens.has(ch)) stack.push({ ch, line: lineNo })
    else if (pairs[ch]) {
      const top = stack.pop()
      if (!top || top.ch !== pairs[ch]) diags.push({ line: lineNo, sev: 'error', msg: `括号不匹配：多余 ${ch}` })
    }
  }
  if (quote) diags.push({ line: lineNo, sev: 'error', msg: '引号未闭合' })
  for (const s of stack) diags.push({ line: s.line, sev: 'error', msg: `括号未闭合：${s.ch}` })
  // 3. 缩进混用（同块既有 tab 又有空格）
  let prevIndentType = null
  lines.forEach((ln, idx) => {
    if (!ln.trim()) return
    const m = ln.match(/^\s*/)[0]
    const hasTab = m.includes('\t')
    const hasSpace = m.includes(' ')
    if (hasTab && hasSpace && prevIndentType !== 'mixed') {
      diags.push({ line: idx, sev: 'warning', msg: '缩进混用 Tab 与空格（建议统一 4 空格）' })
      prevIndentType = 'mixed'
    } else if (hasTab) prevIndentType = 'tab'
    else if (hasSpace) prevIndentType = 'space'
  })
  return diags
}

// lint gutter 标记
const lintMarker = (sev) => new class extends GutterMarker {
  toDOM() { const d = document.createElement('span'); d.textContent = sev === 'error' ? '●' : '○'; d.style.color = sev === 'error' ? '#ef4444' : '#eab308'; d.style.fontSize = '9px'; d.style.paddingLeft = '4px'; return d }
}()
const lintGutterField = gutter({
  class: 'cm-lint-gutter',
  markers: (view) => {
    const marks = []
    const seen = new Set()
    for (const d of lintDiags.value) {
      if (seen.has(d.line)) continue
      seen.add(d.line)
      marks.push({ line: d.line, marker: lintMarker(d.sev) })
    }
    return marks
  },
  initialSpacer: () => lintMarker('error'),
})

const lintDiags = ref([])
const backendError = ref(null)
let backendLintTimer = null

function refreshLint() {
  if (!view) return
  const diags = props.enableLinter ? runLint(view.state.doc.toString()) : []
  if (backendError.value) {
    diags.push({ line: backendError.value.line, sev: 'error', msg: backendError.value.msg })
  }
  lintDiags.value = diags
  emit('lint', diags)
}

async function refreshBackendLint(text) {
  if (!props.enableLinter) return
  window.clearTimeout(backendLintTimer)
  backendLintTimer = window.setTimeout(async () => {
    try {
      const res = await fetch('/api/services/validate-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code_text: text }),
      })
      const data = await res.json()
      if (data.valid) {
        backendError.value = null
      } else {
        const m = (data.error || '').match(/第\s*(\d+)\s*行/)
        const line = m ? Math.max(0, parseInt(m[1], 10) - 1) : 0
        backendError.value = { line, msg: data.error }
      }
    } catch (e) {
      backendError.value = null
    }
    if (view) {
      view.dispatch({ effects: [] }) // trigger gutter redraw
      refreshLint()
    }
  }, 600)
}

function createState(doc) {
  const exts = [
    lineNumbers(),
    foldGutter(),
    highlightActiveLineGutter(),
    highlightActiveLine(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    history(),
    indentOnInput(),
    bracketMatching(),
    closeBrackets(),
    autocompletion({ override: [myCompletions] }),
    keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, indentWithTab]),
    python(),
    oneDark,
    lintGutterField,
    dropHandlers,
    EditorView.theme({
      '&': { height: typeof props.height === 'number' ? props.height + 'px' : props.height, fontSize: '13px' },
      '.cm-scroller': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', lineHeight: '1.6' },
      '.cm-content': { padding: '8px 0' },
      '&.cm-focused': { outline: 'none' },
    }),
    EditorView.updateListener.of((u) => {
      try {
        if (u.docChanged) {
          const text = u.state.doc.toString()
          if (props.maxLength && text.length > props.maxLength) {
            view.dispatch({ changes: { from: props.maxLength, to: text.length } })
            return
          }
          emit('update:modelValue', text)
          if (props.enableLinter) {
            refreshLint()
            refreshBackendLint(text)
          }
        }
        if (u.selectionSet || u.docChanged) {
          const sel = u.state.selection.main
          emit('selection-change', { from: sel.from, to: sel.to, text: u.state.doc.sliceString(sel.from, sel.to) })
        }
      } catch (e) {
        // CM6 内部状态不一致时保护，避免向上抛影响 Vue
        console.warn('[PythonEditor] updateListener error:', e)
      }
    }),
  ]
  return EditorState.create({ doc, extensions: exts })
}

onMounted(() => {
  try {
    view = new EditorView({ state: createState(props.modelValue || ''), parent: host.value })
    if (props.enableLinter) refreshLint()
  } catch (e) {
    // 容器尚未就绪 / 离开页面途中卸载，避免向上冒泡
    console.warn('[PythonEditor] init failed:', e)
    view = null
  }
})

watch(() => props.modelValue, (val) => {
  if (!view) return
  const current = view.state.doc.toString()
  if (val !== current) view.dispatch({ changes: { from: 0, to: current.length, insert: val || '' } })
  if (props.enableLinter) refreshLint()
})

// params 变化后重算补全（CM6 通过 prop 直接读，无需重建）

onBeforeUnmount(() => { if (view) view.destroy() })
</script>

<template>
  <div class="py-editor" @dragover.prevent @drop="onDrop">
    <div v-if="lintDiags.length" class="py-lint">
      <div v-for="(d, i) in lintDiags.slice(0, 6)" :key="i" class="py-lint-row" :class="d.sev">
        <span class="py-lint-icon">{{ d.sev === 'error' ? '✕' : '!' }}</span>
        <span class="py-lint-line">行 {{ d.line + 1 }}</span>
        <span class="py-lint-msg">{{ d.msg }}</span>
      </div>
    </div>
    <div ref="host" class="py-host"></div>
    <div class="py-foot">
      <span class="py-count">{{ (modelValue || '').length }}{{ maxLength ? ' / ' + maxLength : '' }} chars</span>
    </div>
  </div>
</template>

<style scoped>
.py-editor { border: 1px solid var(--c-border); border-radius: 8px; overflow: hidden; background: #282c34; }
.py-host :deep(.cm-editor) { background: #282c34; }
.py-host :deep(.cm-gutters) { background: #21252b; border-right: 1px solid #3a3f4b; color: #6b727f; }
.py-host :deep(.cm-lint-gutter) { width: 14px; }
.py-foot { display: flex; justify-content: flex-end; padding: 2px 8px; font-size: 10.5px; color: #8b95a5; background: #21252b; }
.py-lint { background: #1b1f27; border-bottom: 1px solid #3a3f4b; max-height: 120px; overflow-y: auto; }
.py-lint-row { display: flex; align-items: center; gap: 8px; padding: 3px 10px; font-size: 11.5px; color: #cbd5e1; }
.py-lint-row.error .py-lint-icon { color: #ef4444; }
.py-lint-row.warning .py-lint-icon { color: #eab308; }
.py-lint-line { color: #8b95a5; flex-shrink: 0; }
.py-lint-msg { color: #e2e8f0; }
</style>
