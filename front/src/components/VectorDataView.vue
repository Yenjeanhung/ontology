<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchKbs, fetchVectorFiles } from '../api'
import SearchableSelect from './common/SearchableSelect.vue'

const router = useRouter()

const kbs = ref([])
const selectedKbId = ref('')
const searchText = ref('')
const files = ref([])
const vectorProvider = inject('vectorProvider', ref(''))
const total = ref(0)
const loading = ref(false)
const error = ref('')
const pageInput = ref('1')

const page = ref(1)
const pageSize = ref(20)

const pageOffset = computed(() => (page.value - 1) * pageSize.value)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const pageStart = computed(() => (total.value ? pageOffset.value + 1 : 0))
const pageEnd = computed(() => Math.min(pageOffset.value + files.value.length, total.value))

const kbOptions = computed(() => [
  { value: '', label: '全部知识库' },
  ...kbs.value.map(kb => ({ value: kb.id, label: kb.name })),
])

const pageSizeOptions = [
  { value: 10, label: '10 / 页' },
  { value: 20, label: '20 / 页' },
  { value: 50, label: '50 / 页' },
  { value: 100, label: '100 / 页' },
]

async function loadKbs() {
  try {
    kbs.value = await fetchKbs()
  } catch {
    kbs.value = []
  }
}

async function loadFiles() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchVectorFiles({
      kbId: selectedKbId.value,
      q: searchText.value.trim(),
      limit: pageSize.value,
      offset: pageOffset.value,
    })
    files.value = data.items || []
    total.value = data.total || 0
    const maxPage = Math.max(1, Math.ceil((data.total || 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
    }
    pageInput.value = String(page.value)
  } catch (err) {
    error.value = err.message || '加载向量文件失败'
  } finally {
    loading.value = false
  }
}

function submitFilters() {
  page.value = 1
  loadFiles()
}

function changePage(nextPage) {
  if (nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return
  page.value = nextPage
  pageInput.value = String(nextPage)
  loadFiles()
}

function onPageSizeChange() {
  page.value = 1
  pageInput.value = '1'
  loadFiles()
}

function submitPageInput() {
  const raw = Number.parseInt(String(pageInput.value).trim(), 10)
  if (Number.isNaN(raw)) {
    pageInput.value = String(page.value)
    return
  }
  const nextPage = Math.min(Math.max(raw, 1), totalPages.value)
  pageInput.value = String(nextPage)
  changePage(nextPage)
}

function openDetail(file) {
  router.push(`/vectors/${file.file_id}`)
}

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(async () => {
  await loadKbs()
  pageInput.value = String(page.value)
  await loadFiles()
})
</script>

<template>
  <div class="vectors-page">
    <div class="vectors-toolbar">
      <div>
        <div class="toolbar-title">向量数据 <span class="provider-chip vector" v-if="vectorProvider" :title="`向量库: ${vectorProvider}`">{{ vectorProvider }}</span></div>
        <div class="toolbar-subtitle">查看已入库文件的向量索引概况，点击「详情」查看分片与同步状态。</div>
      </div>
      <div class="toolbar-meta">
        <span class="meta-chip">当前页: {{ files.length }}</span>
        <span class="meta-chip">总文件: {{ total }}</span>
      </div>
    </div>

    <div class="vectors-card filter-card">
      <div class="section-head">
        <div class="section-title">文件筛选</div>
        <div class="section-subtitle">按知识库与文件名筛选已入库的文件。</div>
      </div>
      <div class="vectors-filters">
        <div class="kb-select-wrap">
          <SearchableSelect v-model="selectedKbId" :options="kbOptions" placeholder="全部知识库" search-placeholder="搜索知识库..." />
        </div>
        <input
          v-model="searchText"
          type="text"
          placeholder="筛选文件名"
          @keydown.enter="submitFilters"
        >
        <div class="size-select-wrap">
          <SearchableSelect v-model="pageSize" :options="pageSizeOptions" :searchable="false" @change="onPageSizeChange" />
        </div>
        <button class="btn primary" :disabled="loading" @click="submitFilters">查询</button>
      </div>
    </div>

    <div v-if="error" class="vectors-card error-text">{{ error }}</div>

    <div v-else-if="loading && !files.length" class="vectors-card loading-row">
      <span class="spinner"></span>
      加载文件列表...
    </div>

    <div v-else-if="files.length" class="vectors-card table-card">
      <table>
        <thead>
          <tr>
            <th>文件</th>
            <th>知识库</th>
            <th style="width:80px">分片</th>
            <th style="width:160px">创建时间</th>
            <th style="width:90px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="file in files" :key="file.file_id" @click="openDetail(file)">
            <td>
              <div class="file-cell">
                <span class="file-cell-icon">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                </span>
                <span class="file-cell-name">{{ file.file_name }}</span>
              </div>
            </td>
            <td class="kb-cell">{{ file.kb_name }}</td>
            <td class="num">{{ file.chunk_count }}</td>
            <td class="time">{{ fmtTime(file.created_at) }}</td>
            <td>
              <button class="btn link" @click.stop="openDetail(file)">详情</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="pager-bar">
        <div class="pager-summary">{{ pageStart }} - {{ pageEnd }} / {{ total }}</div>
        <div class="pager-actions">
          <button class="btn pager-btn" :disabled="loading || page <= 1" @click="changePage(page - 1)">上一页</button>
          <div class="pager-jump">
            <input
              v-model="pageInput"
              class="pager-input"
              type="text"
              inputmode="numeric"
              :disabled="loading"
              @keydown.enter="submitPageInput"
              @blur="submitPageInput"
            >
            <span class="pager-total">/ {{ totalPages }}</span>
          </div>
          <button class="btn pager-btn" :disabled="loading || page >= totalPages" @click="changePage(page + 1)">下一页</button>
        </div>
      </div>
    </div>

    <div v-else class="vectors-card empty-state">
      <div class="empty-title">暂无向量文件</div>
      <div class="empty-desc">当前筛选条件下没有查到已入库的文件。</div>
    </div>
  </div>
</template>

<style scoped>
.vectors-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.vectors-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
}

