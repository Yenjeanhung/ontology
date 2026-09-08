<script setup>
import { ref, computed, watch, onMounted, onActivated } from 'vue'
import { useRouter } from 'vue-router'
import { fetchEntities, deleteEntity, createEntity, fetchKbs, fetchOntologyCategories, getOntologyCategoryDetail, getOntologyDetail, fetchOntologyServices, batchInvokeService } from '../../api'
import SearchableSelect from '../common/SearchableSelect.vue'
import Pagination from '../common/Pagination.vue'

const router = useRouter()
const entities = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)

const search = ref('')
const kbId = ref('')
const kbs = ref([])

// 本体树（懒加载：初始只拉分类列表，展开某分类时才请求其明细与实体计数）
const ontologyTree = ref([]) // [{ category: {...}, ontologies: [...], detailLoaded, detailLoading }]
const selectedOntologyId = ref('')
const selectedCategoryId = ref('')
const treeSearch = ref('')
const expandedCats = ref(new Set())
const loadingTree = ref(false)

const TREE_EXPAND_KEY = 'entityList.expandedCats'
function loadExpandedCats() {
  try {
    const raw = localStorage.getItem(TREE_EXPAND_KEY)
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr)) return new Set(arr.filter((x) => typeof x === 'string'))
    }
  } catch {}
  return new Set()
}
function saveExpandedCats() {
  try { localStorage.setItem(TREE_EXPAND_KEY, JSON.stringify([...expandedCats.value])) } catch {}
}

let searchTimer = null
let treeSearchTimer = null

// 新增实体弹窗
const showCreate = ref(false)
const createForm = ref({
  kb_id: '',
  ontology_id: '',
  entity_type: '',
  name: '',
  description: '',
  properties: '{}',
})
const createError = ref('')
const createLoading = ref(false)
const ontologyOptions = ref([])

async function openCreate() {
  createError.value = ''
  createForm.value = {
    kb_id: '',
    ontology_id: selectedOntologyId.value || '',
    entity_type: '',
    name: '',
    description: '',
    properties: '{}'
  }
  // 懒加载模式下未展开的分类还没有本体明细，并行补齐后再聚合为下拉选项
  await Promise.all(
    ontologyTree.value
      .filter(g => !g.detailLoaded && !g.detailLoading)
      .map(g => ensureCategoryDetail(g))
  )
  // 从左侧已加载的本体树中聚合所有本体作为选项，
  // 避免仅选中「本体」节点时分类 ID 为空导致下拉为空。
  ontologyOptions.value = ontologyTree.value.flatMap(g => g.ontologies || [])
  showCreate.value = true
}

async function submitCreate() {
  createError.value = ''
  if (!createForm.value.ontology_id || !createForm.value.name || !createForm.value.entity_type) {
    createError.value = '请选择本体、填写实体类型和名称'
    return
  }
  let props = {}
  try {
    props = JSON.parse(createForm.value.properties || '{}')
  } catch {
    createError.value = '属性 JSON 格式不正确'
    return
  }
  createLoading.value = true
  try {
    await createEntity({
      kb_id: createForm.value.kb_id || '',
      ontology_id: createForm.value.ontology_id,
      entity_type: createForm.value.entity_type,
      name: createForm.value.name,
      description: createForm.value.description,
      properties: props,
    })
    showCreate.value = false
    page.value = 1
    await load()
  } catch (e) {
    createError.value = e.message || '创建失败'
  } finally {
    createLoading.value = false
  }
}

const kbOptions = computed(() => [
  { value: '', label: '全部知识库', meta: '' },
  ...kbs.value.map(k => ({ value: k.id, label: k.name, meta: `${k.file_count || 0} 文件` })),
])

// 当前筛选上下文面包屑
const filterLabel = computed(() => {
  if (selectedOntologyId.value) {
    const ont = ontologyTree.value
      .flatMap(g => g.ontologies)
      .find(o => o.id === selectedOntologyId.value)
    return ont ? ont.name : ''
  }
  if (selectedCategoryId.value) {
    const cat = ontologyTree.value.find(g => g.category.id === selectedCategoryId.value)
    return cat ? cat.category.name : ''
  }
  return ''
})

const filteredTree = computed(() => {
  const q = treeSearch.value.toLowerCase().trim()
  if (!q) return ontologyTree.value
  return ontologyTree.value
    .map(g => ({
      ...g,
      ontologies: g.ontologies.filter(o => o.name.toLowerCase().includes(q)),
    }))
    .filter(g => g.ontologies.length > 0 || g.category.name.toLowerCase().includes(q))
})

// 搜索时懒加载名称匹配的分类明细，保证未展开分类下的本体名也能被搜到
watch(treeSearch, (q) => {
  const kw = q.toLowerCase().trim()
  clearTimeout(treeSearchTimer)
  if (!kw) return
  treeSearchTimer = setTimeout(() => {
    for (const g of ontologyTree.value) {
      if (!g.detailLoaded && !g.detailLoading && g.category.name.toLowerCase().includes(kw)) {
        ensureCategoryDetail(g)
      }
    }
  }, 250)
})

