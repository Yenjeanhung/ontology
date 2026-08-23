<script setup>
import { ref, reactive, watch, computed, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  createOntologyService, createEntityService, updateOntologyService, testOntologyService,
  aiAssistServiceCode, getOntologyService,
} from '../../api'
import PythonEditor from '../workflow/PythonEditor.vue'
import { useToast } from '../../composables/useToast'

const route = useRoute()
const router = useRouter()
const toast = useToast()

const props = defineProps({
  /** 编辑模式：传入服务 id（路由 params.serviceId） */
  serviceId: { type: String, default: '' },
})

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
const loading = ref(false)

const owner = computed(() => {
  const isEntity = route.name?.startsWith('entity-')
  if (isEntity) {
    return { type: 'entity', entityId: route.query.entityId || '', entityName: route.query.entityName || '' }
  }
  return {
    type: 'ontology',
    categoryId: route.query.categoryId || '',
    ontologyId: route.query.ontologyId || '',
    ontologyName: route.query.ontologyName || '',
  }
})

const isEdit = computed(() => !!props.serviceId || !!route.params.serviceId)
const savedId = ref('')
const pageTitle = computed(() => {
  if (isEdit.value) return `编辑服务：${form.name || '加载中...'}`
  return `新建服务${owner.value.type === 'entity' ? '（自定义动作）' : ''}`
})

// 测试运行
const testVisible = ref(false)
const testParams = reactive({})
const mockEntity = reactive({ name: '', entity_type: '', properties: '{}' })
const testing = ref(false)
const testResult = ref(null)
const testError = ref('')

// AI 辅助（右侧聊天面板）
const chatOpen = ref(false)
const chatMessages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
const chatBodyRef = ref(null)

// 选中代码 → 引用到 AI 对话
const codeEditorRef = ref(null)
const selection = ref({ start: 0, end: 0 })
const quotedCode = ref('')

async function scrollChat() {
  await nextTick()
  if (chatBodyRef.value) chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight
}

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

function onCodeSelection(sel) {
  selection.value = { start: sel.from, end: sel.to }
}

function quoteSelection() {
  if (!codeEditorRef.value) return
  const sel = codeEditorRef.value.getSelection()
  if (!sel.text) return
  quotedCode.value = sel.text
  selection.value = { start: 0, end: 0 }
  chatOpen.value = true
}

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
      owner_name: owner.value?.ontologyName || owner.value?.entityName || '',
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
    sort_order: 0,
  }
}

