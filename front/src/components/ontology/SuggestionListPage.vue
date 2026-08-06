<script setup>
import { ref, watch, onMounted } from 'vue'
import { fetchOntologySuggestions, deleteOntologySuggestion } from '../../api'
import SuggestionReviewEditor from './SuggestionReviewEditor.vue'

const STATUS_MAP = { ready: '待审核', approved: '已通过', rejected: '已拒绝', generating: '生成中' }
const STATUS_COLORS = { ready: 'var(--c-primary)', approved: '#4caf50', rejected: '#ef5350', generating: '#ff9800' }
const SOURCE_MAP = { free_extraction: '自由抽取', auto_cluster: '自动聚类' }

const suggestions = ref([])
const loading = ref(false)

const statusFilter = ref('')
const kbIdFilter = ref('')

const reviewingId = ref(null)

async function load() {
  loading.value = true
  try {
    const res = await fetchOntologySuggestions({
      kbId: kbIdFilter.value.trim(),
      status: statusFilter.value,
    })
    suggestions.value = Array.isArray(res) ? res : (res.items || [])
  } catch {
    suggestions.value = []
  } finally {
    loading.value = false
  }
}

watch([statusFilter, kbIdFilter], () => {
  load()
})

function openReview(id) {
  reviewingId.value = id
}

function closeReview() {
  reviewingId.value = null
}

function onReviewed() {
  reviewingId.value = null
  emit('changed')
  load()
}

async function removeSuggestion(item) {
  if (!confirm(`确认删除该本体建议？此操作不可恢复。`)) return
  try {
    await deleteOntologySuggestion(item.id)
    emit('changed')
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

function fmtScore(score) {
  if (score == null) return '—'
  return Math.round(score * 100) + '%'
}

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function statsData(item) {
  const sd = item.suggestion_data || {}
  return {
    ontologyCount: sd.ontologies ? sd.ontologies.length : (sd.ontology_count || 0),
    relationCount: sd.relations ? sd.relations.length : (sd.relation_count || 0),
    constraintCount: sd.constraints ? sd.constraints.length : (sd.constraint_count || 0),
  }
}

const emit = defineEmits(['changed'])

onMounted(load)
</script>

<template>
  <div class="sl-page">
    <div class="sl-head">
      <div class="sl-title-row">
        <h2 class="sl-title">本体建议</h2>
        <span class="sl-subtitle">知识抽取或聚类分析自动生成的本体建议，审核通过后将写入本体类别</span>
      </div>
    </div>

    <div class="sl-toolbar">
      <div class="sl-filter-group">
        <div class="sl-select-wrap">
          <select v-model="statusFilter" class="sl-select">
            <option value="">全部</option>
            <option value="ready">待审核</option>
            <option value="approved">已通过</option>
            <option value="rejected">已拒绝</option>
          </select>
        </div>
        <div class="sl-input-wrap">
          <svg class="sl-input-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input
            type="text"
            v-model="kbIdFilter"
            class="sl-input"
            placeholder="知识库 ID"
          >
        </div>
      </div>
      <button class="icon-btn sm" @click="load" title="刷新">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
      </button>
    </div>

    <div v-if="reviewingId" class="sl-review-panel">
      <div class="sl-review-head">
        <span class="sl-review-title">审核本体建议</span>
        <button class="icon-btn sm" @click="closeReview" title="关闭">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </div>
      <SuggestionReviewEditor
        :suggestion-id="reviewingId"
        @changed="onReviewed"
      />
    </div>

    <div v-if="loading && !suggestions.length" class="sl-loading">
      <span class="spinner"></span> 加载中...
    </div>

    <div v-else-if="suggestions.length" class="sl-list">
      <div v-for="item in suggestions" :key="item.id" class="sl-card">
        <div class="sl-card-body">
          <div class="sl-card-top">
            <div class="sl-card-info">
              <span class="sl-cat-name">{{ item.suggestion_data?.category?.name || '—' }}</span>
              <span class="sl-score-badge">{{ fmtScore(item.score || item.confidence) }}</span>
              <span
                class="sl-status-badge"
                :style="{ background: STATUS_COLORS[item.status] || 'var(--c-muted)', color: '#fff' }"
              >{{ STATUS_MAP[item.status] || item.status }}</span>
            </div>
            <span class="sl-source-tag">{{ SOURCE_MAP[item.source_mode] || item.source_mode || '—' }}</span>
          </div>
          <div class="sl-card-stats">
            <span class="sl-stat">本体 {{ statsData(item).ontologyCount }}</span>
            <span class="sl-stat">关系 {{ statsData(item).relationCount }}</span>
            <span class="sl-stat">约束 {{ statsData(item).constraintCount }}</span>
          </div>
          <div class="sl-card-time">{{ fmtTime(item.created_at || item.created_at) }}</div>
        </div>
        <div class="sl-card-actions">
          <button
            v-if="item.status === 'ready'"
            class="btn primary sm"
            @click="openReview(item.id)"
          >审核</button>
          <button
            class="btn sm"
            @click="removeSuggestion(item)"
          >删除</button>
        </div>
      </div>
    </div>

    <div v-else class="sl-empty">
      <div class="sl-empty-icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="6" height="6" rx="1.5"/><rect x="14.75" y="3.25" width="6" height="6" rx="1.5"/><rect x="9" y="14.75" width="6" height="6" rx="1.5"/><path d="M6.25 9.25v1.75a1.5 1.5 0 0 0 1.5 1.5h1.25"/><path d="M17.75 9.25v1.75a1.5 1.5 0 0 1-1.5 1.5H15.25"/></svg>
      </div>
      <div class="sl-empty-title">暂无本体建议</div>
    </div>
  </div>
</template>

<style scoped>
.sl-page { display: flex; flex-direction: column; gap: 16px; }
.sl-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.sl-title-row { display: flex; flex-direction: column; gap: 2px; }
.sl-title { font-size: 20px; font-weight: 700; color: var(--c-fg); margin: 0; }
.sl-subtitle { font-size: 12px; color: var(--c-secondary); }

.sl-toolbar { display: flex; align-items: center; gap: 10px; }
.sl-filter-group { display: flex; align-items: center; gap: 10px; flex: 1; }
.sl-select-wrap { position: relative; }
.sl-select {
  appearance: none; padding: 0 32px 0 12px; height: 38px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel) url("data:image/svg+xml,%3Csvg width='10' height='6' viewBox='0 0 10 6' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23888' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat right 10px center;
  color: var(--c-fg); font-size: 14px; font-family: var(--font);
  cursor: pointer; outline: none; min-width: 120px;
}
.sl-select:focus { border-color: var(--c-fg); }

.sl-input-wrap {
  display: flex; align-items: center; gap: 8px;
  padding: 0 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); height: 38px; width: 200px;
}
.sl-input-wrap:focus-within { border-color: var(--c-fg); }
.sl-input-icon { color: var(--c-secondary); flex-shrink: 0; }
.sl-input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--c-fg); font-size: 14px; font-family: var(--font); }
.sl-input::placeholder { color: var(--c-secondary); opacity: 0.7; }