async function loadTree() {
  loadingTree.value = true
  try {
    const cats = await fetchOntologyCategories()
    ontologyTree.value = cats.map(cat => ({
      // 分类实体总数由列表接口聚合返回，直接显示；展开后再懒加载各本体明细
      category: { ...cat, entity_count: cat.entity_count ?? null },
      ontologies: [],
      detailLoaded: false,
      detailLoading: false,
    }))
    // 恢复本地记忆的展开状态（跨详情页返回/刷新都保持）
    expandedCats.value = loadExpandedCats()
    // 如果有分类被恢复为展开态，补齐该分类的本体明细
    for (const catId of expandedCats.value) {
      const g = ontologyTree.value.find((x) => x.category.id === catId)
      if (g) ensureCategoryDetail(g)
    }
  } catch (e) {
    console.error('load ontology tree failed', e)
    ontologyTree.value = []
    expandedCats.value = new Set()
  } finally {
    loadingTree.value = false
  }
}

// 懒加载某分类的明细：实体总数 + 分类下各本体及其实体数（带缓存，只请求一次）
async function ensureCategoryDetail(g) {
  if (g.detailLoaded || g.detailLoading) return
  g.detailLoading = true
  try {
    const detail = await getOntologyCategoryDetail(g.category.id)
    g.category.entity_count = detail?.entity_count ?? 0
    g.ontologies = detail?.ontologies || []
    g.detailLoaded = true
  } catch (e) {
    console.error('load category detail failed', e)
  } finally {
    g.detailLoading = false
  }
}

// 选中具体本体时，加载其全部属性作为列表动态列
const ontAttributes = ref([]) // [{name, code, data_type}]

async function loadOntAttributes(ontologyId) {
  ontAttributes.value = []
  if (!ontologyId) return
  try {
    const g = ontologyTree.value.find(grp => (grp.ontologies || []).some(o => o.id === ontologyId))
    if (!g) return
    const detail = await getOntologyDetail(g.category.id, ontologyId)
    ontAttributes.value = (detail.attributes || [])
      .filter(a => a.code || a.name)
      .map(a => ({ name: a.name || a.code, code: a.code || a.name, data_type: a.data_type || '' }))
  } catch (e) {
    console.error('load ontology attributes failed', e)
  }
}