.toolbar-title {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.provider-chip {
  display: inline-block; vertical-align: middle;
  font-size: 11px; font-weight: 600; padding: 1px 7px; margin-left: 6px;
  border-radius: 4px;
  background: rgba(129, 199, 132, 0.18); color: #81c784;
}

.toolbar-subtitle {
  margin-top: 4px;
  color: var(--c-secondary);
  font-size: 13px;
}

.toolbar-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.meta-chip {
  padding: 6px 10px;
  border: 1px solid var(--c-border);
  border-radius: 999px;
  background: var(--c-panel);
  color: var(--c-secondary);
  font-size: 12px;
}

.vectors-card {
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(23, 23, 23, 0.04);
}

.filter-card {
  padding: 16px 18px 18px;
}

.section-head {
  margin-bottom: 12px;
}

.section-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--c-fg);
}

.section-subtitle {
  margin-top: 4px;
  color: var(--c-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.vectors-filters {
  display: grid;
  grid-template-columns: 200px minmax(240px, 1fr) 120px auto;
  gap: 10px;
  align-items: center;
}

.vectors-filters input {
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-panel);
  font-size: 14px;
  outline: none;
  color: var(--c-fg);
  font-family: var(--font);
}

.vectors-filters input:focus {
  border-color: var(--c-accent);
  box-shadow: 0 0 0 4px var(--c-accent-weak);
}

/* 知识库/每页条数下拉：复用 SearchableSelect，统一高度与圆角 */
.vectors-filters :deep(.ss-trigger) {
  height: 42px;
  min-height: 42px;
  border-radius: 12px;
}

.btn {
  height: 42px;
  padding: 0 16px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-panel);
  color: var(--c-fg);
  font-weight: 600;
  cursor: pointer;
  font-family: var(--font);
  font-size: 13px;
  transition: background 150ms, border-color 150ms;
}

.btn:hover {
  background: var(--c-muted);
}

.btn.primary {
  background: var(--c-fg);
  color: #fff;
  border-color: var(--c-fg);
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.btn.link {
  border: none; background: none; color: var(--c-accent);
  padding: 0 4px; height: 24px; font-size: 12.5px;
}
.btn.link:hover { opacity: 0.8; background: none; }

/* 文件表格 */
.table-card {
  overflow: hidden;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

thead th {
  text-align: left; padding: 10px 14px;
  font-size: 12px; font-weight: 600; color: var(--c-secondary);
  background: var(--c-muted); border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
}

tbody td {
  padding: 12px 14px; border-bottom: 1px solid var(--c-border);
  vertical-align: middle;
}

tbody tr:last-child td { border-bottom: none; }
tbody tr { cursor: pointer; transition: background 120ms; }
tbody tr:hover { background: var(--c-muted); }

.file-cell {
  display: flex; align-items: center; gap: 10px;
  font-weight: 600; color: var(--c-fg);
  min-width: 0;
}

.file-cell-icon {
  width: 30px; height: 30px; border-radius: var(--radius-sm);
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--c-accent-weak); color: var(--c-accent); flex-shrink: 0;
}

.file-cell-name {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

.kb-cell { color: var(--c-secondary); font-size: 12.5px; }
.num { color: var(--c-secondary); font-size: 12.5px; }
.time { color: var(--c-secondary); font-size: 12px; white-space: nowrap; }

/* 分页 */
.pager-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border-top: 1px solid var(--c-border);
  background: var(--c-muted);
}

.pager-summary {
  color: var(--c-fg);
  font-size: 13px;
}

.pager-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pager-jump {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  height: 42px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-panel);
}

.pager-input {
  width: 42px;
  border: 0;
  outline: none;
  text-align: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--c-fg);
  background: transparent;
}

.pager-total {
  color: var(--c-secondary);
  font-size: 13px;
}

.pager-btn {
  min-width: 74px;
}

.loading-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 22px 18px;
  color: var(--c-secondary);
}

.error-text {
  padding: 18px;
  color: var(--c-danger);
}

.empty-state {
  padding: 28px 18px;
  text-align: center;
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
}

.empty-desc {
  margin-top: 8px;
  color: var(--c-secondary);
  font-size: 13px;
}

@media (max-width: 1100px) {
  .vectors-filters {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 900px) {
  .vectors-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .vectors-filters {
    grid-template-columns: 1fr;
  }

  .pager-actions {
    width: 100%;
    justify-content: space-between;
  }
}
</style>
