<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { fetchEntities, deleteEntity, fetchKbs, fetchOntologyCategories, getOntologyCategoryDetail } from '../../api'
import SearchableSelect from '../common/SearchableSelect.vue'

const router = useRouter()
const entities = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const hasPrev = ref(false)
const hasNext = ref(false)
const loading = ref(false)

const search = ref('')
const kbId = ref('')
const kbs = ref([])

// 本体树
const ontologyTree = ref([]) // [{ category: {...}, ontologies: [...] }]
const selectedOntologyId = ref('')
const selectedCategoryId = ref('')
const treeSearch = ref('')
const expandedCats = ref(new Set())
const loadingTree = ref(false)

let searchTimer = null

const kbOptions = computed(() => [
  { value: '', label: '全部知识库', meta: '' },
  ...kbs.value.map(k => ({ value: k.id, label: k.name, meta: `${k.file_count || 0} 文件` })),
])

const totalPages = computed(() => Math.ceil(total.value / pageSize.value) || 1)

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

async function loadTree() {
  loadingTree.value = true
  try {
    const cats = await fetchOntologyCategories()
    const tree = []
    for (const cat of cats) {
      const detail = await getOntologyCategoryDetail(cat.id)
      tree.push({ category: { ...cat, entity_count: detail?.entity_count ?? 0 }, ontologies: detail?.ontologies || [] })
      expandedCats.value.add(cat.id)
    }
    ontologyTree.value = tree
  } catch {
    ontologyTree.value = []
  } finally {
    loadingTree.value = false
  }
}

function selectOntology(ontologyId) {
  if (selectedOntologyId.value === ontologyId) {
    selectedOntologyId.value = ''
  } else {
    selectedOntologyId.value = ontologyId
    selectedCategoryId.value = ''
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
  page.value = 1
  load()
}

function clearFilter() {
  selectedOntologyId.value = ''
  selectedCategoryId.value = ''
  page.value = 1
  load()
}

function toggleExpand(catId) {
  if (expandedCats.value.has(catId)) {
    expandedCats.value.delete(catId)
  } else {
    expandedCats.value.add(catId)
  }
}

async function load() {
  loading.value = true
  try {
    const res = await fetchEntities({
      kb_id: kbId.value,
      ontology_id: selectedOntologyId.value,
      q: search.value.trim(),
      page: page.value,
      page_size: pageSize.value,
    })
    entities.value = res.items || []
    total.value = res.total || 0
    hasPrev.value = !!res.has_prev
    hasNext.value = !!res.has_next
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

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  load()
}

function goDetail(entityId) {
  router.push('/entities/' + entityId)
}

async function remove(entity, e) {
  e && e.stopPropagation()
  if (!confirm(`确认删除实体「${entity.name}」？\n关联的关系实例将一并删除，Kùzu 图谱同步更新。`)) return
  try {
    await deleteEntity(entity.id)
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

function fmtProps(props) {
  if (!props) return '—'
  if (typeof props === 'string') {
    try { props = JSON.parse(props) } catch { return props }
  }
  const keys = Object.keys(props)
  if (!keys.length) return '—'
  return keys.slice(0, 3).map(k => `${k}: ${props[k]}`).join(' · ') + (keys.length > 3 ? ` ...+${keys.length - 3}` : '')
}

onMounted(async () => {
  try { kbs.value = await fetchKbs() } catch {}
  await loadTree()
  await load()
})
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">实体管理</h2>
        <span class="page-subtitle">知识抽取生成的实体实例，同步存于 SQLite 与 Kùzu</span>
      </div>
      <router-link to="/entities/relations" class="link-btn">关系实例 →</router-link>
    </div>

    <div class="split-layout">
      <!-- 左侧：本体树 -->
      <div class="tree-panel">
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
            >
              <button class="expand-btn" @click.stop="toggleExpand(g.category.id)">
                <svg :class="{ rotated: expandedCats.has(g.category.id) }" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
              </button>
              <span class="tree-cat-name">{{ g.category.name }}</span>
              <span class="tree-count">{{ g.category.entity_count || 0 }} 实体</span>
            </div>
            <div v-if="expandedCats.has(g.category.id)" class="tree-children">
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
        </div>

        <div v-if="filterLabel" class="filter-breadcrumb">
          <span class="filter-label">当前筛选：</span>
          <span class="filter-value">{{ filterLabel }}</span>
          <button class="filter-clear" @click="clearFilter">✕ 清除</button>
        </div>

        <div v-if="loading && !entities.length" class="loading-state"><span class="spinner"></span> 加载中...</div>

        <div v-else-if="entities.length" class="ent-table">
          <div class="ent-row ent-row-head">
            <span class="col-name">实体名称</span>
            <span class="col-type">本体类型</span>
            <span class="col-props">属性概要</span>
            <span class="col-actions"></span>
          </div>
          <div
            v-for="ent in entities"
            :key="ent.id"
            class="ent-row"
            @click="goDetail(ent.id)"
          >
            <span class="col-name">
              <span class="ent-dot" :style="{ background: 'var(--c-accent)' }"></span>
              {{ ent.name }}
            </span>
            <span class="col-type">
              <span class="type-tag">{{ ent.entity_type || ent.ontology_name || '—' }}</span>
            </span>
            <span class="col-props">{{ fmtProps(ent.properties) }}</span>
            <span class="col-actions">
              <button class="rm-btn sm" @click="remove(ent, $event)" title="删除">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </span>
          </div>
        </div>

        <div v-else class="empty-state">
          <div class="empty-icon">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>
          </div>
          <div class="empty-title">{{ search || kbId || selectedOntologyId || selectedCategoryId ? '没有匹配的实体' : '暂无实体' }}</div>
          <div class="empty-desc" v-if="!search && !kbId && !selectedOntologyId && !selectedCategoryId">处理文件并完成知识抽取后，实体将出现在这里</div>
        </div>

        <!-- 分页 -->
        <div v-if="total > 0" class="pager">
          <span class="pager-info">共 {{ total }} 条 · 第 {{ page }}/{{ totalPages }} 页</span>
          <div class="pager-btns">
            <button class="btn sm" :disabled="!hasPrev" @click="goPage(page - 1)">上一页</button>
            <button class="btn sm" :disabled="!hasNext" @click="goPage(page + 1)">下一页</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }
.link-btn { font-size: 13px; color: var(--c-accent); text-decoration: none; font-weight: 600; }
.link-btn:hover { text-decoration: underline; }

.split-layout { display: flex; gap: 16px; flex: 1; min-height: 0; }

/* 左侧本体树 */
.tree-panel { flex: 0 0 240px; display: flex; flex-direction: column; gap: 8px; border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 10px; overflow: hidden; }
.tree-toolbar { display: flex; align-items: center; gap: 8px; }
.tree-search { flex: 1; display: flex; align-items: center; gap: 6px; padding: 0 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); height: 32px; }
.tree-search:focus-within { border-color: var(--c-fg); }
.tree-search input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--c-fg); font-size: 12px; font-family: var(--font); }
.tree-search input::placeholder { color: var(--c-secondary); opacity: 0.7; }