function formatAttr(v) {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

function selectOntology(ontologyId) {
  if (selectedOntologyId.value === ontologyId) {
    selectedOntologyId.value = ''
    ontAttributes.value = []
  } else {
    selectedOntologyId.value = ontologyId
    selectedCategoryId.value = ''
    loadOntAttributes(ontologyId)
  }
  page.value = 1
  load()
}

function selectCategory(categoryId) {
  if (selectedCategoryId.value === categoryId) {
    selectedCategoryId.value = ''
  } else {
    selectedCategoryId.value = categoryId
    selectedOntologyId.value = ''
  }
  ontAttributes.value = []
  page.value = 1
  load()
}

function clearFilter() {
  selectedOntologyId.value = ''
  selectedCategoryId.value = ''
  ontAttributes.value = []
  page.value = 1
  load()
}

function toggleExpand(catId) {
  if (expandedCats.value.has(catId)) {
    expandedCats.value.delete(catId)
  } else {
    expandedCats.value.add(catId)
    // 展开时才计算/加载该分类下各本体的实体数量
    const g = ontologyTree.value.find(x => x.category.id === catId)
    if (g) ensureCategoryDetail(g)
  }
  // 触发 Set 的响应式更新并持久化
  expandedCats.value = new Set(expandedCats.value)
  saveExpandedCats()
}

async function load() {
  loading.value = true
  try {
    const res = await fetchEntities({
      kb_id: kbId.value,
      ontology_id: selectedOntologyId.value,
      category_id: selectedCategoryId.value,
      q: search.value.trim(),
      page: page.value,
      page_size: pageSize.value,
    })
    entities.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    entities.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function onSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 300)
}

function onKbChange() {
  page.value = 1
  load()
}

function goDetail(entityId) {
  const from = router.currentRoute.value.fullPath
  router.push({ path: '/entities/' + entityId, query: { from } })
}

async function remove(entity, e) {
  e && e.stopPropagation()
  if (!confirm(`确认删除实体「${entity.name}」？\n关联的关系实例将一并删除，图谱同步更新。`)) return
  try {
    await deleteEntity(entity.id)
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

// ══ 批量执行动作（S4）══
const checkedIds = ref(new Set())
const showBatch = ref(false)
const batchCatId = ref('')
const batchOntId = ref('')
const batchServices = ref([])
const batchServiceId = ref('')
const batchParams = ref('{}')
const batchResult = ref(null)
const batchRunning = ref(false)
const batchError = ref('')

const checkedCount = computed(() => checkedIds.value.size)
const allChecked = computed(() =>
  entities.value.length > 0 && entities.value.every(e => checkedIds.value.has(e.id)))

function toggleCheck(id) {
  const next = new Set(checkedIds.value)
  next.has(id) ? next.delete(id) : next.add(id)
  checkedIds.value = next
}

function toggleCheckAll() {
  const next = new Set(checkedIds.value)
  if (allChecked.value) entities.value.forEach(e => next.delete(e.id))
  else entities.value.forEach(e => next.add(e.id))
  checkedIds.value = next
}

function clearChecked() { checkedIds.value = new Set() }

// 选中本体所属分类（批量弹窗默认值）
function findCategoryOfOntology(ontologyId) {
  const g = ontologyTree.value.find(grp => (grp.ontologies || []).some(o => o.id === ontologyId))
  return g?.category?.id || ''
}

function openBatch() {
  batchError.value = ''
  batchResult.value = null
  batchServiceId.value = ''
  batchServices.value = []
  batchParams.value = '{}'
  batchCatId.value = selectedCategoryId.value || findCategoryOfOntology(selectedOntologyId.value) || (ontologyTree.value[0]?.category?.id || '')
  batchOntId.value = selectedOntologyId.value || ''
  showBatch.value = true
  // 默认分类未加载明细时补齐本体列表
  const g = ontologyTree.value.find(x => x.category.id === batchCatId.value)
  if (g && !g.detailLoaded) ensureCategoryDetail(g).then(() => { if (batchOntId.value) loadBatchServices() })
  if (batchOntId.value) loadBatchServices()
}

async function loadBatchServices() {
  batchServices.value = []
  batchServiceId.value = ''
  if (!batchCatId.value || !batchOntId.value) return
  try {
    batchServices.value = await fetchOntologyServices(batchCatId.value, batchOntId.value)
  } catch (e) {
    batchError.value = '加载服务失败：' + e.message
  }
}

const batchSelService = computed(() =>
  batchServices.value.find(s => s.id === batchServiceId.value) || null)

const batchOntOptions = computed(() => {
  const g = ontologyTree.value.find(x => x.category.id === batchCatId.value)
  return g ? (g.ontologies || []) : []
})

async function runBatchInvoke() {
  const ids = [...checkedIds.value]
  if (!batchServiceId.value) { batchError.value = '请选择要执行的动作'; return }
  let params = {}
  try { params = JSON.parse(batchParams.value || '{}') }
  catch { batchError.value = '参数 JSON 格式不正确'; return }
  batchRunning.value = true
  batchError.value = ''
  batchResult.value = null
  try {
    batchResult.value = await batchInvokeService(batchServiceId.value, ids, params)
  } catch (e) {
    batchError.value = e.message || '批量执行失败'
  } finally {
    batchRunning.value = false
  }
}

onMounted(async () => {
  try { kbs.value = await fetchKbs() } catch {}
  await loadTree()
  // 支持从「影响分析」带 ?ontology_id= / ?category_id= 跳转过来时自动应用筛选
  const q = router.currentRoute.value.query
  if (q.ontology_id) {
    selectedOntologyId.value = String(q.ontology_id)
    loadOntAttributes(selectedOntologyId.value)
  } else if (q.category_id) {
    selectedCategoryId.value = String(q.category_id)
  }
  await load()
})

// keep-alive 重新激活（从详情页等返回）时，轻量刷新列表以反映编辑/删除/新增
// 首次激活与 onMounted 重合，需跳过以免重复加载
let firstActivate = true
onActivated(() => {
  if (firstActivate) {
    firstActivate = false
    return
  }
  load()
})

// ===== 客户端排序（属性/关系/服务三列可点击表头）=====
const sortKey = ref('') // '' | 'property_count' | 'relation_count' | 'service_count'
const sortDir = ref('desc') // 'desc' | 'asc'
function toggleSort(key) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'desc' ? 'asc' : 'desc'
  } else {
    sortKey.value = key
    sortDir.value = 'desc'
  }
}
const sortedEntities = computed(() => {
  if (!sortKey.value) return entities.value
  const key = sortKey.value
  const sign = sortDir.value === 'desc' ? -1 : 1
  return [...entities.value].sort((a, b) => {
    const av = a[key] ?? 0
    const bv = b[key] ?? 0
    if (av === bv) return 0
    return av < bv ? sign : -sign
  })
})
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">实体管理</h2>
        <span class="page-subtitle">知识抽取生成的实体实例</span>
      </div>
      <div class="page-actions">
        <button class="primary-btn" @click="openCreate">+ 新增实体</button>
        <router-link to="/entities/relations" class="link-btn">关系实例 →</router-link>
      </div>
    </div>

    <div class="split-layout">
      <!-- 左侧：本体树 -->
      <div class="tree-panel">
        <div class="tree-head">
          <div class="tree-title">本体分类</div>
          <div class="tree-hint">按本体类别 / 本体筛选实体</div>
        </div>
        <div class="tree-toolbar">
          <div class="tree-search">
            <svg class="search-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" v-model="treeSearch" placeholder="搜索本体...">
          </div>
        </div>

        <div class="tree-all" :class="{ active: !selectedOntologyId && !selectedCategoryId }" @click="clearFilter">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8.25" r="4.25"/><path d="M4.75 20.25a7.25 7.25 0 0 1 14.5 0"/></svg>
          <span>全部实体</span>
        </div>

        <div v-if="loadingTree" class="loading-sm"><span class="spinner"></span></div>

        <div class="tree-scroll" v-else>
          <div v-for="g in filteredTree" :key="g.category.id" class="tree-group">
            <div
              class="tree-cat"
              :class="{ active: selectedCategoryId === g.category.id }"
              @click="selectCategory(g.category.id)"
              @dblclick.stop="toggleExpand(g.category.id)"
              title="双击展开/收起"
            >
              <button class="expand-btn" @click.stop="toggleExpand(g.category.id)">
                <svg :class="{ rotated: expandedCats.has(g.category.id) }" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
              </button>
              <span class="tree-cat-name">{{ g.category.name }}</span>
              <span class="tree-count">{{ g.detailLoading ? '…' : (g.category.entity_count == null ? '' : g.category.entity_count + ' 实体') }}</span>
            </div>
            <div v-if="expandedCats.has(g.category.id)" class="tree-children">
              <div v-if="g.detailLoading" class="loading-sm"><span class="spinner"></span></div>
              <div v-else-if="!g.ontologies.length" class="tree-empty">暂无本体</div>
              <template v-else>
                <div
                  v-for="ont in g.ontologies"
                  :key="ont.id"
                  class="tree-ont"
                  :class="{ active: selectedOntologyId === ont.id }"
                  @click="selectOntology(ont.id)"
                >
                  <span class="tree-ont-dot" :style="{ background: ont.color || 'var(--c-accent)' }"></span>
                  <span class="tree-ont-name">{{ ont.name }}<span v-if="ont.entity_count !== undefined">（{{ ont.entity_count }}）</span></span>
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：实体列表 -->
      <div class="list-panel">
        <div class="toolbar">
          <div class="search-wrap">
            <svg class="search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" v-model="search" placeholder="搜索实体名称..." @input="onSearch">
          </div>
          <div class="kb-filter">
            <SearchableSelect
              v-model="kbId"
              :options="kbOptions"
              :searchable="true"
              placeholder="筛选知识库"
              @change="onKbChange"
            />
          </div>
          <button class="icon-btn refresh-btn" @click="load" title="刷新">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
          </button>
          <button v-if="checkedCount" class="batch-btn" @click="openBatch">⚡ 批量执行动作（{{ checkedCount }}）</button>
          <button v-if="checkedCount" class="filter-clear" @click="clearChecked" title="清除勾选">✕</button>
        </div>

        <div v-if="filterLabel" class="filter-breadcrumb">
          <span class="filter-label">当前筛选：</span>
          <span class="filter-value">{{ filterLabel }}</span>
          <button class="filter-clear" @click="clearFilter">✕ 清除</button>
        </div>

        <div v-if="loading && !entities.length" class="loading-state"><span class="spinner"></span> 加载中...</div>

        <div v-else-if="sortedEntities.length" class="ent-table">
          <!-- 选中具体本体：表头展示该本体全部属性列 -->
          <template v-if="ontAttributes.length">
            <div class="ent-row ent-row-head">
              <span class="col-check"><input type="checkbox" :checked="allChecked" @change="toggleCheckAll" @click.stop></span>
              <span class="col-name">实体名称</span>
              <span class="col-type">本体类型</span>
              <span v-for="a in ontAttributes" :key="a.code" class="col-attr head" :title="a.name + (a.data_type ? ` · ${a.data_type}` : '')">{{ a.name }}</span>
              <span class="col-metric-head">
                <button :class="['metric-head-btn','metric-attr',{active:sortKey==='property_count'}]" @click="toggleSort('property_count')" title="按属性数排序">属性<span class="arr" v-if="sortKey==='property_count'">{{ sortDir==='desc'?'↓':'↑' }}</span></button>
                <button :class="['metric-head-btn','metric-rel',{active:sortKey==='relation_count'}]" @click="toggleSort('relation_count')" title="按关系数排序">关系<span class="arr" v-if="sortKey==='relation_count'">{{ sortDir==='desc'?'↓':'↑' }}</span></button>
                <button :class="['metric-head-btn','metric-svc',{active:sortKey==='service_count'}]" @click="toggleSort('service_count')" title="按服务数排序">服务<span class="arr" v-if="sortKey==='service_count'">{{ sortDir==='desc'?'↓':'↑' }}</span></button>
              </span>
              <span class="col-actions"></span>
            </div>
            <div
              v-for="ent in sortedEntities"
              :key="ent.id"
              class="ent-row"
              @click="goDetail(ent.id)"
            >
              <span class="col-check" @click.stop><input type="checkbox" :checked="checkedIds.has(ent.id)" @change="toggleCheck(ent.id)"></span>
              <span class="col-name">
                <span class="ent-dot" :style="{ background: 'var(--c-accent)' }"></span>
                {{ ent.name }}
              </span>
              <span class="col-type">
                <span class="type-tag">{{ ent.entity_type || ent.ontology_name || '—' }}</span>
              </span>
              <span v-for="a in ontAttributes" :key="a.code" class="col-attr" :title="formatAttr(ent.properties?.[a.code])">{{ formatAttr(ent.properties?.[a.code]) }}</span>
              <span class="col-metric">
                <button :class="['metric-pill','metric-attr',{active:sortKey==='property_count'}]" @click.stop="toggleSort('property_count')" title="按属性数排序">
                  <span class="metric-pill-num">{{ ent.property_count ?? 0 }}</span><span class="metric-pill-label">属性</span>
                </button>
                <button :class="['metric-pill','metric-rel',{active:sortKey==='relation_count'}]" @click.stop="toggleSort('relation_count')" title="按关系数排序">
                  <span class="metric-pill-num">{{ ent.relation_count ?? 0 }}</span><span class="metric-pill-label">关系</span>
                </button>
                <button :class="['metric-pill','metric-svc',{active:sortKey==='service_count'}]" @click.stop="toggleSort('service_count')" title="按服务数排序">
                  <span class="metric-pill-num">{{ ent.service_count ?? 0 }}</span><span class="metric-pill-label">服务</span>
                </button>
              </span>
              <span class="col-actions">
                <button class="rm-btn sm" @click="remove(ent, $event)" title="删除">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </span>
            </div>
          </template>

          <!-- 分类/全部：保持原有概要列 -->
          <template v-else>
            <div class="ent-row ent-row-head">
              <span class="col-check"><input type="checkbox" :checked="allChecked" @change="toggleCheckAll" @click.stop></span>
              <span class="col-name">实体名称</span>
              <span class="col-type">本体类型</span>
              <span class="col-metric-head">
                <button :class="['metric-head-btn','metric-attr',{active:sortKey==='property_count'}]" @click="toggleSort('property_count')" title="按属性数排序">属性<span class="arr" v-if="sortKey==='property_count'">{{ sortDir==='desc'?'↓':'↑' }}</span></button>
                <button :class="['metric-head-btn','metric-rel',{active:sortKey==='relation_count'}]" @click="toggleSort('relation_count')" title="按关系数排序">关系<span class="arr" v-if="sortKey==='relation_count'">{{ sortDir==='desc'?'↓':'↑' }}</span></button>
                <button :class="['metric-head-btn','metric-svc',{active:sortKey==='service_count'}]" @click="toggleSort('service_count')" title="按服务数排序">服务<span class="arr" v-if="sortKey==='service_count'">{{ sortDir==='desc'?'↓':'↑' }}</span></button>
              </span>
              <span class="col-props">属性概要</span>
              <span class="col-actions"></span>
            </div>
            <div
              v-for="ent in sortedEntities"
              :key="ent.id"
              class="ent-row"
              @click="goDetail(ent.id)"
            >
              <span class="col-check" @click.stop><input type="checkbox" :checked="checkedIds.has(ent.id)" @change="toggleCheck(ent.id)"></span>
              <span class="col-name">
                <span class="ent-dot" :style="{ background: 'var(--c-accent)' }"></span>
                {{ ent.name }}
              </span>
              <span class="col-type">
                <span class="type-tag">{{ ent.entity_type || ent.ontology_name || '—' }}</span>
              </span>
              <span class="col-metric">
                <button :class="['metric-pill','metric-attr',{active:sortKey==='property_count'}]" @click.stop="toggleSort('property_count')" title="按属性数排序">
                  <span class="metric-pill-num">{{ ent.property_count ?? 0 }}</span><span class="metric-pill-label">属性</span>
                </button>
                <button :class="['metric-pill','metric-rel',{active:sortKey==='relation_count'}]" @click.stop="toggleSort('relation_count')" title="按关系数排序">
                  <span class="metric-pill-num">{{ ent.relation_count ?? 0 }}</span><span class="metric-pill-label">关系</span>
                </button>
                <button :class="['metric-pill','metric-svc',{active:sortKey==='service_count'}]" @click.stop="toggleSort('service_count')" title="按服务数排序">
                  <span class="metric-pill-num">{{ ent.service_count ?? 0 }}</span><span class="metric-pill-label">服务</span>
                </button>
              </span>
              <span class="col-props">{{ ent.property_preview || '—' }}</span>
              <span class="col-actions">
                <button class="rm-btn sm" @click="remove(ent, $event)" title="删除">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </span>
            </div>
          </template>
        </div>

        <div v-else class="empty-state">
          <div class="empty-icon">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>
          </div>
          <div class="empty-title">{{ search || kbId || selectedOntologyId || selectedCategoryId ? '没有匹配的实体' : '暂无实体' }}</div>
          <div class="empty-desc" v-if="!search && !kbId && !selectedOntologyId && !selectedCategoryId">处理文件并完成知识抽取后，实体将出现在这里</div>
          <button class="primary-btn mt" @click="openCreate">+ 新增实体</button>
        </div>

    <!-- 新增实体弹窗 -->
    <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
      <div class="modal-card">
        <div class="modal-head">
          <h3>新增实体</h3>
          <button class="close-btn" @click="showCreate = false">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-row">
            <label>所属本体</label>
            <select v-model="createForm.ontology_id">
              <option value="">请选择本体</option>
              <option v-for="o in ontologyOptions" :key="o.id" :value="o.id">{{ o.name }}</option>
            </select>
          </div>
          <div class="form-row">
            <label>实体类型</label>
            <input type="text" v-model="createForm.entity_type" placeholder="如 AircraftModel">
          </div>
          <div class="form-row">
            <label>实体名称</label>
            <input type="text" v-model="createForm.name" placeholder="唯一标识名称">
          </div>
          <div class="form-row">
            <label>描述</label>
            <input type="text" v-model="createForm.description" placeholder="可选">
          </div>
          <div class="form-row">
            <label>属性 JSON</label>
            <textarea v-model="createForm.properties" rows="4" placeholder='{"key":"value"}'></textarea>
          </div>
          <div v-if="createError" class="form-error">{{ createError }}</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showCreate = false">取消</button>
          <button class="primary-btn" :disabled="createLoading" @click="submitCreate">{{ createLoading ? '创建中...' : '创建' }}</button>
        </div>
      </div>
    </div>

    <!-- 批量执行动作弹窗（S4） -->
    <div v-if="showBatch" class="modal-overlay" @click.self="showBatch = false">
      <div class="modal-card batch-card">
        <div class="modal-head">
          <h3>批量执行动作（已选 {{ checkedCount }} 个实体）</h3>
          <button class="close-btn" @click="showBatch = false">✕</button>
        </div>
        <div class="modal-body">
          <div class="form-row two">
            <label>分类</label>
            <select v-model="batchCatId">
              <option value="">请选择</option>
              <option v-for="g in ontologyTree" :key="g.category.id" :value="g.category.id">{{ g.category.name }}</option>
            </select>
          </div>
          <div class="form-row two">
            <label>本体</label>
            <select v-model="batchOntId" @change="loadBatchServices">
              <option value="">请选择</option>
              <option v-for="o in batchOntOptions" :key="o.id" :value="o.id">{{ o.name }}</option>
            </select>
          </div>
          <div class="form-row two">
            <label>动作（服务）</label>
            <select v-model="batchServiceId">
              <option value="">请选择</option>
              <option v-for="s in batchServices" :key="s.id" :value="s.id">{{ s.name }}（{{ s.code }}）</option>
            </select>
          </div>
          <div v-if="batchSelService?.params?.length" class="batch-params-hint">
            参数：{{ batchSelService.params.map(p => `${p.name}${p.required ? '*' : ''}`).join('、') }}
          </div>
          <div class="form-row">
            <label>公共参数（JSON，对所有实体相同）</label>
            <textarea v-model="batchParams" rows="4" spellcheck="false" placeholder='{"reason": "批量处理"}'></textarea>
          </div>
          <div v-if="batchError" class="form-error">{{ batchError }}</div>
          <div v-if="batchResult" class="batch-result">
            <div class="batch-result-head">
              成功 {{ batchResult.succeeded || 0 }} · 失败 {{ batchResult.failed || 0 }}
            </div>
            <pre>{{ JSON.stringify(batchResult.items || [], null, 2) }}</pre>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showBatch = false">关闭</button>
          <button class="primary-btn" :disabled="batchRunning || !batchServiceId" @click="runBatchInvoke">{{ batchRunning ? '执行中...' : '▶ 批量执行' }}</button>
        </div>
      </div>
    </div>

        <!-- 分页 -->
        <Pagination v-if="total > 0" v-model:page="page" v-model:page-size="pageSize" :total="total" @change="load" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; height: 100%; }

