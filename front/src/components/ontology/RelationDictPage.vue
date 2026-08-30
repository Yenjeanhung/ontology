<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { fetchOntologyCategories, getOntologyCategoryDetail } from '../../api'
import RelationDictEditor from './RelationDictEditor.vue'
import SearchableSelect from '../common/SearchableSelect.vue'
import ExcelImportExport from './ExcelImportExport.vue'

const categories = ref([])
const selectedCategoryId = ref('')
const detail = ref(null)
const loading = ref(false)

const categoryOptions = computed(() => [
  { value: '', label: '全部类别', meta: '' },
  ...categories.value.map(c => ({ value: c.id, label: c.name, meta: `${c.ontology_count} 个本体` })),
])

async function loadCategories() {
  try {
    categories.value = await fetchOntologyCategories()
    if (categories.value.length) {
      selectedCategoryId.value = categories.value[0].id
    }
  } catch {
    categories.value = []
  }
}

async function loadDetail() {
  if (!selectedCategoryId.value) {
    detail.value = null
    return
  }
  loading.value = true
  try {
    detail.value = await getOntologyCategoryDetail(selectedCategoryId.value)
  } catch {
    detail.value = null
  } finally {
    loading.value = false
  }
}

function onCategoryChange() {
  loadDetail()
}

function onSubChanged() {
  loadDetail()
}

watch(selectedCategoryId, loadDetail)
onMounted(loadCategories)
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">关系字典</h2>
        <span class="page-subtitle">维护关系类型词汇库（仅名称与描述），为三元组约束提供可选关系</span>
      </div>
    </div>

    <div class="toolbar">
      <div class="filter-wrap">
        <SearchableSelect
          v-model="selectedCategoryId"
          :options="categoryOptions"
          :searchable="true"
          placeholder="筛选本体类别"
          @change="onCategoryChange"
        />
      </div>
      <button class="icon-btn" @click="loadDetail" title="刷新">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
      </button>
      <ExcelImportExport scope="relations" :category-id="selectedCategoryId" @success="loadDetail" />
    </div>

    <div v-if="loading" class="loading-state"><span class="spinner"></span> 加载中...</div>
    <div v-else-if="!selectedCategoryId" class="empty-state">
      <div class="empty-title">请先创建本体类别</div>
      <div class="empty-desc">前往「本体管理」创建类别后即可维护关系字典</div>
    </div>
    <template v-else-if="detail">
      <RelationDictEditor
        :category-id="selectedCategoryId"
        :relations="detail.relations"
        @changed="onSubChanged"
      />
    </template>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }

.toolbar { display: flex; align-items: center; gap: 10px; }
.filter-wrap { width: 280px; flex-shrink: 0; }
.icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 38px; height: 38px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; transition: background 150ms, color 150ms; flex-shrink: 0; }
.icon-btn:hover { background: var(--c-muted); color: var(--c-fg); }

.loading-state { padding: 40px; text-align: center; color: var(--c-secondary); font-size: 14px; }
.empty-state { text-align: center; padding: 48px 20px; color: var(--c-secondary); }
.empty-title { font-size: 15px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.empty-desc { font-size: 13px; }
</style>