.tree-all { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: var(--radius-sm); cursor: pointer; font-size: 13px; font-weight: 600; color: var(--c-secondary); transition: background 120ms; }
.tree-all:hover { background: var(--c-muted); }
.tree-all.active { background: var(--c-muted); color: var(--c-fg); }

.tree-scroll { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.tree-group { display: flex; flex-direction: column; }
.tree-cat { display: flex; align-items: center; gap: 6px; padding: 7px 8px; border-radius: var(--radius-sm); cursor: pointer; transition: background 120ms; }
.tree-cat:hover { background: var(--c-muted); }
.tree-cat.active { background: var(--c-muted); }
.expand-btn { display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border: 0; background: transparent; color: var(--c-secondary); cursor: pointer; flex-shrink: 0; }
.expand-btn svg { transition: transform 150ms; }
.expand-btn svg.rotated { transform: rotate(90deg); }
.tree-cat-name { flex: 1; min-width: 0; font-size: 13px; font-weight: 600; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-count { font-size: 11px; color: var(--c-secondary); flex-shrink: 0; }

.tree-children { padding-left: 16px; display: flex; flex-direction: column; gap: 1px; }
.tree-ont { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: var(--radius-sm); cursor: pointer; transition: background 120ms; }
.tree-ont:hover { background: var(--c-muted); }
.tree-ont.active { background: var(--c-muted); }
.tree-ont-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.tree-ont-name { font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-ont.active .tree-ont-name { color: var(--c-fg); font-weight: 600; }

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
.col-props { flex: 2; min-width: 0; font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
</style>