/* 勾选列与批量按钮 */
.col-check { flex: 0 0 34px; display: flex; align-items: center; justify-content: center; }
.col-check input[type="checkbox"] { width: 14px; height: 14px; accent-color: var(--c-accent); cursor: pointer; }
.batch-btn { flex: 0 0 auto; padding: 7px 14px; border: 1px solid var(--c-accent); border-radius: var(--radius-sm); background: rgba(59,130,246,0.08); color: var(--c-accent); font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap; }
.batch-btn:hover { background: rgba(59,130,246,0.16); }
.batch-card { width: 640px; }
.batch-params-hint { font-size: 11px; color: var(--c-secondary); background: var(--c-muted); border-radius: var(--radius-sm); padding: 6px 10px; }
.batch-result { border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; }
.batch-result-head { padding: 8px 12px; font-size: 12px; font-weight: 700; color: var(--c-fg); background: var(--c-muted); border-bottom: 1px solid var(--c-border); }
.batch-result pre { margin: 0; padding: 10px 12px; font-size: 11px; max-height: 220px; overflow: auto; font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; word-break: break-all; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }
.link-btn { font-size: 13px; color: var(--c-accent); text-decoration: none; font-weight: 600; }
.link-btn:hover { text-decoration: underline; }

.split-layout { display: flex; gap: 16px; flex: 1; min-height: 0; }

