<script setup>
import { ref, reactive, watch, computed, nextTick } from 'vue'
import {
  createOntologyService, createEntityService, updateOntologyService, testOntologyService,
  aiAssistServiceCode,
} from '../../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** owner: { type:'ontology', categoryId, ontologyId, ontologyName } | { type:'entity', entityId, entityName } */
  owner: { type: Object, required: true },
  /** 编辑目标服务（null = 新建） */
  service: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const PARAM_TYPES = ['string', 'number', 'boolean', 'date', 'datetime', 'text']

const CODE_TEMPLATES = {
  http: `import requests

def run(params, entity, context):
    """调用 HTTP API 示例。"""
    url = "https://api.example.com/data"
    resp = requests.get(url, params={"q": params.get("query", "")}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return {"count": len(data) if isinstance(data, list) else 1, "raw": data}

`,
  data: `def run(params, entity, context):
    """数据处理示例：基于实体属性与入参做计算。"""
    props = entity.get("properties") or {}
    raw = params.get("values") or ""
    values = [float(v) for v in raw.split(",") if v.strip()]
    return {
        "entity": entity.get("name"),
        "prop_count": len(props),
        "max": max(values) if values else None,
        "avg": sum(values) / len(values) if values else None,
    }

`,
}

const form = reactive({
  name: '', code: '', description: '', timeout_seconds: 30,
  is_enabled: true, code_text: '', params: [],
})
const saving = ref(false)
const errMsg = ref('')

// 测试运行
const testVisible = ref(false)
const testParams = reactive({})
const mockEntity = reactive({ name: '', entity_type: '', properties: '{}' })
const testing = ref(false)
const testResult = ref(null)
const testError = ref('')

// AI 辅助（右侧聊天面板）
const chatOpen = ref(false)
const chatMessages = ref([]) // {role:'user'|'assistant', content} | {role:'assistant', data:{code_text,params,explanation}} | {role:'assistant', error}
const chatInput = ref('')
const chatLoading = ref(false)
const chatBodyRef = ref(null)

// 选中代码 → 引用到 AI 对话
const codeAreaRef = ref(null)
const selection = ref({ start: 0, end: 0 })
const quotedCode = ref('')

async function scrollChat() {
  await nextTick()
  if (chatBodyRef.value) chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight
}

// 把流式 Markdown 文本切成 文本/代码 段（未闭合的代码块按代码续流）
function splitSegments(text) {
  const segs = []
  const parts = String(text || '').split('```')
  parts.forEach((p, i) => {
    if (!p) return
    if (i % 2 === 1) {
      const nl = p.indexOf('\n')
      const body = nl >= 0 ? p.slice(nl + 1) : ''
      if (body) segs.push({ type: 'code', content: body })
    } else if (p.trim()) {
      segs.push({ type: 'text', content: p })
    }
  })
  return segs
}

const hasSelection = computed(() => selection.value.end > selection.value.start)

function captureSelection() {
  const el = codeAreaRef.value
  if (!el) return
  selection.value = { start: el.selectionStart, end: el.selectionEnd }
}

function quoteSelection() {
  const el = codeAreaRef.value
  if (!el || el.selectionEnd <= el.selectionStart) return
  quotedCode.value = el.value.slice(el.selectionStart, el.selectionEnd)
  selection.value = { start: 0, end: 0 }
  chatOpen.value = true
}

