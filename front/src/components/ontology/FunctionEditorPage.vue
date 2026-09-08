<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import {
  fetchOntologyCategories, getOntologyCategoryDetail,
  fetchOntologyFunctions, createOntologyFunction, updateOntologyFunction, deleteOntologyFunction, testOntologyFunction,
  fetchAllDerivedProperties, createDerivedProperty, updateDerivedProperty, deleteDerivedProperty, materializeDerivedProperty,
  fetchEntities, testDerivedProperty, getOntologyFunction,
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
const tab = ref('functions')

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
    is_deterministic: true, cache_seconds: 300, timeout_seconds: 30, is_enabled: true,
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
    is_deterministic: fn.is_deterministic !== false,
    cache_seconds: fn.cache_seconds ?? 300,
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
    is_deterministic: !!f.is_deterministic,
    cache_seconds: Number(f.cache_seconds) || 0,
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

// ── 派生属性管理（进入即加载全部，本体作为筛选条件）──
const allDerived = ref([])
const loadingDerived = ref(false)
const showDerivedModal = ref(false)
const derivedForm = ref(emptyDerivedForm())
const derivedSaving = ref(false)
const materializingId = ref('')

const derived = computed(() => {
  if (!ontologyId.value) return allDerived.value
  return allDerived.value.filter((d) => d.ontology_id === ontologyId.value)
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
    materialize_mode: 'virtual', is_enabled: true,
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

function openDerivedNew() {
  derivedForm.value = emptyDerivedForm()
  showDerivedModal.value = true
}

function openDerivedEdit(dp) {
  derivedForm.value = {
    id: dp.id, name: dp.name, code: dp.code, data_type: dp.data_type || 'number',
    source_kind: dp.source_kind || 'function', function_id: dp.function_id || '',
    graph_metric: dp.graph_metric || 'degree',
    materialize_mode: dp.materialize_mode === 'materialized' ? 'materialized' : 'virtual',
    is_enabled: dp.is_enabled !== false,
  }
  showDerivedModal.value = true
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

// 关联函数的参数模式（跨本体时列表里可能没有，回退到按 id 拉详情）
async function loadDpFnSchema(dp) {
  const t = dpTest.value
  if (dp.source_kind !== 'function' || !dp.function_id) { t.schema = []; return }
  const local = functions.value.find((f) => f.id === dp.function_id)
  if (local) { t.schema = (local.params_schema || []).filter((p) => p.name); return }
  try {
    const fn = await getOntologyFunction(dp.function_id)
    t.schema = (fn?.params_schema || []).filter((p) => p.name)
  } catch {
    t.schema = []
  }
}

// 按 params_schema 的类型归一化测试入参
function normalizeDpTestParams() {
  const t = dpTest.value
  const out = {}
  for (const p of t.schema) {
    const raw = t.params?.[p.name]
    if (raw === undefined || raw === '') {
      if (p.required) out[p.name] = p.type === 'number' ? 0 : p.type === 'boolean' ? false : ''
      continue
    }
    if (p.type === 'number') {
      const n = Number(raw)
      out[p.name] = Number.isFinite(n) ? n : raw
    } else if (p.type === 'boolean') {
      out[p.name] = raw === true || raw === 'true'
    } else if (p.type === 'object') {
      try { out[p.name] = typeof raw === 'string' ? JSON.parse(raw) : raw } catch { out[p.name] = raw }
    } else {
      out[p.name] = raw
    }
  }
  return out
}

async function openDpTest(dp) {
  dpTest.value = emptyDpTest()
  dpTest.value.dp = dp
  dpTest.value.params = { ...(dp.params || {}) }
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
    t.result = await testDerivedProperty(t.dp.id, t.entityId, t.write, normalizeDpTestParams())
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
        <span class="page-subtitle">S3 · 只读函数与派生属性管理（确定性分级 / 缓存 / 物化）</span>
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
              <span class="tag">{{ f.is_deterministic === false ? '不缓存' : '缓存' }}</span>
              <span v-if="f.cache_seconds" class="tag soft">缓存 {{ f.cache_seconds }}s</span>
              <span v-if="f.is_enabled === false" class="tag off">停用</span>
            </div>
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
          <label class="field"><span>缓存</span>
            <select v-model="fnForm.is_deterministic">
              <option :value="true">缓存</option>
              <option :value="false">不缓存</option>
            </select>
          </label>
          <label class="field"><span>缓存(秒)</span><input v-model.number="fnForm.cache_seconds" type="number" min="0"></label>
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

        <div class="sec-sub">函数代码（沙箱执行，仅只读上下文）</div>
        <div class="code-wrap">
          <PythonEditor v-model="fnForm.code_text" />
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
            <div class="sec-sub">测试结果
              <span v-if="fnTestResult.success" class="cache-badge" :class="fnTestResult.cached ? 'hit' : 'fresh'">
                {{ fnTestResult.cached ? '⚡ 缓存命中（TTL 内重复调用）' : '✓ 实时计算（缓存未命中或已过期）' }}
              </span>
            </div>
            <pre class="test-result" :class="{ ok: fnTestResult.success, err: !fnTestResult.success }">{{ JSON.stringify(fnTestResult, null, 2) }}</pre>
          </template>
        </div>
      </div>
    </div>

    <!-- ══ 派生属性管理 ══ -->
    <div v-else class="derived-layout">
      <div class="derived-toolbar">
        <select v-model="ontologyId" class="ctrl-select">
          <option value="">全部本体</option>
          <option v-for="o in ontologyOptions" :key="o.id" :value="o.id">{{ o.name }}</option>
        </select>
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
    <div v-if="showDerivedModal" class="modal-overlay" @click.self="showDerivedModal = false">
      <div class="modal-card">
        <div class="modal-head"><h3>{{ derivedForm.id ? '编辑' : '新建' }}派生属性</h3><button class="close-btn" @click="showDerivedModal = false">✕</button></div>
        <div class="modal-body">
          <div class="form-grid">
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
              <select v-model="derivedForm.function_id">
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
          <div class="hint" style="margin-top: 10px;">函数入参在"测试"弹窗中动态填写，测试成功后会自动固化为该派生属性的运行参数。</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showDerivedModal = false">取消</button>
          <button class="primary-btn" :disabled="derivedSaving" @click="saveDerived">{{ derivedSaving ? '保存中...' : '保存' }}</button>
        </div>
      </div>
    </div>

    <!-- 派生属性测试弹窗 -->
    <div v-if="showDpTestModal" class="modal-overlay" @click.self="showDpTestModal = false">
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
            <div class="sec-sub" style="margin-top: 12px;">函数入参（动态填写，测试成功后固化为运行参数）</div>
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
.fn-item { padding: 9px 11px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); cursor: pointer; transition: all 120ms; }
.fn-item:hover { border-color: var(--c-accent); }
.fn-item.active { border-color: var(--c-accent); background: var(--c-muted); }
.fn-item-name { font-size: 13px; font-weight: 600; color: var(--c-fg); }
.fn-item-meta { display: flex; gap: 6px; margin-top: 5px; flex-wrap: wrap; }
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

.code-wrap { border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; height: 260px; }

.test-box { display: flex; flex-direction: column; gap: 10px; }
.test-cols { display: flex; gap: 12px; }
.test-cols .field { flex: 1; }
.test-result { margin: 0; padding: 10px 12px; border-radius: var(--radius-sm); font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; max-height: 200px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
.test-result.ok { background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.3); }
.test-result.err { background: rgba(220,38,38,0.08); color: var(--c-danger); border: 1px solid rgba(220,38,38,0.3); }
.cache-badge { margin-left: 10px; font-size: 11px; font-weight: normal; padding: 1px 8px; border-radius: 999px; vertical-align: middle; }
.cache-badge.hit { background: rgba(234,179,8,0.15); color: #eab308; border: 1px solid rgba(234,179,8,0.4); }
.cache-badge.fresh { background: rgba(34,197,94,0.12); color: #22c55e; border: 1px solid rgba(34,197,94,0.4); }

.derived-layout { display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; }
.derived-toolbar { display: flex; gap: 10px; align-items: center; }
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
.primary-btn { padding: 7px 14px; border: 0; border-radius: var(--radius-sm); background: var(--c-accent); color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; }
.primary-btn:hover { filter: brightness(1.1); }
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
