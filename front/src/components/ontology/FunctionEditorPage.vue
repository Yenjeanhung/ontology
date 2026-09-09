<script setup>
import { ref, reactive, computed, watch, onMounted, nextTick } from 'vue'
import {
  fetchOntologyCategories, getOntologyCategoryDetail,
  fetchOntologyFunctions, createOntologyFunction, updateOntologyFunction, deleteOntologyFunction, testOntologyFunction,
  fetchAllDerivedProperties, createDerivedProperty, updateDerivedProperty, deleteDerivedProperty, materializeDerivedProperty,
  fetchEntities, testDerivedProperty, getOntologyFunction, aiAssistFunctionCode,
} from '../../api'
import PythonEditor from '../workflow/PythonEditor.vue'
import { useToast } from '../../composables/useToast'

const _toast = useToast()
// 兼容 toast(msg, 'success' | 'error' | 'warning' | 'info') 的调用风格
const toast = (msg, type = 'info') => {
  const fn = { success: _toast.success, error: _toast.error, warning: _toast.info, info: _toast.info }[type]
  fn ? fn(msg) : _toast.info(msg)
}

// ── 分类 / 本体上下文 ──
const categories = ref([])
const categoryId = ref('')
const ontologies = ref([])
const ontologyId = ref('')
const tab = ref(sessionStorage.getItem('fnPage.tab') === 'derived' ? 'derived' : 'functions')
watch(tab, v => { try { sessionStorage.setItem('fnPage.tab', v) } catch { /* 忽略隐私模式 */ } })

async function loadCategories() {
  categories.value = await fetchOntologyCategories()
  if (categories.value.length && !categoryId.value) {
    categoryId.value = categories.value[0].id
  }
}

async function loadOntologies() {
  ontologies.value = []
  ontologyId.value = ''
  if (!categoryId.value) return
  const detail = await getOntologyCategoryDetail(categoryId.value)
  ontologies.value = detail?.ontologies || []
}

watch(categoryId, () => { loadOntologies().then(loadAll) })

// ── 函数管理 ──
const functions = ref([])
const loadingFns = ref(false)
const selectedFn = ref(null)
const fnForm = ref(emptyFnForm())
const fnSaving = ref(false)
const fnTesting = ref(false)
const fnTestParams = ref('{}')
const fnTestMockEntity = ref('')
const fnTestResult = ref(null)
const searchFn = ref('')

function emptyFnForm() {
  return {
    id: '', name: '', code: '', description: '',
    timeout_seconds: 30, is_enabled: true,
    language: 'python',
    params_schema: [],
    code_text: 'def run(params, entity, context):\n    """只读函数：返回可 JSON 序列化的结果\n\n    params:  调用参数(dict)\n    entity:  当前实体(dict, 含 properties)\n    context: 运行上下文(dict)\n    """\n    return {"echo": params}\n',
  }
}

const filteredFns = computed(() => {
  const q = (searchFn.value || '').toLowerCase().trim()
  if (!q) return functions.value
  return functions.value.filter(f =>
    (f.name || '').toLowerCase().includes(q) || (f.code || '').toLowerCase().includes(q))
})

async function loadFunctions() {
  if (!categoryId.value) return
  loadingFns.value = true
  try {
    functions.value = await fetchOntologyFunctions(categoryId.value, ontologyId.value)
    if (!selectedFn.value && functions.value.length) selectFn(functions.value[0])
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loadingFns.value = false
  }
}

function selectFn(fn) {
  selectedFn.value = fn
  fnTestResult.value = null
  fnForm.value = {
    id: fn.id,
    name: fn.name, code: fn.code, description: fn.description || '',
    timeout_seconds: fn.timeout_seconds ?? 30,
    is_enabled: fn.is_enabled !== false,
    language: fn.language || 'python',
    params_schema: (fn.params_schema || []).map(p => ({ ...p })),
    code_text: fn.code_text || '',
  }
  const tpl = {}
  for (const p of fnForm.value.params_schema) tpl[p.name] = p.type === 'number' ? 0 : p.type === 'boolean' ? false : ''
  fnTestParams.value = JSON.stringify(tpl, null, 2)
  fnTestMockEntity.value = ''
}

function newFn() {
  selectedFn.value = null
  fnForm.value = emptyFnForm()
  fnTestResult.value = null
  fnTestParams.value = '{}'
  fnTestMockEntity.value = ''
}

function duplicateFn(fn) {
  selectFn(fn)
  selectedFn.value = null          // 置空 → 保存走"新建"，不会覆盖原函数
  fnForm.value.id = ''
  fnForm.value.name = `${fn.name}_copy`
  fnForm.value.code = `${fn.code || ''}_copy`.slice(0, 64)
  toast('已复制为新函数，请检查编码后保存', 'success')
}

function addParam() { fnForm.value.params_schema.push({ name: '', type: 'string', required: true, description: '' }) }
function removeParam(i) { fnForm.value.params_schema.splice(i, 1) }

async function saveFn() {
  const f = fnForm.value
  if (!f.name || !f.code) { toast('请填写函数名称与编码', 'error'); return }
  const payload = {
    name: f.name, code: f.code, description: f.description,
    ontology_id: ontologyId.value || '',
    params_schema: f.params_schema.filter(p => p.name),
    code_text: f.code_text, language: f.language || 'python',
    timeout_seconds: Number(f.timeout_seconds) || 30,
    is_enabled: !!f.is_enabled,
  }
  fnSaving.value = true
  try {
    if (f.id) {
      await updateOntologyFunction(f.id, payload)
      toast('函数已更新', 'success')
    } else {
      const created = await createOntologyFunction(categoryId.value, payload)
      fnForm.value.id = created.id
      selectedFn.value = created
      toast('函数已创建', 'success')
    }
    await loadFunctions()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    fnSaving.value = false
  }
}

