<script setup>
import { ref, computed, onMounted } from 'vue'
import { fetchRelationInstances, deleteRelationInstance, fetchKbs } from '../../api'
import SearchableSelect from '../common/SearchableSelect.vue'

const relations = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const hasPrev = ref(false)
const hasNext = ref(false)
const loading = ref(false)

const search = ref('')
const kbId = ref('')
const kbs = ref([])

let searchTimer = null

const kbOptions = computed(() => [
  { value: '', label: '全部知识库', meta: '' },
  ...kbs.value.map(k => ({ value: k.id, label: k.name, meta: `${k.file_count || 0} 文件` })),
])

const totalPages = computed(() => Math.ceil(total.value / pageSize.value) || 1)

async function load() {
  loading.value = true
  try {
    const res = await fetchRelationInstances({
      kb_id: kbId.value,
      q: search.value.trim(),
      page: page.value,
      page_size: pageSize.value,
    })
    relations.value = res.items || []
    total.value = res.total || 0
    hasPrev.value = !!res.has_prev
    hasNext.value = !!res.has_next
  } catch {
    relations.value = []
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

async function remove(rel) {
  const label = `${rel.source_entity_name || '—'} —${rel.relation_def_name || rel.relation_type}→ ${rel.target_entity_name || '—'}`
  if (!confirm(`确认删除关系「${label}」？\n图谱中的对应边将同步删除。`)) return
  try {
    await deleteRelationInstance(rel.id)
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

onMounted(async () => {
  try { kbs.value = await fetchKbs() } catch {}
  await load()
})
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">关系实例</h2>
        <span class="page-subtitle">知识抽取生成的关系实例，三元组形式展示</span>
      </div>
      <router-link to="/entities" class="link-btn">← 实体管理</router-link>
    </div>

    <div class="toolbar">
      <div class="search-wrap">
        <svg class="search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="text" v-model="search" placeholder="搜索关系类型或实体名..." @input="onSearch">
      </div>
      <div class="kb-filter">
        <SearchableSelect
          v-model="kbId"
          :options="kbOptions"
          placeholder="筛选知识库"
          @change="onKbChange"
        />
      </div>
      <button class="icon-btn refresh-btn" @click="load" title="刷新">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
      </button>
    </div>

    <div v-if="loading && !relations.length" class="loading-state"><span class="spinner"></span> 加载中...</div>

    <div v-else-if="relations.length" class="rel-list">
      <div v-for="rel in relations" :key="rel.id" class="rel-row">
        <div class="rel-tri">
          <span class="rel-node" :title="rel.source_entity_type">{{ rel.source_entity_name || '—' }}</span>
          <span class="rel-edge">—{{ rel.relation_def_name || rel.relation_type }}→</span>
          <span class="rel-node" :title="rel.target_entity_type">{{ rel.target_entity_name || '—' }}</span>
        </div>
        <button class="rm-btn sm" @click="remove(rel)" title="删除">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
        </button>
      </div>
    </div>

    <div v-else class="empty-state">
      <div class="empty-icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6.5" cy="6.5" r="2.25"/><circle cx="17.5" cy="6.5" r="2.25"/><circle cx="12" cy="17.5" r="2.25"/><path d="M8.75 6.5h6.5"/></svg>
      </div>
      <div class="empty-title">{{ search || kbId ? '没有匹配的关系' : '暂无关系实例' }}</div>
      <div class="empty-desc" v-if="!search && !kbId">处理文件并完成知识抽取后，关系将出现在这里</div>
    </div>

    <div v-if="total > 0" class="pager">
      <span class="pager-info">共 {{ total }} 条 · 第 {{ page }}/{{ totalPages }} 页</span>
      <div class="pager-btns">
        <button class="btn sm" :disabled="!hasPrev" @click="goPage(page - 1)">上一页</button>
        <button class="btn sm" :disabled="!hasNext" @click="goPage(page + 1)">下一页</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }
.link-btn { font-size: 13px; color: var(--c-accent); text-decoration: none; font-weight: 600; }
.link-btn:hover { text-decoration: underline; }

.toolbar { display: flex; align-items: center; gap: 10px; }
.search-wrap { flex: 1; display: flex; align-items: center; gap: 8px; padding: 0 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); height: 38px; }
.search-wrap:focus-within { border-color: var(--c-fg); }
.search-icon { color: var(--c-secondary); flex-shrink: 0; }
.search-wrap input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--c-fg); font-size: 14px; font-family: var(--font); }
.search-wrap input::placeholder { color: var(--c-secondary); opacity: 0.7; }
.kb-filter { width: 220px; flex-shrink: 0; }
.icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 38px; height: 38px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; transition: background 150ms, color 150ms; flex-shrink: 0; }
.icon-btn:hover { background: var(--c-muted); color: var(--c-fg); }

.rel-list { display: flex; flex-direction: column; gap: 6px; }
.rel-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); }
.rel-row:hover { background: var(--c-muted); }
.rel-tri { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.rel-node { font-size: 13px; font-weight: 600; color: var(--c-fg); padding: 3px 10px; border-radius: 12px; background: var(--c-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.rel-edge { font-size: 12px; color: var(--c-accent); font-weight: 600; }
.rm-btn.sm { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; flex-shrink: 0; }
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