/* 左侧本体树 */
.tree-panel { flex: 0 0 240px; display: flex; flex-direction: column; gap: 8px; border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 10px; overflow: hidden; }
.tree-head { display: flex; flex-direction: column; gap: 1px; padding: 0 2px 4px; }
.tree-title { font-size: 13px; font-weight: 700; color: var(--c-fg); }
.tree-hint { font-size: 11px; color: var(--c-secondary); }
.tree-toolbar { display: flex; align-items: center; gap: 8px; }
.tree-search { flex: 1; display: flex; align-items: center; gap: 6px; padding: 0 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); height: 32px; }
.tree-search:focus-within { border-color: var(--c-fg); }
.tree-search input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--c-fg); font-size: 12px; font-family: var(--font); }
.tree-search input::placeholder { color: var(--c-secondary); opacity: 0.7; }

.tree-all { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: var(--radius-sm); cursor: pointer; font-size: 13px; font-weight: 600; color: var(--c-secondary); transition: background 120ms; }
.tree-all:hover { background: var(--c-muted-hover); }
.tree-all.active { background: color-mix(in srgb, var(--c-accent) 18%, transparent); color: var(--c-accent); box-shadow: inset 2px 0 0 var(--c-accent); }

.tree-scroll { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.tree-group { display: flex; flex-direction: column; }
.tree-cat { display: flex; align-items: center; gap: 6px; padding: 7px 8px; border-radius: var(--radius-sm); cursor: pointer; transition: background 120ms; }
.tree-cat:hover { background: var(--c-muted-hover); }
.tree-cat.active { background: color-mix(in srgb, var(--c-accent) 16%, transparent); box-shadow: inset 2px 0 0 var(--c-accent); }
.tree-cat.active .tree-cat-name { color: var(--c-accent); }
.expand-btn { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border: 0; background: transparent; color: var(--c-secondary); cursor: pointer; flex-shrink: 0; }
.expand-btn svg { transition: transform 150ms; }
.expand-btn svg.rotated { transform: rotate(90deg); }
.tree-cat-name { flex: 1; min-width: 0; font-size: 13px; font-weight: 600; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-count { font-size: 11px; color: var(--c-secondary); flex-shrink: 0; }

.tree-children { padding-left: 16px; display: flex; flex-direction: column; gap: 1px; }
.tree-ont { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: var(--radius-sm); cursor: pointer; transition: background 120ms; }
.tree-ont:hover { background: var(--c-muted-hover); }
.tree-ont.active { background: color-mix(in srgb, var(--c-accent) 16%, transparent); box-shadow: inset 2px 0 0 var(--c-accent); }
.tree-ont-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.tree-ont-name { font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-ont.active .tree-ont-name { color: var(--c-accent); font-weight: 600; }
.tree-empty { padding: 6px 10px; font-size: 12px; color: var(--c-secondary); }

.loading-sm { padding: 20px; text-align: center; }

/* 右侧列表 */
.list-panel { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }

.toolbar { display: flex; align-items: center; gap: 10px; }
.search-wrap { flex: 1; display: flex; align-items: center; gap: 8px; padding: 0 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); height: 38px; }
.search-wrap:focus-within { border-color: var(--c-fg); }
.search-icon { color: var(--c-secondary); flex-shrink: 0; }
.search-wrap input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--c-fg); font-size: 14px; font-family: var(--font); }
.search-wrap input::placeholder { color: var(--c-secondary); opacity: 0.7; }
.kb-filter { width: 200px; flex-shrink: 0; }
.icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 38px; height: 38px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; transition: background 150ms, color 150ms; flex-shrink: 0; }
.icon-btn:hover { background: var(--c-muted); color: var(--c-fg); }