// 行级 diff（LCS）：返回 [{type:'same'|'add'|'del', text}]
function computeDiff(oldText, newText) {
  const a = String(oldText || '').split('\n')
  const b = String(newText || '').split('\n')
  const n = a.length, m = b.length
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const rows = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) { rows.push({ type: 'same', text: a[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) rows.push({ type: 'del', text: a[i++] })
    else rows.push({ type: 'add', text: b[j++] })
  }
  while (i < n) rows.push({ type: 'del', text: a[i++] })
  while (j < m) rows.push({ type: 'add', text: b[j++] })
  return rows
}

function diffStats(msg) {
  const rows = computeDiff(msg.oldCode ?? '', msg.data?.code_text ?? '')
  return {
    added: rows.filter(r => r.type === 'add').length,
    removed: rows.filter(r => r.type === 'del').length,
  }
}

async function chatSend() {
  const text = chatInput.value.trim()
  if (!text || chatLoading.value) return
  const quoted = quotedCode.value
  chatMessages.value.push({ role: 'user', content: text, quoted: quoted || null })
  chatInput.value = ''
  quotedCode.value = ''
  chatLoading.value = true
  // 组装多轮历史（assistant 回传 说明+代码，让模型有上下文可迭代修改）
  const history = chatMessages.value
    .filter(m => !m.error && (m.role === 'user' ? m.content : m.data?.code_text))
    .slice(-10)
    .map(m => ({
      role: m.role,
      content: m.role === 'user' ? m.content : `${m.data.explanation || ''}\n\n${m.data.code_text}`,
    }))
  const msg = reactive({ role: 'assistant', content: '', data: null, error: null, streaming: true, oldCode: form.code_text, showDiff: false })
  chatMessages.value.push(msg)
  scrollChat()
  try {
    const data = await aiAssistServiceCode({
      prompt: text,
      name: form.name,
      code: form.code,
      description: form.description,
      owner_name: props.owner?.ontologyName || props.owner?.entityName || '',
      current_code: form.code_text,
      selected_code: quoted,
      history,
      onDelta: d => { msg.content += d; scrollChat() },
    })
    msg.data = data
  } catch (e) {
    msg.error = e.message || 'AI 生成失败'
  } finally {
    msg.streaming = false
    chatLoading.value = false
    scrollChat()
  }
}

function applyChatCode(msg) {
  if (!msg.data?.code_text) return
  form.code_text = msg.data.code_text
  if (msg.data.params?.length) {
    form.params = msg.data.params.map(p => ({
      name: (p.name || '').trim(),
      label: p.label || p.name || '',
      type: p.type || 'string',
      required: !!p.required,
      default: p.default ?? '',
      description: p.description || '',
    }))
  }
}

const isEdit = computed(() => !!props.service)
const savedId = ref('') // 保存后的服务 id（编辑模式直接取 service.id）

const dialogTitle = computed(() =>
  isEdit.value ? `编辑服务：${props.service?.name}` : `新建服务${props.owner?.type === 'entity' ? '（自定义动作）' : ''}`
)

watch(() => props.modelValue, (v) => {
  if (!v) return
  errMsg.value = ''
  testVisible.value = false
  testResult.value = null
  testError.value = ''
  chatOpen.value = false
  chatMessages.value = []
  chatInput.value = ''
  chatLoading.value = false
  quotedCode.value = ''
  selection.value = { start: 0, end: 0 }
  if (props.service) {
    form.name = props.service.name
    form.code = props.service.code
    form.description = props.service.description || ''
    form.timeout_seconds = props.service.timeout_seconds || 30
    form.is_enabled = !!props.service.is_enabled
    form.code_text = props.service.code_text || ''
    form.params = (props.service.params || []).map(p => ({ ...p }))
    savedId.value = props.service.id
  } else {
    form.name = ''
    form.code = ''
    form.description = ''
    form.timeout_seconds = 30
    form.is_enabled = true
    form.code_text = CODE_TEMPLATES.http
    form.params = []
    savedId.value = ''
  }
  Object.keys(testParams).forEach(k => delete testParams[k])
  mockEntity.name = ''
  mockEntity.entity_type = ''
  mockEntity.properties = '{}'
})

function addParam() {
  form.params.push({ name: '', label: '', type: 'string', required: false, default: '', description: '' })
}
function removeParam(i) {
  form.params.splice(i, 1)
}
function applyTemplate(key) {
  form.code_text = CODE_TEMPLATES[key]
}

function buildPayload() {
  return {
    name: form.name.trim(),
    code: form.code.trim(),
    description: form.description.trim(),
    params: form.params
      .filter(p => p.name?.trim())
      .map(p => ({
        name: p.name.trim(),
        label: (p.label || p.name).trim(),
        type: p.type || 'string',
        required: !!p.required,
        default: p.default ?? null,
        description: p.description || '',
      })),
    code_text: form.code_text,
    language: 'python',
    timeout_seconds: Number(form.timeout_seconds) || 30,
    is_enabled: !!form.is_enabled,
    sort_order: props.service?.sort_order || 0,
  }
}

async function save() {
  if (!form.name.trim() || !form.code.trim() || !form.code_text.trim()) {
    errMsg.value = '名称、动作标识、代码均不能为空'
    return
  }
  saving.value = true
  errMsg.value = ''
  try {
    let svc
    if (isEdit.value) {
      svc = await updateOntologyService(props.service.id, buildPayload())
    } else if (props.owner.type === 'ontology') {
      svc = await createOntologyService(props.owner.categoryId, props.owner.ontologyId, buildPayload())
    } else {
      svc = await createEntityService(props.owner.entityId, buildPayload())
    }
    savedId.value = svc.id
    emit('saved', svc)
    emit('update:modelValue', false)
  } catch (e) {
    errMsg.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function runTest() {
  if (!savedId.value) return
  testing.value = true
  testResult.value = null
  testError.value = ''
  try {
    let mock = null
    try {
      const propsJson = JSON.parse(mockEntity.properties || '{}')
      if (mockEntity.name || mockEntity.entity_type || Object.keys(propsJson).length) {
        mock = { name: mockEntity.name, entity_type: mockEntity.entity_type, properties: propsJson }
      }
    } catch { /* 属性 JSON 非法时忽略 mock */ }
    testResult.value = await testOntologyService(savedId.value, { params: { ...testParams }, mock_entity: mock })
  } catch (e) {
    testError.value = e.message || '测试运行失败'
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div v-if="modelValue" class="sed-mask">
    <div class="sed-modal" :class="{ 'with-chat': chatOpen }">
      <div class="sed-main">
      <h3>{{ dialogTitle }}</h3>

      <div class="sed-grid">
        <div class="sed-field">
          <label>名称 <i class="req">*</i></label>
          <input type="text" v-model="form.name" placeholder="如：查询天气">
        </div>
        <div class="sed-field">
          <label>动作标识 <i class="req">*</i></label>
          <input type="text" v-model="form.code" placeholder="如：weather.query">
        </div>
        <div class="sed-field narrow">
          <label>超时(秒)</label>
          <input type="number" v-model.number="form.timeout_seconds" min="1" max="120">
        </div>
        <div class="sed-field narrow">
          <label>启用</label>
          <label class="sed-switch">
            <input type="checkbox" v-model="form.is_enabled">
            <span>{{ form.is_enabled ? '已启用' : '已停用' }}</span>
          </label>
        </div>
      </div>

      <div class="sed-field">
        <label>描述</label>
        <input type="text" v-model="form.description" placeholder="该动作的用途说明（也会展示给智能体理解）">
      </div>

      <div class="sed-block">
        <div class="sed-block-head">
          <span class="sed-block-title">动作参数</span>
          <button class="btn sm" @click="addParam">+ 添加参数</button>
        </div>
        <div v-if="!form.params.length" class="sed-hint">无参数。动作可直接使用 entity（实体数据）与 context。</div>
        <div v-else class="sed-params">
          <div class="sed-param-row sed-param-head">
            <span>标识</span><span>名称</span><span>类型</span><span>必填</span><span>默认值</span><span></span>
          </div>
          <div v-for="(p, i) in form.params" :key="i" class="sed-param-row">
            <input type="text" v-model="p.name" placeholder="city">
            <input type="text" v-model="p.label" placeholder="城市">
            <select v-model="p.type">
              <option v-for="t in PARAM_TYPES" :key="t" :value="t">{{ t }}</option>
            </select>
            <input type="checkbox" v-model="p.required">
            <input type="text" v-model="p.default" placeholder="默认值">
            <button class="sed-rm" @click="removeParam(i)" title="删除">×</button>
          </div>
        </div>
      </div>

      <div class="sed-block">
        <div class="sed-block-head">
          <span class="sed-block-title">代码（Python，定义 run 函数）</span>
          <div class="sed-tpl-btns">
            <button class="btn sm ai-btn" :class="{ active: chatOpen }" @click="chatOpen = !chatOpen">✦ AI 辅助</button>
            <button v-if="chatOpen" class="btn sm" :disabled="!hasSelection" @click="quoteSelection"
              :title="hasSelection ? '把选中的代码片段引用到 AI 对话' : '先在下方代码区选中代码'">选中 → AI</button>
            <button class="btn sm" @click="applyTemplate('http')">示例：调用 API</button>
            <button class="btn sm" @click="applyTemplate('data')">示例：数据处理</button>
          </div>
        </div>

        <textarea ref="codeAreaRef" class="sed-code" v-model="form.code_text" spellcheck="false" rows="12"
          @mouseup="captureSelection" @keyup="captureSelection"
          placeholder="def run(params, entity, context):&#10;    return {}"></textarea>
        <div v-if="chatOpen && hasSelection" class="sed-sel-tip">
          已选中 {{ selection.end - selection.start }} 字符 · 点「选中 → AI」引用到对话
        </div>
        <div class="sed-hint">
          可用 import：json / re / math / datetime / random / collections / urllib / hashlib / base64 / requests / httpx 等；
          入口为 <code>run(params, entity, context)</code>，返回可 JSON 序列化的 dict。
        </div>
      </div>

      <div class="sed-block">
        <div class="sed-block-head">
          <span class="sed-block-title">测试运行</span>
          <button class="btn sm" @click="testVisible = !testVisible">{{ testVisible ? '收起' : '展开' }}</button>
        </div>
        <div v-if="!savedId" class="sed-hint">保存服务后可在此测试运行。</div>
        <template v-else-if="testVisible">
          <div v-if="form.params.length" class="sed-test-form">
            <div v-for="p in form.params" :key="p.name" class="sed-field">
              <label>{{ p.label || p.name }} <i v-if="p.required" class="req">*</i></label>
              <template v-if="p.type === 'boolean'">
                <select v-model="testParams[p.name]">
                  <option :value="true">true</option>
                  <option :value="false">false</option>
                </select>
              </template>
              <input v-else :type="p.type === 'number' ? 'number' : 'text'" v-model="testParams[p.name]"
                :placeholder="p.description || p.default || ''">
            </div>
          </div>
          <details class="sed-mock">
            <summary>模拟实体（可选，测试依赖实体数据的代码时填写）</summary>
            <div class="sed-mock-body">
              <input type="text" v-model="mockEntity.name" placeholder="实体名称">
              <input type="text" v-model="mockEntity.entity_type" placeholder="实体类型">
              <textarea v-model="mockEntity.properties" rows="2" spellcheck="false" placeholder='实体属性 JSON，如 {"型号": "X1"}'></textarea>
            </div>
          </details>
          <div class="sed-test-actions">
            <button class="btn primary sm" @click="runTest" :disabled="testing">
              <span v-if="testing" class="spinner"></span> 运行
            </button>
          </div>
          <div v-if="testError" class="sed-result err">{{ testError }}</div>
          <div v-if="testResult" class="sed-result" :class="{ fail: !testResult.success }">
            <div class="sed-result-meta">
              <span class="sed-status" :class="testResult.success ? 'ok' : 'fail'">
                {{ testResult.success ? '成功' : '失败' }}
              </span>
              <span>耗时 {{ testResult.duration_ms }}ms</span>
            </div>
            <div v-if="testResult.error" class="sed-err-text">{{ testResult.error }}</div>
            <div v-if="testResult.data != null" class="sed-section-label">返回数据</div>
            <pre v-if="testResult.data != null">{{ JSON.stringify(testResult.data, null, 2) }}</pre>
            <div v-if="testResult.stdout" class="sed-section-label">stdout</div>
            <pre v-if="testResult.stdout">{{ testResult.stdout }}</pre>
          </div>
        </template>
      </div>

      <div v-if="errMsg" class="sed-error">{{ errMsg }}</div>
      <div class="sed-actions">
        <button class="btn" @click="emit('update:modelValue', false)">取消</button>
        <button class="btn primary" @click="save" :disabled="saving">
          <span v-if="saving" class="spinner"></span> 保存
        </button>
      </div>
      </div>

      <!-- 右侧 AI 辅助聊天面板 -->
      <aside v-if="chatOpen" class="sed-chat">
        <div class="sed-chat-head">
          <span class="sed-chat-title">✦ AI 辅助</span>
          <button class="sed-chat-close" @click="chatOpen = false" title="收起">×</button>
        </div>
        <div class="sed-chat-body" ref="chatBodyRef">
          <div v-if="!chatMessages.length" class="sed-chat-empty">
            描述想要的功能，AI 生成动作代码<br>
            也可继续追问，让 AI 修改当前代码
          </div>
          <template v-for="(m, i) in chatMessages" :key="i">
            <div v-if="m.role === 'user'" class="sed-chat-msg user">
              <pre v-if="m.quoted" class="sed-chat-quote-pre" title="引用的选中代码">{{ m.quoted }}</pre>
              <div class="sed-chat-bubble">{{ m.content }}</div>
            </div>
            <div v-else class="sed-chat-msg assistant">
              <div v-if="m.error" class="sed-ai-error">{{ m.error }}</div>
              <template v-else>
                <template v-for="(seg, si) in splitSegments(m.content)" :key="si">
                  <div v-if="seg.type === 'text'" class="sed-ai-explain">{{ seg.content }}</div>
                  <pre v-else class="sed-chat-code-pre">{{ seg.content }}</pre>
                </template>
                <span v-if="m.streaming" class="sed-chat-cursor">▍</span>
                <template v-if="m.data">
                  <div class="sed-ai-meta">已通过安全校验{{ m.data.params?.length ? ` · 含 ${m.data.params.length} 个参数定义` : '' }}</div>
                  <button class="btn sm diff-toggle" @click="m.showDiff = !m.showDiff">
                    {{ m.showDiff ? '收起改动' : `查看改动（+${diffStats(m).added} −${diffStats(m).removed}）` }}
                  </button>
                  <div v-if="m.showDiff" class="sed-diff">
                    <div v-for="(r, ri) in computeDiff(m.oldCode, m.data.code_text)" :key="ri"
                      class="sed-diff-row" :class="r.type">
                      <span class="sed-diff-sign">{{ r.type === 'add' ? '+' : r.type === 'del' ? '−' : '' }}</span>
                      <span class="sed-diff-text">{{ r.text }}</span>
                    </div>
                  </div>
                  <div class="sed-ai-actions">
                    <button class="btn primary sm" @click="applyChatCode(m)">应用改动</button>
                  </div>
                </template>
              </template>
            </div>
          </template>
        </div>
        <div v-if="quotedCode" class="sed-chat-quote">
          <div class="sed-chat-quote-head">
            <span>引用选中代码 · {{ quotedCode.length }} 字符</span>
            <button class="sed-chat-close" @click="quotedCode = ''" title="移除引用">×</button>
          </div>
          <pre>{{ quotedCode }}</pre>
        </div>
        <div class="sed-chat-input">
          <textarea v-model="chatInput" rows="2" spellcheck="false" :disabled="chatLoading"
            placeholder="描述需求，如：调用 wttr.in 查询城市天气并返回温度；Enter 发送，Shift+Enter 换行"
            @keydown.enter.exact.prevent="chatSend"></textarea>
          <button class="btn primary sm" @click="chatSend" :disabled="chatLoading || !chatInput.trim()">发送</button>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.sed-mask { position: fixed; inset: 0; background: var(--c-overlay); display: flex; align-items: center; justify-content: center; z-index: 100; padding: 20px; }
.sed-modal { background: var(--c-panel); border-radius: var(--radius); width: 100%; max-width: 720px; max-height: 88vh; overflow: hidden; box-shadow: 0 8px 30px rgba(0,0,0,0.18); display: flex; flex-direction: column; }
.sed-modal.with-chat { flex-direction: row; max-width: 1120px; }
.sed-main { flex: 1; min-width: 0; overflow-y: auto; padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.sed-modal h3 { font-size: 15px; font-weight: 700; color: var(--c-fg); }
.sed-grid { display: grid; grid-template-columns: 1.2fr 1.2fr 0.6fr 0.8fr; gap: 10px; }
.sed-grid .narrow { min-width: 0; }
.sed-field { display: flex; flex-direction: column; gap: 4px; }
.sed-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.sed-field .req { color: var(--c-danger); font-style: normal; }
.sed-field input, .sed-field select { width: 100%; padding: 6px 9px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; box-sizing: border-box; }
.sed-field input:focus, .sed-field select:focus { border-color: var(--c-fg); }
.sed-switch { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-fg); padding-top: 4px; }

.sed-block { display: flex; flex-direction: column; gap: 8px; }
.sed-block-head { display: flex; align-items: center; justify-content: space-between; }
.sed-block-title { font-size: 13px; font-weight: 700; color: var(--c-fg); }
.sed-tpl-btns { display: flex; gap: 6px; }
.sed-hint { font-size: 12px; color: var(--c-secondary); line-height: 1.6; }
.sed-hint code { font-family: ui-monospace, Consolas, monospace; background: var(--c-muted); padding: 0 4px; border-radius: 4px; }

.sed-params { display: flex; flex-direction: column; gap: 4px; }
.sed-param-row { display: grid; grid-template-columns: 1.1fr 1.1fr 0.9fr 40px 1fr 28px; gap: 6px; align-items: center; }
.sed-param-head { font-size: 11px; color: var(--c-secondary); padding: 0 2px; }
.sed-param-row input[type="text"], .sed-param-row select { padding: 5px 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12px; width: 100%; box-sizing: border-box; outline: none; }
.sed-param-row input[type="checkbox"] { width: 15px; height: 15px; }
.sed-rm { width: 26px; height: 26px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; font-size: 15px; line-height: 1; }
.sed-rm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }

.sed-code { width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-muted); color: var(--c-fg); font-family: ui-monospace, Consolas, "Courier New", monospace; font-size: 12.5px; line-height: 1.55; resize: vertical; outline: none; tab-size: 4; }
.sed-code:focus { border-color: var(--c-fg); }

.sed-ai-error { padding: 7px 9px; border-radius: var(--radius-sm); background: rgba(220, 38, 38, 0.08); color: var(--c-danger); font-size: 12px; }
.sed-ai-explain { font-size: 12.5px; color: var(--c-fg); line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.sed-ai-meta { font-size: 11px; color: var(--c-secondary); }
.sed-ai-actions { display: flex; gap: 8px; }

/* diff 改动预览 */
.diff-toggle { color: #8b5cf6; border-color: rgba(139, 92, 246, 0.4); align-self: flex-start; }
.diff-toggle:hover { background: rgba(139, 92, 246, 0.1); }
.sed-diff { border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-muted); font-family: ui-monospace, Consolas, monospace; font-size: 11px; line-height: 1.55; overflow: auto; max-height: 320px; }
.sed-diff-row { display: flex; align-items: stretch; }
.sed-diff-sign { flex: 0 0 22px; text-align: center; color: var(--c-secondary); user-select: none; }
.sed-diff-row.add { background: rgba(34, 197, 94, 0.14); }
.sed-diff-row.add .sed-diff-sign { color: #16a34a; font-weight: 700; }
.sed-diff-row.del { background: rgba(220, 38, 38, 0.12); }
.sed-diff-row.del .sed-diff-sign { color: var(--c-danger); font-weight: 700; }
.sed-diff-text { flex: 1; padding: 0 6px 0 0; white-space: pre-wrap; word-break: break-all; min-height: 1.4em; }

/* 右侧 AI 聊天面板 */
.ai-btn.active { border-color: #8b5cf6; color: #8b5cf6; }
.ai-btn.active:hover { background: rgba(139, 92, 246, 0.1); }
.sed-chat { flex: 0 0 350px; display: flex; flex-direction: column; min-height: 0; border-left: 1px solid var(--c-border); background: rgba(139, 92, 246, 0.03); }
.sed-chat-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid var(--c-border); flex-shrink: 0; }
.sed-chat-title { font-size: 13px; font-weight: 700; color: #8b5cf6; }
.sed-chat-close { width: 26px; height: 26px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); font-size: 16px; line-height: 1; cursor: pointer; }
.sed-chat-close:hover { background: rgba(139, 92, 246, 0.12); color: var(--c-fg); }
.sed-chat-body { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; min-height: 200px; }
.sed-chat-empty { color: var(--c-secondary); font-size: 12px; text-align: center; line-height: 2; margin-top: 40%; }
.sed-chat-msg.user { display: flex; justify-content: flex-end; }
.sed-chat-bubble { background: rgba(139, 92, 246, 0.14); border-radius: 10px 10px 2px 10px; padding: 8px 11px; font-size: 12.5px; color: var(--c-fg); max-width: 88%; white-space: pre-wrap; word-break: break-word; }
.sed-chat-msg.assistant { display: flex; flex-direction: column; gap: 6px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 9px 11px; background: var(--c-panel); font-size: 12.5px; }
.sed-chat-msg.assistant .spinner { width: 13px; height: 13px; border-width: 2px; margin-right: 4px; }
.sed-chat-code-pre { margin: 0; padding: 8px; border-radius: var(--radius-sm); background: var(--c-muted); font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; white-space: pre-wrap; word-break: break-all; max-height: 240px; overflow-y: auto; }
.sed-chat-cursor { display: inline-block; color: #8b5cf6; animation: sed-cursor-blink 0.9s steps(1) infinite; }
@keyframes sed-cursor-blink { 50% { opacity: 0; } }

.sed-sel-tip { margin-top: 6px; font-size: 11.5px; color: #8b5cf6; background: rgba(139, 92, 246, 0.08); border: 1px dashed rgba(139, 92, 246, 0.4); border-radius: var(--radius-sm); padding: 5px 9px; }

.sed-chat-quote { border: 1px dashed rgba(139, 92, 246, 0.4); border-radius: var(--radius-sm); margin: 0 10px 8px; background: var(--c-panel); }
.sed-chat-quote-head { display: flex; align-items: center; justify-content: space-between; padding: 6px 9px; font-size: 11px; color: #8b5cf6; border-bottom: 1px dashed rgba(139, 92, 246, 0.3); }
.sed-chat-quote pre { margin: 0; padding: 8px 9px; font-family: ui-monospace, Consolas, monospace; font-size: 11px; white-space: pre-wrap; word-break: break-all; max-height: 110px; overflow-y: auto; color: var(--c-secondary); }
.sed-chat-quote-pre { margin: 0 0 4px; padding: 6px 8px; border-radius: var(--radius-sm); border-left: 2px solid rgba(139, 92, 246, 0.5); background: var(--c-muted); font-family: ui-monospace, Consolas, monospace; font-size: 10.5px; white-space: pre-wrap; word-break: break-all; max-height: 90px; overflow-y: auto; color: var(--c-secondary); }
.sed-chat-input { border-top: 1px solid var(--c-border); padding: 10px; display: flex; gap: 8px; align-items: flex-end; flex-shrink: 0; }
.sed-chat-input textarea { flex: 1; padding: 8px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12.5px; line-height: 1.5; resize: none; outline: none; box-sizing: border-box; font-family: var(--font, inherit); }
.sed-chat-input textarea:focus { border-color: #8b5cf6; }
.sed-chat-input .btn { flex-shrink: 0; }

.sed-test-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
.sed-mock summary { font-size: 12px; color: var(--c-secondary); cursor: pointer; }
.sed-mock-body { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.sed-mock-body input, .sed-mock-body textarea { padding: 5px 9px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12px; outline: none; }
.sed-mock-body textarea { font-family: ui-monospace, Consolas, monospace; resize: vertical; }
.sed-test-actions { display: flex; justify-content: flex-end; }

.sed-result { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 10px 12px; background: var(--c-muted); font-size: 12px; display: flex; flex-direction: column; gap: 6px; }
.sed-result.err { color: var(--c-danger); }
.sed-result.fail { border-color: var(--c-danger); }
.sed-result-meta { display: flex; align-items: center; gap: 10px; color: var(--c-secondary); }
.sed-status { font-weight: 800; }
.sed-status.ok { color: var(--c-success, #16A34A); }
.sed-status.fail { color: var(--c-danger); }
.sed-err-text { color: var(--c-danger); font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; word-break: break-all; }
.sed-section-label { font-weight: 700; color: var(--c-secondary); }
.sed-result pre { margin: 0; padding: 8px; border-radius: var(--radius-sm); background: var(--c-panel); font-family: ui-monospace, Consolas, monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; max-height: 220px; overflow-y: auto; }

.sed-error { padding: 8px 10px; border-radius: var(--radius-sm); background: rgba(220, 38, 38, 0.08); color: var(--c-danger); font-size: 12px; }
.sed-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