async function removeFn(fn) {
  if (!confirm(`确认删除函数「${fn.name}」？`)) return
  try {
    await deleteOntologyFunction(fn.id)
    if (selectedFn.value?.id === fn.id) newFn()
    await loadFunctions()
    toast('已删除', 'success')
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function runTest() {
  let params = {}
  try { params = JSON.parse(fnTestParams.value || '{}') }
  catch { toast('参数 JSON 格式不正确', 'error'); return }
  let mock_entity = null
  if (fnTestMockEntity.value.trim()) {
    try { mock_entity = JSON.parse(fnTestMockEntity.value) }
    catch { toast('模拟实体 JSON 格式不正确', 'error'); return }
  }
  if (!fnForm.value.id) { await saveFn(); if (!fnForm.value.id) return }
  fnTesting.value = true
  fnTestResult.value = null
  try {
    fnTestResult.value = await testOntologyFunction(fnForm.value.id, { params, mock_entity })
  } catch (e) {
    fnTestResult.value = { success: false, error: e.message }
  } finally {
    fnTesting.value = false
  }
}

// ── AI 辅助编写函数代码（参考本体服务的 AI 辅助：SSE 流式 + 多轮对话 + 应用代码）──
const fnCodeEditorRef = ref(null)
const aiChatOpen = ref(false)
const aiMessages = ref([]) // { role: 'user'|'assistant', content, code?, params?, streaming? }
const aiInput = ref('')
const aiLoading = ref(false)
const aiBodyRef = ref(null)
const aiQuotedCode = ref('') // 编辑器中选中的代码片段（作为本轮重点上下文）

function toggleAiChat() {
  aiChatOpen.value = !aiChatOpen.value
  if (aiChatOpen.value) scrollAiChat()
}

function onFnCodeSelection(sel) {
  aiQuotedCode.value = (sel?.text || '').trim()
}

function clearQuote() {
  aiQuotedCode.value = ''
  fnCodeEditorRef.value?.focus?.()
}

function scrollAiChat() {
  nextTick(() => {
    const el = aiBodyRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 思考区是独立的内层滚动容器（max-height + overflow-y），流式增量时必须滚动它自身
const aiThinkRef = ref(null)
function scrollThinkBody() {
  nextTick(() => {
    // v-for 内的模板 ref 在 Vue 3 中是数组；流式中的思考区是其中最后一个元素
    const v = aiThinkRef.value
    const el = Array.isArray(v) ? v[v.length - 1] : v
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 把 assistant 回复拆成 文本/代码 段落渲染（支持流式中尚未闭合的代码块）
function splitSegments(msg) {
  const segs = []
  const re = /```(\w+)?[ \t]*\n([\s\S]*?)(?:```|$)/g
  let last = 0
  let m
  while ((m = re.exec(msg.content))) {
    if (m.index > last) segs.push({ type: 'text', text: msg.content.slice(last, m.index).trim() })
    segs.push({ type: 'code', lang: (m[1] || '').toLowerCase(), code: m[2].replace(/\n$/, '') })
    last = re.lastIndex
    if (m[0].length === 0) re.lastIndex++ // 空匹配保护，防死循环
  }
  if (last < msg.content.length) segs.push({ type: 'text', text: msg.content.slice(last).trim() })
  if (!segs.length) segs.push({ type: 'text', text: msg.content })
  return segs
}

// AI 返回的类型可能不在下拉选项里，归一化到 params_schema 支持的类型
function normParamType(t) {
  const s = String(t || '').toLowerCase()
  if (['number', 'int', 'integer', 'float', 'double'].includes(s)) return 'number'
  if (['boolean', 'bool'].includes(s)) return 'boolean'
  if (['object', 'dict', 'list', 'array'].includes(s)) return 'object'
  return 'string'
}

function applyAiCode(seg, msg) {
  if (!seg?.code) return
  fnForm.value.code_text = seg.code
  // AI 给出参数定义时以它为准同步参数表（代码与参数是配套的）
  let synced = false
  if (msg.params?.length) {
    fnForm.value.params_schema = msg.params
      .filter((p) => p.name)
      .map((p) => ({
        name: p.name, type: normParamType(p.type),
        required: p.required !== false, description: p.description || '',
      }))
    synced = fnForm.value.params_schema.length > 0
  }
  toast(synced ? '已应用代码并同步参数，点击「保存」生效' : '已应用到编辑器，点击「保存」生效', 'success')
}

async function sendAiMessage() {
  const q = aiInput.value.trim()
  if (!q || aiLoading.value) return
  aiInput.value = ''
  aiMessages.value.push({ role: 'user', content: q })
  // 必须用 reactive 包装：push 进响应式数组后 Vue 渲染的是代理副本，
  // 直接修改普通对象 onDelta 的增量不会触发流式渲染
  const reply = reactive({ role: 'assistant', content: '', thinking: '', thinkCollapsed: false, streaming: true })
  aiMessages.value.push(reply)
  aiLoading.value = true
  scrollAiChat()
  try {
    const history = aiMessages.value
      .filter((m) => !m.streaming && m.content)
      .slice(-10)
      .map((m) => ({ role: m.role, content: m.content }))
    const result = await aiAssistFunctionCode({
      prompt: q,
      name: fnForm.value.name,
      code: fnForm.value.code,
      description: fnForm.value.description,
      owner_name: ontologies.value.find((o) => o.id === ontologyId.value)?.name || '',
      current_code: fnForm.value.code_text,
      selected_code: aiQuotedCode.value,
      history,
      onThinking: (t) => { reply.thinking += t; scrollThinkBody() },
      onDelta: (t) => { reply.content += t; scrollAiChat() },
    })
    if (result?.code_text) {
      reply.content = (result.explanation ? result.explanation + '\n\n' : '')
        + '```python\n' + result.code_text + '\n```\n' + (result.explanation ? '' : reply.content)
      reply.code = result.code_text
      reply.params = result.params || []
    }
    aiQuotedCode.value = ''
  } catch (e) {
    reply.content = reply.content || ''
    reply.error = e.message
  } finally {
    reply.streaming = false
    reply.thinkCollapsed = true
    aiLoading.value = false
    scrollAiChat()
  }
}

// ── 派生属性管理（进入即加载全部，本体作为筛选条件）──
const allDerived = ref([])
const loadingDerived = ref(false)
const showDerivedModal = ref(false)
const searchDp = ref('')
const derivedForm = ref(emptyDerivedForm())
const derivedSaving = ref(false)
const materializingId = ref('')

const derived = computed(() => {
  let list = allDerived.value
  if (ontologyId.value) list = list.filter((d) => d.ontology_id === ontologyId.value)
  const q = (searchDp.value || '').toLowerCase().trim()
  if (!q) return list
  return list.filter(d => [d.name, d.code, d.ontology_name, d.data_type, d.source_kind, d.description]
    .some(v => (v || '').toLowerCase().includes(q)))
})

// 筛选下拉选项：当前分类的全部本体 + 全量数据中出现过的其他本体
const ontologyOptions = computed(() => {
  const m = new Map()
  for (const o of ontologies.value) if (o.id && !m.has(o.id)) m.set(o.id, o.name)
  for (const d of allDerived.value) {
    if (d.ontology_id && !m.has(d.ontology_id)) m.set(d.ontology_id, d.ontology_name || d.ontology_id)
  }
  return [...m].map(([id, name]) => ({ id, name }))
})

function emptyDerivedForm() {
  return {
    id: '', name: '', code: '', data_type: 'number',
    source_kind: 'function', function_id: '', graph_metric: 'degree',
    materialize_mode: 'virtual', is_enabled: true, params: {},
  }
}

async function loadDerived() {
  loadingDerived.value = true
  try {
    allDerived.value = (await fetchAllDerivedProperties()) || []
  } catch (e) {
    allDerived.value = []
  } finally {
    loadingDerived.value = false
  }
}

// 编辑弹窗里关联函数的参数模式（随函数切换动态加载）
const derivedFnSchema = ref([])

// 弹窗里展示的「所属本体」：新建 = 顶部筛选选中的本体；编辑 = 该派生属性已绑定的本体（只读）
const derivedOntologyName = ref('')

function openDerivedNew() {
  derivedForm.value = emptyDerivedForm()
  derivedFnSchema.value = []
  derivedOntologyName.value = ontologies.value.find((o) => o.id === ontologyId.value)?.name || ''
  showDerivedModal.value = true
}

function openDerivedEdit(dp) {
  derivedForm.value = {
    id: dp.id, name: dp.name, code: dp.code, data_type: dp.data_type || 'number',
    source_kind: dp.source_kind || 'function', function_id: dp.function_id || '',
    graph_metric: dp.graph_metric || 'degree',
    materialize_mode: dp.materialize_mode === 'materialized' ? 'materialized' : 'virtual',
    is_enabled: dp.is_enabled !== false,
    params: { ...(dp.params || {}) },
  }
  derivedOntologyName.value = dp.ontology_name
    || ontologies.value.find((o) => o.id === dp.ontology_id)?.name
    || dp.ontology_id
    || ''
  derivedFnSchema.value = []
  if (derivedForm.value.source_kind === 'function' && derivedForm.value.function_id) {
    fetchFnSchema(derivedForm.value.function_id).then((s) => { derivedFnSchema.value = s })
  }
  showDerivedModal.value = true
}

// 切换关联函数后重载参数模式并清空已填参数
async function onDerivedFnChange() {
  derivedFnSchema.value = derivedForm.value.function_id ? await fetchFnSchema(derivedForm.value.function_id) : []
  derivedForm.value.params = {}
}

async function saveDerived() {
  const f = derivedForm.value
  if (!f.name || !f.code) { toast('请填写名称与编码', 'error'); return }
  if (f.source_kind === 'function' && !f.function_id) { toast('请选择关联函数', 'error'); return }
  const payload = {
    name: f.name, code: f.code, data_type: f.data_type, source_kind: f.source_kind,
    function_id: f.source_kind === 'function' ? f.function_id : '',
    graph_metric: f.source_kind === 'graph_metric' ? f.graph_metric : '',
    materialize_mode: f.materialize_mode, is_enabled: !!f.is_enabled,
    params: f.source_kind === 'function' ? normalizeParams(derivedFnSchema.value, f.params) : {},
  }
  derivedSaving.value = true
  try {
    if (f.id) await updateDerivedProperty(f.id, payload)
    else await createDerivedProperty(categoryId.value, ontologyId.value, payload)
    showDerivedModal.value = false
    toast('已保存', 'success')
    await loadDerived()
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    derivedSaving.value = false
  }
}

async function removeDerived(dp) {
  if (!confirm(`确认删除派生属性「${dp.name}」？`)) return
  try {
    await deleteDerivedProperty(dp.id)
    await loadDerived()
    toast('已删除', 'success')
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function materialize(dp) {
  materializingId.value = dp.id
  try {
    const res = await materializeDerivedProperty(dp.id)
    toast(`物化完成：成功 ${res.updated || 0} / 失败 ${res.failed || 0}`, res.failed ? 'warning' : 'success')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    materializingId.value = ''
  }
}

// ── 派生属性测试：选一个真实实体试算，可选把结果写入实体属性；入参在测试时动态填写 ──
const showDpTestModal = ref(false)
const dpTest = ref(emptyDpTest())

function emptyDpTest() {
  return { dp: null, entities: [], entityId: '', search: '', write: true, running: false, loadingEntities: false, result: null, schema: [], params: {} }
}

// 拉取函数的参数模式（列表里可能没有，回退到按 id 拉详情）
async function fetchFnSchema(functionId) {
  if (!functionId) return []
  const local = functions.value.find((f) => f.id === functionId)
  if (local) return (local.params_schema || []).filter((p) => p.name)
  try {
    const fn = await getOntologyFunction(functionId)
    return (fn?.params_schema || []).filter((p) => p.name)
  } catch {
    return []
  }
}

async function loadDpFnSchema(dp) {
  const t = dpTest.value
  t.schema = dp.source_kind === 'function' ? await fetchFnSchema(dp.function_id) : []
}

// 按 params_schema 的类型归一化入参（保存运行参数与测试试算共用）
// skipEmpty=true 时丢弃未填写的键（测试用：留空即回退到派生属性已配的运行参数）
function normalizeParams(schema, raw, skipEmpty = false) {
  const src = raw || {}
  if (!schema?.length) {
    return skipEmpty
      ? Object.fromEntries(Object.entries(src).filter(([, v]) => v !== undefined && v !== ''))
      : { ...src }
  }
  const out = {}
  for (const p of schema) {
    const v = src[p.name]
    if (v === undefined || v === '') {
      if (skipEmpty) continue
      if (p.required) out[p.name] = p.type === 'number' ? 0 : p.type === 'boolean' ? false : ''
      continue
    }
    if (p.type === 'number') {
      const n = Number(v)
      out[p.name] = Number.isFinite(n) ? n : v
    } else if (p.type === 'boolean') {
      out[p.name] = v === true || v === 'true'
    } else if (p.type === 'object') {
      try { out[p.name] = typeof v === 'string' ? JSON.parse(v) : v } catch { out[p.name] = v }
    } else {
      out[p.name] = v
    }
  }
  return out
}

async function openDpTest(dp) {
  dpTest.value = emptyDpTest()
  dpTest.value.dp = dp
  showDpTestModal.value = true
  searchDpEntities()
  await loadDpFnSchema(dp)
}

async function searchDpEntities() {
  const t = dpTest.value
  if (!t.dp) return
  t.loadingEntities = true
  try {
    const res = await fetchEntities({ ontology_id: t.dp.ontology_id, q: t.search, page: 1, page_size: 50 })
    t.entities = res?.items || res || []
  } catch {
    t.entities = []
  } finally {
    t.loadingEntities = false
  }
}

async function runDpTest() {
  const t = dpTest.value
  if (!t.entityId) { toast('请先选择测试实体', 'error'); return }
  t.running = true
  t.result = null
  try {
    t.result = await testDerivedProperty(t.dp.id, t.entityId, t.write, normalizeParams(t.schema, t.params, true))
    toast(
      t.result.ok ? (t.write ? '测试成功，结果已写入实体属性' : '测试成功') : `测试失败：${t.result.error || '未知错误'}`,
      t.result.ok ? 'success' : 'error',
    )
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    t.running = false
  }
}

const sourceLabel = { function: '函数', graph_metric: '图指标' }
const metricLabel = { degree: '度数', pagerank: 'PageRank', betweenness: '介数中心性', community: '社区' }

// 函数 id → 名称（后端列表不回传 func_name，前端映射）
const funcNameMap = computed(() => {
  const m = {}
  for (const f of functions.value) m[f.id] = f.name
  return m
})

function loadAll() {
  if (tab.value === 'functions') loadFunctions()
  else { loadDerived(); if (!functions.value.length) loadFunctions() }
}

watch(tab, loadAll)

onMounted(async () => {
  try { await loadCategories(); await loadOntologies(); await loadFunctions() }
  catch (e) { toast(e.message, 'error') }
})
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">函数与派生属性</h2>
        <!-- <span class="page-subtitle">S3 · 只读函数与派生属性管理（计算 / 物化）</span> -->
      </div>
      <div class="head-controls">
        <select v-model="categoryId" class="ctrl-select">
          <option value="" disabled>选择分类</option>
          <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
        <div class="seg">
          <button :class="{ on: tab === 'functions' }" @click="tab = 'functions'">函数</button>
          <button :class="{ on: tab === 'derived' }" @click="tab = 'derived'">派生属性</button>
        </div>
      </div>
    </div>

    <!-- ══ 函数管理 ══ -->
    <div v-if="tab === 'functions'" class="fn-layout">
      <div class="fn-list">
        <div class="fn-list-toolbar">
          <input v-model="searchFn" type="text" class="fn-search" placeholder="搜索函数...">
          <button class="primary-btn sm" @click="newFn">+ 新建</button>
        </div>
        <div v-if="loadingFns" class="hint">加载中...</div>
        <div v-else-if="!filteredFns.length" class="hint">暂无函数，点击「+ 新建」创建</div>
        <div v-else class="fn-items">
          <div
            v-for="f in filteredFns" :key="f.id"
            class="fn-item" :class="{ active: selectedFn?.id === f.id }"
            @click="selectFn(f)"
          >
            <div class="fn-item-name">{{ f.name }}</div>
            <div class="fn-item-meta">
              <span v-if="f.is_enabled === false" class="tag off">停用</span>
            </div>
            <button class="fn-copy-btn" title="复制为新函数" @click.stop="duplicateFn(f)">⧉</button>
          </div>
        </div>
      </div>

      <div class="fn-editor">
        <div class="sec-head">
          <span>{{ fnForm.id ? '编辑函数' : '新建函数' }}</span>
          <div class="sec-actions">
            <button v-if="fnForm.id" class="danger-btn sm" @click="removeFn(selectedFn)">删除</button>
            <button class="btn sm" :disabled="fnSaving" @click="saveFn">{{ fnSaving ? '保存中...' : '保存' }}</button>
          </div>
        </div>

        <div class="form-grid">
          <label class="field"><span>名称 *</span><input v-model="fnForm.name" placeholder="如 机龄计算"></label>
          <label class="field"><span>编码 *</span><input v-model="fnForm.code" placeholder="如 compute_age" :disabled="!!fnForm.id" maxlength="64"></label>
          <label class="field"><span>超时(秒，1-120)</span><input v-model.number="fnForm.timeout_seconds" type="number" min="1" max="120"></label>
          <label class="field"><span>启用</span>
            <select v-model="fnForm.is_enabled"><option :value="true">启用</option><option :value="false">停用</option></select>
          </label>
          <label class="field wide"><span>描述</span><input v-model="fnForm.description" placeholder="可选"></label>
        </div>

        <div class="sec-sub">参数（params_schema）</div>
        <div class="params-box">
          <div v-for="(p, i) in fnForm.params_schema" :key="i" class="param-row">
            <input v-model="p.name" placeholder="参数名">
            <select v-model="p.type">
              <option>string</option><option>number</option><option>boolean</option><option>object</option>
            </select>
            <select v-model="p.required"><option :value="true">必填</option><option :value="false">可选</option></select>
            <input v-model="p.description" placeholder="说明" class="grow">
            <button class="danger-btn sm" @click="removeParam(i)">✕</button>
          </div>
          <button class="btn sm" @click="addParam">+ 添加参数</button>
        </div>

        <div class="sec-sub code-sub-row">
          <span>函数代码（沙箱执行，仅只读上下文）</span>
          <button class="ai-toggle" :class="{ on: aiChatOpen }" :title="aiChatOpen ? '关闭 AI 辅助' : 'AI 辅助编写函数代码'" @click="toggleAiChat">
            ✦ AI 辅助
          </button>
        </div>
        <div class="code-wrap">
          <PythonEditor ref="fnCodeEditorRef" v-model="fnForm.code_text" @selection-change="onFnCodeSelection" />
        </div>

        <div class="sec-sub">测试运行</div>
        <div class="test-box">
          <div class="test-cols">
            <label class="field"><span>params（JSON）</span>
              <textarea v-model="fnTestParams" rows="6" spellcheck="false"></textarea>
            </label>
            <label class="field"><span>模拟实体（可选 JSON，含 properties）</span>
              <textarea v-model="fnTestMockEntity" rows="6" spellcheck="false" placeholder='{"name":"B-1234","properties":{"first_flight":"2015-01-01"}}'></textarea>
            </label>
          </div>
          <button class="primary-btn sm" :disabled="fnTesting" @click="runTest">{{ fnTesting ? '运行中...' : '▶ 运行测试' }}</button>
          <template v-if="fnTestResult">
            <div class="sec-sub">测试结果</div>
            <pre class="test-result" :class="{ ok: fnTestResult.success, err: !fnTestResult.success }">{{ JSON.stringify(fnTestResult, null, 2) }}</pre>
          </template>
        </div>
      </div>

      <!-- AI 辅助聊天面板 -->
      <aside v-if="aiChatOpen" class="ai-chat">
        <div class="ai-chat-head">
          <span class="ai-chat-title">✦ AI 辅助编写函数</span>
          <button class="close-btn" @click="aiChatOpen = false">✕</button>
        </div>
        <div ref="aiBodyRef" class="ai-chat-body">
          <div v-if="!aiMessages.length" class="ai-chat-empty">
            描述你想要的函数功能，AI 将生成符合沙箱约束的只读函数代码；也可先在编辑器中<b>选中一段代码</b>再提问，针对片段修改。
          </div>
          <div v-for="(m, i) in aiMessages" :key="i" class="ai-msg" :class="m.role">
            <div class="ai-bubble">
              <template v-if="m.role === 'user'">{{ m.content }}</template>
              <template v-else>
                <div v-if="m.thinking" class="ai-think" :class="{ streaming: m.streaming && !m.content }">
                  <button class="ai-think-toggle" @click="m.thinkCollapsed = !m.thinkCollapsed">
                    <span class="ai-think-arrow">{{ m.thinkCollapsed ? '▸' : '▾' }}</span>
                    <span class="ai-think-label">{{ m.streaming && !m.content ? '思考中…' : '已深度思考' }}</span>
                  </button>
                  <pre v-if="!m.thinkCollapsed" ref="aiThinkRef" class="ai-think-body">{{ m.thinking }}</pre>
                </div>
                <template v-for="(s, j) in splitSegments(m)" :key="j">
                  <div v-if="s.type === 'text' && s.text" class="ai-text">{{ s.text }}</div>
                  <div v-else-if="s.type === 'code' && s.code" class="ai-code">
                    <pre>{{ s.code }}</pre>
                    <button v-if="s.lang !== 'json'" class="ai-apply" :disabled="aiLoading" @click="applyAiCode(s, m)">应用到编辑器</button>
                  </div>
                </template>
                <div v-if="m.params?.length" class="ai-params">
                  <div class="ai-params-title">参数定义（应用时自动同步到参数表）</div>
                  <table>
                    <thead><tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr></thead>
                    <tbody>
                      <tr v-for="p in m.params" :key="p.name">
                        <td class="mono">{{ p.name }}</td>
                        <td>{{ p.type || 'string' }}</td>
                        <td>{{ p.required !== false ? '是' : '否' }}</td>
                        <td class="ai-params-desc">{{ p.description || '—' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-if="m.error" class="ai-err">{{ m.error }}</div>
                <div v-if="m.streaming && !m.content && !m.thinking" class="ai-text dim">生成中...</div>
              </template>
            </div>
          </div>
        </div>
        <div v-if="aiQuotedCode" class="ai-quote-bar">
          <span class="ai-quote-label">已引用选中代码（{{ aiQuotedCode.split('\n').length }} 行）</span>
          <button class="ai-quote-clear" @click="clearQuote">移除</button>
        </div>
        <div class="ai-chat-input">
          <textarea
            v-model="aiInput" rows="2"
            placeholder="描述需求，回车发送，Shift+回车换行"
            :disabled="aiLoading"
            @keydown.enter.exact.prevent="sendAiMessage"
          ></textarea>
          <button class="primary-btn sm" :disabled="aiLoading || !aiInput.trim()" @click="sendAiMessage">
            {{ aiLoading ? '生成中...' : '发送' }}
          </button>
        </div>
      </aside>
    </div>

    <!-- ══ 派生属性管理 ══ -->
    <div v-else class="derived-layout">
      <div class="derived-toolbar">
        <select v-model="ontologyId" class="ctrl-select">
          <option value="">全部本体</option>
          <option v-for="o in ontologyOptions" :key="o.id" :value="o.id">{{ o.name }}</option>
        </select>
        <input v-model="searchDp" type="text" class="fn-search" placeholder="搜索派生属性...">
        <button class="btn sm" :disabled="loadingDerived" @click="loadDerived">刷新</button>
        <button class="primary-btn sm" :disabled="!ontologyId" @click="openDerivedNew">+ 新建派生属性</button>
      </div>

      <div v-if="loadingDerived" class="hint pad">加载中...</div>
      <div v-else-if="!derived.length" class="hint pad">暂无派生属性</div>
      <div v-else class="dp-table">
        <div class="dp-row dp-head">
          <span>名称</span><span>编码</span><span>所属本体</span><span>类型</span><span>来源</span><span>定义</span><span>刷新</span><span>状态</span><span></span>
        </div>
        <div v-for="dp in derived" :key="dp.id" class="dp-row">
          <span class="dp-name">{{ dp.name }}</span>
          <span class="mono">{{ dp.code }}</span>
          <span class="dp-onto" :title="dp.ontology_name">{{ dp.ontology_name || '—' }}</span>
          <span>{{ dp.data_type }}</span>
          <span><span class="tag">{{ sourceLabel[dp.source_kind] || dp.source_kind }}</span></span>
          <span class="dp-def" :title="dp.source_kind === 'function' ? (funcNameMap[dp.function_id] || dp.function_id) : (metricLabel[dp.graph_metric] || dp.graph_metric)">
            {{ dp.source_kind === 'function' ? (funcNameMap[dp.function_id] || dp.function_id) : (metricLabel[dp.graph_metric] || dp.graph_metric) }}
          </span>
          <span>{{ dp.materialize_mode === 'materialized' ? '物化' : '读时' }}</span>
          <span><span v-if="dp.is_enabled !== false" class="tag">启用</span><span v-else class="tag off">停用</span></span>
          <span class="dp-ops">
            <button class="btn sm" @click="openDpTest(dp)">测试</button>
            <button v-if="dp.materialize_mode === 'materialized'" class="btn sm" :disabled="materializingId === dp.id" @click="materialize(dp)">
              {{ materializingId === dp.id ? '物化中...' : '物化' }}
            </button>
            <button class="btn sm" @click="openDerivedEdit(dp)">编辑</button>
            <button class="danger-btn sm" @click="removeDerived(dp)">删除</button>
          </span>
        </div>
      </div>
    </div>

    <!-- 派生属性弹窗 -->
    <div v-if="showDerivedModal" class="modal-overlay">
      <div class="modal-card">
        <div class="modal-head"><h3>{{ derivedForm.id ? '编辑' : '新建' }}派生属性</h3><button class="close-btn" @click="showDerivedModal = false">✕</button></div>
        <div class="modal-body">
          <div class="form-grid">
            <label class="field"><span>所属本体</span>
              <input :value="derivedOntologyName" disabled :title="derivedForm.id ? '创建后不可更换所属本体' : '归属到页面顶部「本体」筛选当前选中的本体'">
            </label>
            <label class="field"><span>名称 *</span><input v-model="derivedForm.name" placeholder="如 机龄"></label>
            <label class="field"><span>编码 *</span><input v-model="derivedForm.code" placeholder="如 age_years" :disabled="!!derivedForm.id"></label>
            <label class="field"><span>数据类型</span>
              <select v-model="derivedForm.data_type"><option>number</option><option>string</option><option>boolean</option></select>
            </label>
            <label class="field"><span>来源</span>
              <select v-model="derivedForm.source_kind">
                <option value="function">函数</option>
                <option value="graph_metric">图指标</option>
              </select>
            </label>
            <label v-if="derivedForm.source_kind === 'function'" class="field"><span>关联函数</span>
              <select v-model="derivedForm.function_id" @change="onDerivedFnChange">
                <option value="">请选择</option>
                <option v-for="f in functions" :key="f.id" :value="f.id">{{ f.name }}（{{ f.code }}）</option>
              </select>
            </label>
            <label v-if="derivedForm.source_kind === 'graph_metric'" class="field"><span>图指标</span>
              <select v-model="derivedForm.graph_metric">
                <option value="degree">度数</option>
                <option value="pagerank">PageRank</option>
                <option value="betweenness">介数中心性</option>
                <option value="community">社区</option>
              </select>
            </label>
            <label class="field"><span>刷新策略</span>
              <select v-model="derivedForm.materialize_mode">
                <option value="virtual">读时计算（实时）</option>
                <option value="materialized">物化（手动/定期刷新）</option>
              </select>
            </label>
            <label class="field"><span>启用</span>
              <select v-model="derivedForm.is_enabled"><option :value="true">启用</option><option :value="false">停用</option></select>
            </label>
          </div>
          <template v-if="derivedForm.source_kind === 'function' && derivedFnSchema.length">
            <div class="sec-sub" style="margin-top: 12px;">运行参数（物化 / 读时计算均使用此参数）</div>
            <div class="form-grid">
              <label v-for="p in derivedFnSchema" :key="p.name" class="field">
                <span>{{ p.name }}{{ p.required ? ' *' : '' }}（{{ p.type }}）{{ p.description || '' }}</span>
                <select v-if="p.type === 'boolean'" v-model="derivedForm.params[p.name]">
                  <option :value="true">true</option><option :value="false">false</option>
                </select>
                <textarea v-else-if="p.type === 'object'" v-model="derivedForm.params[p.name]" rows="2" placeholder='JSON，如 {"k": 1}'></textarea>
                <input v-else-if="p.type === 'number'" v-model="derivedForm.params[p.name]" type="number" :placeholder="p.required ? '必填' : '可选'">
                <input v-else v-model="derivedForm.params[p.name]" :placeholder="p.required ? '必填' : '可选'">
              </label>
            </div>
          </template>
          <div class="hint" style="margin-top: 10px;">函数入参（运行参数）在此配置保存；「测试」弹窗可临时改参试算，不会改动这里的配置。</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showDerivedModal = false">取消</button>
          <button class="primary-btn" :disabled="derivedSaving" @click="saveDerived">{{ derivedSaving ? '保存中...' : '保存' }}</button>
        </div>
      </div>
    </div>

    <!-- 派生属性测试弹窗 -->
    <div v-if="showDpTestModal" class="modal-overlay">
      <div class="modal-card">
        <div class="modal-head"><h3>测试派生属性：{{ dpTest.dp?.name }}</h3><button class="close-btn" @click="showDpTestModal = false">✕</button></div>
        <div class="modal-body">
          <div class="hint" style="margin-bottom: 10px;">
            选择该本体下的一个真实实体进行试算；选择"写入"后，计算结果会写入该实体的属性（键 = 编码 <b class="mono">{{ dpTest.dp?.code }}</b>），相当于调用一次接口落库。
          </div>
          <div class="form-grid">
            <label class="field"><span>搜索实体</span>
              <input v-model="dpTest.search" placeholder="名称关键字，回车搜索" @keyup.enter="searchDpEntities">
            </label>
            <label class="field"><span>测试实体 *</span>
              <select v-model="dpTest.entityId">
                <option value="">{{ dpTest.loadingEntities ? '加载中...' : '请选择' }}</option>
                <option v-for="e in dpTest.entities" :key="e.id" :value="e.id">{{ e.name }}（{{ e.id }}）</option>
              </select>
            </label>
            <label class="field"><span>写入结果</span>
              <select v-model="dpTest.write">
                <option :value="true">写入实体属性</option>
                <option :value="false">仅试算，不写入</option>
              </select>
            </label>
          </div>
          <template v-if="dpTest.dp?.source_kind === 'function' && dpTest.schema.length">
            <div class="sec-sub" style="margin-top: 12px;">函数入参（留空则使用编辑弹窗中配置的运行参数；此处填写仅对本次试算生效，不会保存）</div>
            <div class="form-grid">
              <label v-for="p in dpTest.schema" :key="p.name" class="field">
                <span>{{ p.name }}{{ p.required ? ' *' : '' }}（{{ p.type }}）{{ p.description || '' }}</span>
                <select v-if="p.type === 'boolean'" v-model="dpTest.params[p.name]">
                  <option :value="true">true</option><option :value="false">false</option>
                </select>
                <textarea v-else-if="p.type === 'object'" v-model="dpTest.params[p.name]" rows="2" placeholder='JSON，如 {"k": 1}'></textarea>
                <input v-else-if="p.type === 'number'" v-model="dpTest.params[p.name]" type="number" :placeholder="p.required ? '必填' : '可选'">
                <input v-else v-model="dpTest.params[p.name]" :placeholder="p.required ? '必填' : '可选'">
              </label>
            </div>
          </template>
          <button class="primary-btn" style="margin-top: 10px;" :disabled="dpTest.running || !dpTest.entityId" @click="runDpTest">
            {{ dpTest.running ? '运行中...' : '▶ 运行测试' }}
          </button>
          <template v-if="dpTest.result">
            <div class="sec-sub" style="margin-top: 12px;">测试结果</div>
            <pre class="test-result" :class="{ ok: dpTest.result.ok, err: !dpTest.result.ok }">{{ JSON.stringify(dpTest.result, null, 2) }}</pre>
          </template>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showDpTestModal = false">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 14px; height: 100%; min-height: 0; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); margin: 0; }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }
.head-controls { display: flex; align-items: center; gap: 10px; }
.ctrl-select { padding: 7px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; min-width: 180px; }

.seg { display: inline-flex; border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; }
.seg button { border: 0; background: var(--c-panel); color: var(--c-secondary); padding: 7px 16px; font-size: 13px; font-weight: 600; cursor: pointer; }
.seg button.on { background: var(--c-accent); color: #fff; }

.fn-layout { display: flex; gap: 14px; flex: 1; min-height: 0; }
.fn-list { flex: 0 0 280px; display: flex; flex-direction: column; gap: 10px; border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 10px; overflow: hidden; }
.fn-list-toolbar { display: flex; gap: 8px; }
.fn-search { flex: 1; min-width: 0; padding: 7px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg); color: var(--c-fg); font-size: 12px; outline: none; }
.fn-items { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.fn-item { position: relative; padding: 9px 11px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); cursor: pointer; transition: all 120ms; }
.fn-item:hover { border-color: var(--c-accent); }
.fn-item.active { border-color: var(--c-accent); background: var(--c-muted); }
.fn-item-name { font-size: 13px; font-weight: 600; color: var(--c-fg); }
.fn-item-meta { display: flex; gap: 6px; margin-top: 5px; flex-wrap: wrap; }
.fn-copy-btn { position: absolute; top: 6px; right: 6px; border: none; background: transparent; color: var(--c-secondary); font-size: 13px; line-height: 1; padding: 2px 5px; border-radius: 4px; cursor: pointer; transition: all 120ms; }
.fn-copy-btn:hover { color: var(--c-accent); background: var(--c-muted); }
.tag { font-size: 10px; padding: 1px 7px; border-radius: 8px; background: var(--c-muted); color: var(--c-secondary); }
.tag.off { background: rgba(220,38,38,0.12); color: var(--c-danger); }

.fn-editor { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 14px; overflow-y: auto; }
.sec-head { display: flex; align-items: center; justify-content: space-between; font-size: 14px; font-weight: 700; color: var(--c-fg); }
.sec-actions { display: flex; gap: 8px; }
.sec-sub { font-size: 12px; font-weight: 700; color: var(--c-secondary); margin-top: 4px; }

.form-grid { display: flex; flex-wrap: wrap; gap: 10px 14px; }
.field { display: flex; flex-direction: column; gap: 4px; flex: 1 1 180px; min-width: 150px; }
.field.wide { flex-basis: 100%; }
.field > span { font-size: 11px; font-weight: 600; color: var(--c-secondary); }
.field input, .field select, .field textarea { padding: 7px 9px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg); color: var(--c-fg); font-size: 12px; outline: none; font-family: var(--font); }
.field input:disabled { opacity: 0.55; }
.field textarea { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; resize: vertical; }
.field input:focus, .field select:focus, .field textarea:focus { border-color: var(--c-accent); }

.params-box { display: flex; flex-direction: column; gap: 8px; }
.param-row { display: flex; gap: 6px; align-items: center; }
.param-row input, .param-row select { padding: 6px 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg); color: var(--c-fg); font-size: 12px; outline: none; }
.param-row .grow { flex: 1; min-width: 0; }

.code-wrap { border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; height: 300px; }

/* ── AI 辅助 ── */
.code-sub-row { display: flex; align-items: center; justify-content: space-between; }
.ai-toggle { border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg); color: var(--c-secondary); font-size: 11px; font-weight: 600; padding: 3px 10px; cursor: pointer; }
.ai-toggle:hover { color: var(--c-accent); border-color: var(--c-accent); }
.ai-toggle.on { background: var(--c-accent); border-color: var(--c-accent); color: #fff; }
.ai-chat { flex: 0 0 360px; min-width: 0; align-self: flex-start; height: calc(100vh - 190px); min-height: 460px; display: flex; flex-direction: column; border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); overflow: hidden; }
.ai-chat-head { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 1px solid var(--c-border); }
.ai-chat-title { font-size: 13px; font-weight: 700; color: var(--c-accent); }
.ai-chat-body { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; min-height: 0; scrollbar-width: thin; scrollbar-color: rgba(148, 163, 184, 0.3) transparent; }
.ai-chat-body::-webkit-scrollbar { width: 6px; }
.ai-chat-body::-webkit-scrollbar-thumb { background: rgba(148, 163, 184, 0.3); border-radius: 3px; }
.ai-chat-body::-webkit-scrollbar-track { background: transparent; }
.ai-chat-empty { font-size: 12px; color: var(--c-secondary); line-height: 1.7; padding: 8px 2px; }
.ai-msg { display: flex; }
.ai-msg.user { justify-content: flex-end; }
.ai-bubble { max-width: 88%; padding: 8px 11px; border-radius: 10px; font-size: 12px; line-height: 1.6; }
.ai-msg.user .ai-bubble { background: var(--c-bg); border: 1px solid var(--c-border); color: var(--c-fg); white-space: pre-wrap; word-break: break-word; }
.ai-msg.assistant .ai-bubble { background: var(--c-bg); border: 1px solid var(--c-border); color: var(--c-fg); }
.ai-text { white-space: pre-wrap; word-break: break-word; }
.ai-text.dim { color: var(--c-secondary); }
.ai-code { margin-top: 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); overflow: hidden; }
.ai-code pre { margin: 0; padding: 8px 10px; font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-x: auto; max-height: 220px; overflow-y: auto; white-space: pre; }
.ai-apply { width: 100%; border: 0; border-top: 1px solid var(--c-border); background: var(--c-muted); color: var(--c-accent); font-size: 11px; font-weight: 600; padding: 5px 0; cursor: pointer; }
.ai-apply:hover { background: var(--c-accent); color: #fff; }
.ai-apply:disabled { opacity: 0.5; cursor: not-allowed; }
.ai-err { margin-top: 6px; color: var(--c-danger); font-size: 11px; }
.ai-think { margin-bottom: 8px; border-left: 2px solid rgba(148, 163, 184, 0.35); padding-left: 10px; }
.ai-think-toggle { display: flex; align-items: center; gap: 4px; width: 100%; border: 0; background: transparent; color: var(--c-secondary); font-size: 11px; text-align: left; padding: 2px 0; cursor: pointer; }
.ai-think-toggle:hover { color: var(--c-fg); }
.ai-think-arrow { font-size: 9px; opacity: 0.8; }
.ai-think.streaming .ai-think-label { animation: ai-think-pulse 1.6s ease-in-out infinite; }
.ai-think-body { margin: 2px 0 4px; padding: 0; color: rgba(148, 163, 184, 0.95); font-size: 11px; line-height: 1.65; font-family: var(--font); white-space: pre-wrap; word-break: break-word; max-height: 240px; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: transparent transparent; -webkit-mask-image: linear-gradient(180deg, transparent 0, #000 10px, #000 calc(100% - 14px), transparent 100%); mask-image: linear-gradient(180deg, transparent 0, #000 10px, #000 calc(100% - 14px), transparent 100%); }
.ai-think-body:hover { scrollbar-color: rgba(148, 163, 184, 0.28) transparent; }
.ai-think-body::-webkit-scrollbar { width: 3px; }
.ai-think-body::-webkit-scrollbar-thumb { background: transparent; border-radius: 2px; }
.ai-think-body:hover::-webkit-scrollbar-thumb { background: rgba(148, 163, 184, 0.28); }
.ai-think-body::-webkit-scrollbar-track { background: transparent; }
@keyframes ai-think-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
.ai-params { margin-top: 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; }
.ai-params-title { padding: 4px 8px; background: var(--c-muted); font-size: 11px; font-weight: 600; color: var(--c-secondary); }
.ai-params table { width: 100%; border-collapse: collapse; font-size: 11px; }
.ai-params th, .ai-params td { padding: 4px 8px; border-top: 1px solid var(--c-border); text-align: left; color: var(--c-fg); }
.ai-params th { background: var(--c-muted); color: var(--c-secondary); font-weight: 600; }
.ai-params-desc { color: var(--c-secondary); max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-quote-bar { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 12px; border-top: 1px solid var(--c-border); background: var(--c-muted); }
.ai-quote-label { font-size: 11px; color: var(--c-accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-quote-clear { border: 0; background: transparent; color: var(--c-secondary); font-size: 11px; cursor: pointer; }
.ai-quote-clear:hover { color: var(--c-danger); }
.ai-chat-input { flex-shrink: 0; display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--c-border); }
.ai-chat-input textarea { flex: 1; min-width: 0; resize: none; padding: 7px 9px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg); color: var(--c-fg); font-size: 12px; font-family: var(--font); outline: none; }
.ai-chat-input textarea:focus { border-color: var(--c-accent); }

.test-box { display: flex; flex-direction: column; gap: 10px; }
.test-cols { display: flex; gap: 12px; }
.test-cols .field { flex: 1; }
.test-result { margin: 0; padding: 10px 12px; border-radius: var(--radius-sm); font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; max-height: 200px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
.test-result.ok { background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.3); }
.test-result.err { background: rgba(220,38,38,0.08); color: var(--c-danger); border: 1px solid rgba(220,38,38,0.3); }

.derived-layout { display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; }
.derived-toolbar { display: flex; gap: 10px; align-items: center; }
.derived-toolbar .fn-search { flex: 0 0 auto; width: 220px; }
.hint { font-size: 12px; color: var(--c-secondary); padding: 4px 2px; }
.hint.pad { padding: 30px; text-align: center; font-size: 13px; }

.dp-table { border: 1px solid var(--c-border); border-radius: var(--radius); overflow: hidden; background: var(--c-panel); }
.dp-row { display: grid; grid-template-columns: 1.1fr 0.9fr 110px 60px 70px 1.6fr 60px 56px 190px; gap: 10px; align-items: center; padding: 10px 14px; border-bottom: 1px solid var(--c-border); font-size: 12px; color: var(--c-fg); }
.dp-onto { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-secondary); }
.dp-row:last-child { border-bottom: 0; }
.dp-head { background: var(--c-muted); font-size: 11px; font-weight: 700; color: var(--c-secondary); }
.dp-name { font-weight: 600; }
.dp-def { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-secondary); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--c-secondary); }
.dp-ops { display: flex; gap: 6px; justify-content: flex-end; }

.btn { padding: 7px 14px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; cursor: pointer; }
.btn:hover { background: var(--c-muted); }
.btn.sm { padding: 4px 10px; font-size: 11px; }
.primary-btn { padding: 7px 14px; border: 0; border-radius: var(--radius-sm); background: var(--c-btn-primary-bg); color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; }
.primary-btn:hover { background: var(--c-btn-primary-bg-hover); }
.primary-btn.sm { padding: 4px 10px; font-size: 11px; }
.primary-btn:disabled, .btn:disabled { opacity: 0.5; cursor: not-allowed; }
.danger-btn.sm { padding: 4px 10px; font-size: 11px; border: 1px solid rgba(220,38,38,0.35); border-radius: var(--radius-sm); background: transparent; color: var(--c-danger); cursor: pointer; }
.danger-btn.sm:hover { background: rgba(220,38,38,0.08); }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.modal-card { width: 720px; max-width: 92vw; max-height: 88vh; overflow-y: auto; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius); box-shadow: 0 20px 60px rgba(0,0,0,0.35); display: flex; flex-direction: column; }
.modal-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--c-border); }
.modal-head h3 { margin: 0; font-size: 15px; font-weight: 700; color: var(--c-fg); }
.close-btn { border: 0; background: transparent; color: var(--c-secondary); font-size: 18px; cursor: pointer; }
.modal-body { padding: 18px; }
.modal-foot { display: flex; justify-content: flex-end; gap: 10px; padding: 14px 18px; border-top: 1px solid var(--c-border); }
</style>