.filter-breadcrumb { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border-radius: var(--radius-sm); background: var(--c-muted); font-size: 13px; }
.filter-label { color: var(--c-secondary); }
.filter-value { font-weight: 600; color: var(--c-fg); }
.filter-clear { border: 0; background: transparent; color: var(--c-secondary); cursor: pointer; font-size: 12px; margin-left: auto; }
.filter-clear:hover { color: var(--c-danger); }

.ent-table { border: 1px solid var(--c-border); border-radius: var(--radius); overflow: hidden; background: var(--c-panel); }
.ent-row { display: flex; align-items: center; gap: 12px; padding: 11px 16px; border-bottom: 1px solid var(--c-border); cursor: pointer; transition: background 120ms; }
.ent-row:last-child { border-bottom: 0; }
.ent-row:hover { background: var(--c-muted); }
.ent-row-head { background: var(--c-muted); cursor: default; font-size: 12px; font-weight: 600; color: var(--c-secondary); text-transform: uppercase; letter-spacing: 0.3px; }
.ent-row-head:hover { background: var(--c-muted); }
.col-name { flex: 1.5; min-width: 0; display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.col-type { flex: 0 0 130px; min-width: 0; }
.col-num { flex: 0 0 56px; min-width: 0; text-align: center; font-size: 12px; color: var(--c-secondary); font-variant-numeric: tabular-nums; }
.col-props { flex: 2; min-width: 0; font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.col-attr { flex: 1 1 0; min-width: 0; font-size: 12px; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.col-attr.head { color: var(--c-secondary); font-weight: 600; text-transform: none; letter-spacing: 0; }

/* 属性 / 关系 / 服务 三列：metric pill 风格 + 点击排序 */
.col-metric, .col-metric-head { flex: 0 0 220px; min-width: 0; display: flex; align-items: center; gap: 6px; }
.col-metric-head { justify-content: flex-start; }
.metric-head-btn, .metric-pill {
  display: inline-flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 1px; min-width: 56px; padding: 4px 8px;
  border: 1px solid transparent; border-radius: 8px;
  background: transparent; cursor: pointer; transition: all 150ms;
  font-family: var(--font);
}
.metric-head-btn { padding: 5px 10px; border-color: var(--c-border); background: var(--c-panel); color: var(--c-secondary); font-size: 12px; font-weight: 600; }
.metric-head-btn:hover { color: var(--c-fg); border-color: var(--c-fg); }
.metric-head-btn .arr { margin-left: 3px; font-size: 10px; opacity: 0.8; }

.metric-pill { padding: 5px 9px; }
.metric-pill-num { font-size: 14px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.metric-pill-label { font-size: 10px; line-height: 1; }

.metric-attr { color: #16a34a; background: rgba(22, 163, 74, 0.08); }
.metric-attr:hover { background: rgba(22, 163, 74, 0.18); }
.metric-attr.active { background: rgba(22, 163, 74, 0.22); box-shadow: 0 0 0 2px rgba(22, 163, 74, 0.45); }

.metric-rel { color: #2563eb; background: rgba(37, 99, 235, 0.08); }
.metric-rel:hover { background: rgba(37, 99, 235, 0.18); }
.metric-rel.active { background: rgba(37, 99, 235, 0.22); box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.45); }

.metric-svc { color: #9333ea; background: rgba(147, 51, 234, 0.08); }
.metric-svc:hover { background: rgba(147, 51, 234, 0.18); }
.metric-svc.active { background: rgba(147, 51, 234, 0.22); box-shadow: 0 0 0 2px rgba(147, 51, 234, 0.45); }

.metric-head-btn.metric-attr.active, .metric-head-btn.metric-attr:hover { color: #16a34a; }
.metric-head-btn.metric-rel.active, .metric-head-btn.metric-rel:hover { color: #2563eb; }
.metric-head-btn.metric-svc.active, .metric-head-btn.metric-svc:hover { color: #9333ea; }
.col-actions { flex: 0 0 40px; display: flex; justify-content: flex-end; }
.ent-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.type-tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); }
.rm-btn.sm { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; }
.rm-btn.sm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }

.loading-state { padding: 40px; text-align: center; color: var(--c-secondary); }
.empty-state { text-align: center; padding: 48px 20px; color: var(--c-secondary); }
.empty-state .empty-icon { margin-bottom: 12px; color: var(--c-border); }
.empty-state .empty-title { font-size: 15px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.empty-state .empty-desc { font-size: 13px; }

.pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pager-info { font-size: 12px; color: var(--c-secondary); }
.pager-btns { display: flex; gap: 8px; }
.btn.sm { padding: 5px 12px; font-size: 12px; }
.btn.sm:disabled { opacity: 0.4; cursor: not-allowed; }

.page-actions { display: flex; align-items: center; gap: 12px; }
.primary-btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border: 0; border-radius: var(--radius-sm); background: var(--c-accent); color: #fff; font-size: 13px; font-weight: 600; cursor: pointer; transition: filter 150ms; }
.primary-btn:hover { filter: brightness(1.1); }
.primary-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.primary-btn.mt { margin-top: 16px; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.modal-card { width: 520px; max-width: 92vw; max-height: 88vh; overflow-y: auto; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius); box-shadow: 0 20px 60px rgba(0,0,0,0.35); display: flex; flex-direction: column; }
.modal-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--c-border); }
.modal-head h3 { margin: 0; font-size: 15px; font-weight: 700; color: var(--c-fg); }
.close-btn { border: 0; background: transparent; color: var(--c-secondary); font-size: 18px; cursor: pointer; }
.modal-body { padding: 18px; display: flex; flex-direction: column; gap: 14px; }
.modal-foot { display: flex; justify-content: flex-end; gap: 10px; padding: 14px 18px; border-top: 1px solid var(--c-border); }
.form-row { display: flex; flex-direction: column; gap: 5px; }
.form-row label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.form-row input, .form-row select, .form-row textarea { padding: 8px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-bg); color: var(--c-fg); font-size: 13px; font-family: var(--font); outline: none; }
.form-row input:focus, .form-row select:focus, .form-row textarea:focus { border-color: var(--c-accent); }
.form-row textarea { resize: vertical; }
.form-error { color: var(--c-danger); font-size: 12px; padding: 4px 0; }
</style>