.sl-review-panel {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); overflow: hidden;
}
.sl-review-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px; border-bottom: 1px solid var(--c-border);
  background: var(--c-muted);
}
.sl-review-title { font-size: 14px; font-weight: 600; color: var(--c-fg); }

.sl-loading { padding: 40px; text-align: center; color: var(--c-secondary); }

.sl-list { display: flex; flex-direction: column; gap: 8px; }
.sl-card {
  display: flex; align-items: center; gap: 16px;
  padding: 14px 16px; border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel);
  transition: background 150ms, border-color 150ms;
}
.sl-card:hover { background: var(--c-muted); border-color: var(--c-fg); }
.sl-card-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.sl-card-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.sl-card-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sl-cat-name { font-size: 15px; font-weight: 600; color: var(--c-fg); }
.sl-score-badge {
  display: inline-flex; align-items: center; padding: 1px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600; background: var(--c-muted); color: var(--c-secondary);
}
.sl-status-badge {
  display: inline-flex; align-items: center; padding: 1px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.sl-source-tag {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: var(--c-muted); color: var(--c-secondary);
}
.sl-card-stats { display: flex; align-items: center; gap: 12px; }
.sl-stat { font-size: 12px; color: var(--c-secondary); }
.sl-card-time { font-size: 12px; color: var(--c-muted); }
.sl-card-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.sl-empty { text-align: center; padding: 48px 20px; color: var(--c-secondary); }
.sl-empty-icon { margin-bottom: 12px; color: var(--c-border); }
.sl-empty-title { font-size: 15px; font-weight: 700; color: var(--c-fg); }
</style>