function resetForm(isEditing, svc) {
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
  if (isEditing && svc) {
    form.name = svc.name
    form.code = svc.code
    form.description = svc.description || ''
    form.timeout_seconds = svc.timeout_seconds || 30
    form.is_enabled = !!svc.is_enabled
    form.code_text = svc.code_text || ''
    form.params = (svc.params || []).map(p => ({ ...p }))
    savedId.value = svc.id
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
}

onMounted(async () => {
  if (isEdit.value) {
    loading.value = true
    try {
      const id = props.serviceId || route.params.serviceId
      const svc = await getOntologyService(id)
      resetForm(true, svc)
    } catch (e) {
      toast.error(`加载服务失败：${e.message}`)
      goBack()
    } finally {
      loading.value = false
    }
  } else {
    resetForm(false, null)
  }
})

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push(owner.value.type === 'entity' && owner.value.entityId
    ? `/entities/${owner.value.entityId}` : '/ontology/ontologies')
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
    if (savedId.value) {
      svc = await updateOntologyService(savedId.value, buildPayload())
    } else if (owner.value.type === 'ontology') {
      svc = await createOntologyService(owner.value.categoryId, owner.value.ontologyId, buildPayload())
    } else {
      svc = await createEntityService(owner.value.entityId, buildPayload())
    }
    savedId.value = svc.id
    toast.success('已保存')
    goBack()
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
  <div class="sep-page">
    <header class="sep-top">
      <div class="sep-crumb">
        <button class="btn sm ghost" @click="goBack">← 返回</button>
        <span class="sep-sep">/</span>
        <span class="sep-owner">{{ owner.type === 'entity' ? (owner.entityName || '实体') : (owner.ontologyName || '本体') }}</span>
        <span class="sep-sep">/</span>
        <span class="sep-title">{{ pageTitle }}</span>
      </div>
      <div class="sep-top-actions">
        <button class="btn sm" @click="goBack">取消</button>
        <button class="btn sm primary" @click="save" :disabled="saving">
          <span v-if="saving" class="spinner"></span> 保存
        </button>
      </div>
    </header>

    <div v-if="loading" class="sep-loading"><span class="spinner"></span> 加载中...</div>

    <div v-else class="sep-body" :class="{ 'with-chat': chatOpen }">
      <!-- 左：元数据 -->
      <aside class="sep-left">
        <div class="sep-field">
          <label>名称 <i class="req">*</i></label>
          <input type="text" v-model="form.name" placeholder="如：查询天气">
        </div>
        <div class="sep-field">
          <label>动作标识 <i class="req">*</i></label>
          <input type="text" v-model="form.code" placeholder="如：weather.query">
        </div>
        <div class="sep-row2">
          <div class="sep-field">
            <label>超时(秒)</label>
            <input type="number" v-model.number="form.timeout_seconds" min="1" max="120">
          </div>
          <div class="sep-field">
            <label>启用</label>
            <label class="sep-switch">
              <input type="checkbox" v-model="form.is_enabled">
              <span>{{ form.is_enabled ? '已启用' : '已停用' }}</span>
            </label>
          </div>
        </div>
        <div class="sep-field">
          <label>描述</label>
          <input type="text" v-model="form.description" placeholder="该动作的用途说明（也会展示给智能体理解）">
        </div>

        <div class="sep-block">
          <div class="sep-block-head">
            <span class="sep-block-title">动作参数</span>
            <button class="btn sm" @click="addParam">+ 添加参数</button>
          </div>
          <div v-if="!form.params.length" class="sep-hint">无参数。动作可直接使用 entity（实体数据）与 context。</div>
          <div v-else class="sep-params">
            <div class="sep-param-row sep-param-head">
              <span>标识</span><span>名称</span><span>类型</span><span>必填</span><span>默认值</span><span></span>
            </div>
            <div v-for="(p, i) in form.params" :key="i" class="sep-param-row">
              <input type="text" v-model="p.name" placeholder="city">
              <input type="text" v-model="p.label" placeholder="城市">
              <select v-model="p.type">
                <option v-for="t in PARAM_TYPES" :key="t" :value="t">{{ t }}</option>
              </select>
              <input type="checkbox" v-model="p.required">
              <input type="text" v-model="p.default" placeholder="默认值">
              <button class="sep-rm" @click="removeParam(i)" title="删除">×</button>
            </div>
          </div>
        </div>

        <div class="sep-hint sep-api">
          可用 import：json / re / math / datetime / random / collections / urllib / hashlib / base64 / requests / httpx 等；
          入口为 <code>run(params, entity, context)</code>，返回可 JSON 序列化的 dict。
        </div>
      </aside>

      <!-- 中：代码 + 测试 -->
      <main class="sep-main">
        <div class="sep-code-head">
          <span class="sep-block-title">代码（Python，定义 run 函数）</span>
          <div class="sep-tpl-btns">
            <button class="btn sm ai-btn" :class="{ active: chatOpen }" @click="chatOpen = !chatOpen">✦ AI 辅助</button>
            <button v-if="chatOpen" class="btn sm" :disabled="!hasSelection" @click="quoteSelection"
              :title="hasSelection ? '把选中的代码片段引用到 AI 对话' : '先在代码区选中代码'">选中 → AI</button>
            <button class="btn sm" @click="applyTemplate('http')">示例：调用 API</button>
            <button class="btn sm" @click="applyTemplate('data')">示例：数据处理</button>
          </div>
        </div>
        <PythonEditor
          ref="codeEditorRef"
          v-model="form.code_text"
          :height="380"
          :max-length="50000"
          :params="form.params"
          @selection-change="onCodeSelection"
        />
        <div v-if="chatOpen && hasSelection" class="sep-sel-tip">
          已选中 {{ selection.end - selection.start }} 字符 · 点「选中 → AI」引用到对话
        </div>

        <div class="sep-block sep-test">
          <div class="sep-block-head">
            <span class="sep-block-title">测试运行</span>
            <button class="btn sm" @click="testVisible = !testVisible">{{ testVisible ? '收起' : '展开' }}</button>
          </div>
          <div v-if="!savedId" class="sep-hint">保存服务后可在此测试运行。</div>
          <template v-else-if="testVisible">
            <div v-if="form.params.length" class="sep-test-form">
              <div v-for="p in form.params" :key="p.name" class="sep-field">
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
            <details class="sep-mock">
              <summary>模拟实体（可选，测试依赖实体数据的代码时填写）</summary>
              <div class="sep-mock-body">
                <input type="text" v-model="mockEntity.name" placeholder="实体名称">
                <input type="text" v-model="mockEntity.entity_type" placeholder="实体类型">
                <textarea v-model="mockEntity.properties" rows="2" spellcheck="false" placeholder='实体属性 JSON，如 {"型号": "X1"}'></textarea>
              </div>
            </details>
            <div class="sep-test-actions">
              <button class="btn primary sm" @click="runTest" :disabled="testing">
                <span v-if="testing" class="spinner"></span> 运行
              </button>
            </div>
            <div v-if="testError" class="sep-result err">{{ testError }}</div>
            <div v-if="testResult" class="sep-result" :class="{ fail: !testResult.success }">
              <div class="sep-result-meta">
                <span class="sep-status" :class="testResult.success ? 'ok' : 'fail'">
                  {{ testResult.success ? '成功' : '失败' }}
                </span>
                <span>耗时 {{ testResult.duration_ms }}ms</span>
              </div>
              <div v-if="testResult.error" class="sep-err-text">{{ testResult.error }}</div>
              <div v-if="testResult.data != null" class="sep-section-label">返回数据</div>
              <pre v-if="testResult.data != null">{{ JSON.stringify(testResult.data, null, 2) }}</pre>
              <div v-if="testResult.stdout" class="sep-section-label">stdout</div>
              <pre v-if="testResult.stdout">{{ testResult.stdout }}</pre>
            </div>
          </template>
        </div>

        <div v-if="errMsg" class="sep-error">{{ errMsg }}</div>
      </main>

      <!-- 右：AI 辅助聊天面板 -->
      <aside v-if="chatOpen" class="sep-chat">
        <div class="sep-chat-head">
          <span class="sep-chat-title">✦ AI 辅助</span>
          <button class="sep-chat-close" @click="chatOpen = false" title="收起">×</button>
        </div>
        <div class="sep-chat-body" ref="chatBodyRef">
          <div v-if="!chatMessages.length" class="sep-chat-empty">
            描述想要的功能，AI 生成动作代码<br>
            也可继续追问，让 AI 修改当前代码
          </div>
          <template v-for="(m, i) in chatMessages" :key="i">
            <div v-if="m.role === 'user'" class="sep-chat-msg user">
              <pre v-if="m.quoted" class="sep-chat-quote-pre" title="引用的选中代码">{{ m.quoted }}</pre>
              <div class="sep-chat-bubble">{{ m.content }}</div>
            </div>
            <div v-else class="sep-chat-msg assistant">
              <div v-if="m.error" class="sep-ai-error">{{ m.error }}</div>
              <template v-else>
                <template v-for="(seg, si) in splitSegments(m.content)" :key="si">
                  <div v-if="seg.type === 'text'" class="sep-ai-explain">{{ seg.content }}</div>
                  <pre v-else class="sep-chat-code-pre">{{ seg.content }}</pre>
                </template>
                <span v-if="m.streaming" class="sep-chat-cursor">▍</span>
                <template v-if="m.data">
                  <div class="sep-ai-meta">已通过安全校验{{ m.data.params?.length ? ` · 含 ${m.data.params.length} 个参数定义` : '' }}</div>
                  <button class="btn sm diff-toggle" @click="m.showDiff = !m.showDiff">
                    {{ m.showDiff ? '收起改动' : `查看改动（+${diffStats(m).added} −${diffStats(m).removed}）` }}
                  </button>
                  <div v-if="m.showDiff" class="sep-diff">
                    <div v-for="(r, ri) in computeDiff(m.oldCode, m.data.code_text)" :key="ri"
                      class="sep-diff-row" :class="r.type">
                      <span class="sep-diff-sign">{{ r.type === 'add' ? '+' : r.type === 'del' ? '−' : '' }}</span>
                      <span class="sep-diff-text">{{ r.text }}</span>
                    </div>
                  </div>
                  <div class="sep-ai-actions">
                    <button class="btn primary sm" @click="applyChatCode(m)">应用改动</button>
                  </div>
                </template>
              </template>
            </div>
          </template>
        </div>
        <div v-if="quotedCode" class="sep-chat-quote">
          <div class="sep-chat-quote-head">
            <span>引用选中代码 · {{ quotedCode.length }} 字符</span>
            <button class="sep-chat-close" @click="quotedCode = ''" title="移除引用">×</button>
          </div>
          <pre>{{ quotedCode }}</pre>
        </div>
        <div class="sep-chat-input">
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
.sep-page { position: fixed; inset: 0; z-index: 200; background: var(--c-panel); display: flex; flex-direction: column; }
.sep-top { display: flex; align-items: center; justify-content: space-between; padding: 12px 18px; border-bottom: 1px solid var(--c-border); flex-shrink: 0; }
.sep-crumb { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.sep-sep { color: var(--c-secondary); }
.sep-owner { color: var(--c-secondary); }
.sep-title { font-weight: 700; color: var(--c-fg); }
.sep-top-actions { display: flex; gap: 8px; }
.sep-loading { flex: 1; display: flex; align-items: center; justify-content: center; gap: 8px; color: var(--c-secondary); }
.sep-body { flex: 1; min-height: 0; display: flex; gap: 0; }
.sep-body.with-chat .sep-main { margin-right: 0; }
.sep-left { flex: 0 0 300px; border-right: 1px solid var(--c-border); padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.sep-main { flex: 1; min-width: 0; padding: 16px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.sep-field { display: flex; flex-direction: column; gap: 4px; }
.sep-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.sep-field .req { color: var(--c-danger); font-style: normal; }
.sep-field input, .sep-field select { width: 100%; padding: 6px 9px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; box-sizing: border-box; }
.sep-field input:focus, .sep-field select:focus { border-color: var(--c-fg); }
.sep-row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.sep-switch { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-fg); padding-top: 4px; }
.sep-block { display: flex; flex-direction: column; gap: 8px; }
.sep-block-head { display: flex; align-items: center; justify-content: space-between; }
.sep-block-title { font-size: 13px; font-weight: 700; color: var(--c-fg); }
.sep-tpl-btns { display: flex; gap: 6px; flex-wrap: wrap; }
.sep-hint { font-size: 12px; color: var(--c-secondary); line-height: 1.6; }
.sep-hint code { font-family: ui-monospace, Consolas, monospace; background: var(--c-muted); padding: 0 4px; border-radius: 4px; }
.sep-api { margin-top: 4px; }
.sep-params { display: flex; flex-direction: column; gap: 4px; }
.sep-param-row { display: grid; grid-template-columns: 1.1fr 1.1fr 0.9fr 40px 1fr 28px; gap: 6px; align-items: center; }
.sep-param-head { font-size: 11px; color: var(--c-secondary); padding: 0 2px; }
.sep-param-row input[type="text"], .sep-param-row select { padding: 5px 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12px; width: 100%; box-sizing: border-box; outline: none; }
.sep-param-row input[type="checkbox"] { width: 15px; height: 15px; }
.sep-rm { width: 26px; height: 26px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; font-size: 15px; line-height: 1; }
.sep-rm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.sep-code-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.sep-sel-tip { margin-top: 6px; font-size: 11.5px; color: #8b5cf6; background: rgba(139, 92, 246, 0.08); border: 1px dashed rgba(139, 92, 246, 0.4); border-radius: var(--radius-sm); padding: 5px 9px; }
.sep-test { border-top: 1px solid var(--c-border); padding-top: 12px; }
.sep-test-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
.sep-mock summary { font-size: 12px; color: var(--c-secondary); cursor: pointer; }
.sep-mock-body { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.sep-mock-body input, .sep-mock-body textarea { padding: 5px 9px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12px; outline: none; }
.sep-mock-body textarea { font-family: ui-monospace, Consolas, monospace; resize: vertical; }
.sep-test-actions { display: flex; justify-content: flex-end; }
.sep-result { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 10px 12px; background: var(--c-muted); font-size: 12px; display: flex; flex-direction: column; gap: 6px; }
.sep-result.err { color: var(--c-danger); }
.sep-result.fail { border-color: var(--c-danger); }
.sep-result-meta { display: flex; align-items: center; gap: 10px; color: var(--c-secondary); }
.sep-status { font-weight: 800; }
.sep-status.ok { color: var(--c-success, #16A34A); }
.sep-status.fail { color: var(--c-danger); }
.sep-err-text { color: var(--c-danger); font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; word-break: break-all; }
.sep-section-label { font-weight: 700; color: var(--c-secondary); }
.sep-result pre { margin: 0; padding: 8px; border-radius: var(--radius-sm); background: var(--c-panel); font-family: ui-monospace, Consolas, monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; max-height: 220px; overflow-y: auto; }
.sep-error { padding: 8px 10px; border-radius: var(--radius-sm); background: rgba(220, 38, 38, 0.08); color: var(--c-danger); font-size: 12px; }

/* AI 聊天面板 */
.ai-btn.active { border-color: #8b5cf6; color: #8b5cf6; }
.ai-btn.active:hover { background: rgba(139, 92, 246, 0.1); }
.sep-chat { flex: 0 0 360px; display: flex; flex-direction: column; min-height: 0; border-left: 1px solid var(--c-border); background: rgba(139, 92, 246, 0.03); }
.sep-chat-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; border-bottom: 1px solid var(--c-border); flex-shrink: 0; }
.sep-chat-title { font-size: 13px; font-weight: 700; color: #8b5cf6; }
.sep-chat-close { width: 26px; height: 26px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); font-size: 16px; line-height: 1; cursor: pointer; }
.sep-chat-close:hover { background: rgba(139, 92, 246, 0.12); color: var(--c-fg); }
.sep-chat-body { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; min-height: 200px; }
.sep-chat-empty { color: var(--c-secondary); font-size: 12px; text-align: center; line-height: 2; margin-top: 40%; }
.sep-chat-msg.user { display: flex; justify-content: flex-end; }
.sep-chat-bubble { background: rgba(139, 92, 246, 0.14); border-radius: 10px 10px 2px 10px; padding: 8px 11px; font-size: 12.5px; color: var(--c-fg); max-width: 88%; white-space: pre-wrap; word-break: break-word; }
.sep-chat-msg.assistant { display: flex; flex-direction: column; gap: 6px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 9px 11px; background: var(--c-panel); font-size: 12.5px; }
.sep-chat-msg.assistant .spinner { width: 13px; height: 13px; border-width: 2px; margin-right: 4px; }
.sep-chat-code-pre { margin: 0; padding: 8px; border-radius: var(--radius-sm); background: var(--c-muted); font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; white-space: pre-wrap; word-break: break-all; max-height: 240px; overflow-y: auto; }
.sep-chat-cursor { display: inline-block; color: #8b5cf6; animation: sep-cursor-blink 0.9s steps(1) infinite; }
@keyframes sep-cursor-blink { 50% { opacity: 0; } }
.sep-ai-error { padding: 7px 9px; border-radius: var(--radius-sm); background: rgba(220, 38, 38, 0.08); color: var(--c-danger); font-size: 12px; }
.sep-ai-explain { font-size: 12.5px; color: var(--c-fg); line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.sep-ai-meta { font-size: 11px; color: var(--c-secondary); }
.sep-ai-actions { display: flex; gap: 8px; }
.diff-toggle { color: #8b5cf6; border-color: rgba(139, 92, 246, 0.4); align-self: flex-start; }
.diff-toggle:hover { background: rgba(139, 92, 246, 0.1); }
.sep-diff { border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-muted); font-family: ui-monospace, Consolas, monospace; font-size: 11px; line-height: 1.55; overflow: auto; max-height: 320px; }
.sep-diff-row { display: flex; align-items: stretch; }
.sep-diff-sign { flex: 0 0 22px; text-align: center; color: var(--c-secondary); user-select: none; }
.sep-diff-row.add { background: rgba(34, 197, 94, 0.14); }
.sep-diff-row.add .sep-diff-sign { color: #16a34a; font-weight: 700; }
.sep-diff-row.del { background: rgba(220, 38, 38, 0.12); }
.sep-diff-row.del .sep-diff-sign { color: var(--c-danger); font-weight: 700; }
.sep-diff-text { flex: 1; padding: 0 6px 0 0; white-space: pre-wrap; word-break: break-all; min-height: 1.4em; }
.sep-chat-quote { border: 1px dashed rgba(139, 92, 246, 0.4); border-radius: var(--radius-sm); margin: 0 10px 8px; background: var(--c-panel); }
.sep-chat-quote-head { display: flex; align-items: center; justify-content: space-between; padding: 6px 9px; font-size: 11px; color: #8b5cf6; border-bottom: 1px dashed rgba(139, 92, 246, 0.3); }
.sep-chat-quote pre { margin: 0; padding: 8px 9px; font-family: ui-monospace, Consolas, monospace; font-size: 11px; white-space: pre-wrap; word-break: break-all; max-height: 110px; overflow-y: auto; color: var(--c-secondary); }
.sep-chat-quote-pre { margin: 0 0 4px; padding: 6px 8px; border-radius: var(--radius-sm); border-left: 2px solid rgba(139, 92, 246, 0.5); background: var(--c-muted); font-family: ui-monospace, Consolas, monospace; font-size: 10.5px; white-space: pre-wrap; word-break: break-all; max-height: 90px; overflow-y: auto; color: var(--c-secondary); }
.sep-chat-input { border-top: 1px solid var(--c-border); padding: 10px; display: flex; gap: 8px; align-items: flex-end; flex-shrink: 0; }
.sep-chat-input textarea { flex: 1; padding: 8px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12.5px; line-height: 1.5; resize: none; outline: none; box-sizing: border-box; font-family: var(--font, inherit); }
.sep-chat-input textarea:focus { border-color: #8b5cf6; }
.sep-chat-input .btn { flex-shrink: 0; }

.ghost { background: transparent; border-color: transparent; color: var(--c-secondary); }
.ghost:hover { background: var(--c-muted); color: var(--c-fg); }
</style>
