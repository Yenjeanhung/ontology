<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import {
  fetchKbs,
  fetchVectorRecords,
  fetchVectorSearchTest,
  fetchVectorSummaryExport,
} from '../api'

const kbs = ref([])
const selectedKbId = ref('')
const searchText = ref('')
const onlyUnsynced = ref(false)
const records = ref([])
const provider = ref('')
const vectorProvider = inject('vectorProvider', ref(''))
const total = ref(0)
const sourceTotal = ref(0)
const recordsLoading = ref(false)
const error = ref('')
const expandedRows = ref({})
const pageInput = ref('1')

const page = ref(1)
const pageSize = ref(20)

const testQuery = ref('')
const testTopK = ref(8)
const testLoading = ref(false)
const testResults = ref([])
const exportLoading = ref(false)

const statusHelpItems = [
  { key: 'synced', label: '已同步', desc: '业务库有这条分片，向量库里也查到了同一个 embedding_id。' },
  { key: 'missing_vector', label: '向量缺失', desc: '业务库里有分片和 embedding_id，但去向量库里没查到对应记录。' },
  { key: 'missing_id', label: '缺少 ID', desc: '业务库里有分片，但没有 embedding_id，暂时无法去向量库校验。' },
  { key: 'unchecked', label: '未校验', desc: '本次没有完成向量库校验，比如 provider 未实现、collection 读取失败或校验过程异常。' },
]

const pageOffset = computed(() => (page.value - 1) * pageSize.value)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const pageStart = computed(() => (total.value ? pageOffset.value + 1 : 0))
const pageEnd = computed(() => Math.min(pageOffset.value + records.value.length, total.value))

const groupedRecords = computed(() => {
  const kbMap = new Map()
  for (const row of records.value) {
    if (!kbMap.has(row.kb_id)) {
      kbMap.set(row.kb_id, {
        kb_id: row.kb_id,
        kb_name: row.kb_name,
        files: new Map(),
      })
    }

    const kbGroup = kbMap.get(row.kb_id)
    if (!kbGroup.files.has(row.file_id)) {
      kbGroup.files.set(row.file_id, {
        file_id: row.file_id,
        file_name: row.file_name,
        rows: [],
      })
    }

    kbGroup.files.get(row.file_id).rows.push(row)
  }

  return Array.from(kbMap.values()).map(group => ({
    ...group,
    files: Array.from(group.files.values()),
  }))
})

async function loadKbs() {
  try {
    kbs.value = await fetchKbs()
  } catch {
    kbs.value = []
  }
}

async function loadRecords() {
  recordsLoading.value = true
  error.value = ''
  try {
    const data = await fetchVectorRecords({
      kbId: selectedKbId.value,
      q: searchText.value.trim(),
      unsyncedOnly: onlyUnsynced.value,
      limit: pageSize.value,
      offset: pageOffset.value,
    })
    records.value = data.items || []
    provider.value = data.provider || ''
    total.value = data.total || 0
    sourceTotal.value = data.source_total || 0
    const maxPage = Math.max(1, Math.ceil((data.total || 0) / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
    }
    pageInput.value = String(page.value)
    expandedRows.value = {}
  } catch (err) {
    error.value = err.message || '加载向量数据失败'
  } finally {
    recordsLoading.value = false
  }
}

function toggleRow(chunkId) {
  expandedRows.value[chunkId] = !expandedRows.value[chunkId]
}

function submitFilters() {
  page.value = 1
  loadRecords()
}

function changePage(nextPage) {
  if (nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return
  page.value = nextPage
  pageInput.value = String(nextPage)
  loadRecords()
}

function onPageSizeChange() {
  page.value = 1
  pageInput.value = '1'
  loadRecords()
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

function getStatusMeta(row) {
  if (!row.embedding_id) {
    return {
      key: 'missing_id',
      label: '缺少 ID',
      desc: '业务库里有分片，但没有 embedding_id，暂时无法去向量库校验。',
    }
  }
  if (row.store_found === true) {
    return {
      key: 'synced',
      label: '已同步',
      desc: '业务库有这条分片，向量库里也查到了同一个 embedding_id。',
    }
  }
  if (row.store_found === false) {
    return {
      key: 'missing_vector',
      label: '向量缺失',
      desc: '业务库里有分片和 embedding_id，但去向量库里没查到对应记录。',
    }
  }
  return {
    key: 'unchecked',
    label: '未校验',
    desc: '本次没有完成向量库校验，比如 provider 未实现、collection 读取失败或校验过程异常。',
  }
}

async function runSearchTest() {
  if (!selectedKbId.value || !testQuery.value.trim()) return
  testLoading.value = true
  error.value = ''
  try {
    const data = await fetchVectorSearchTest({
      kbId: selectedKbId.value,
      query: testQuery.value.trim(),
      topK: testTopK.value,
    })
    testResults.value = data.items || []
  } catch (err) {
    error.value = err.message || '相似检索测试失败'
  } finally {
    testLoading.value = false
  }
}

async function exportSummary(format) {
  exportLoading.value = true
  error.value = ''
  try {
    const data = await fetchVectorSummaryExport({
      kbId: selectedKbId.value,
      format,
    })
    const content = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
    const blob = new Blob(
      [content],
      {
        type: format === 'md'
          ? 'text/markdown;charset=utf-8'
          : 'application/json;charset=utf-8',
      },
    )
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `vector-summary${selectedKbId.value ? `-${selectedKbId.value}` : ''}.${format === 'md' ? 'md' : 'json'}`
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    error.value = err.message || '导出失败'
  } finally {
    exportLoading.value = false
  }
}

onMounted(async () => {
  await loadKbs()
  pageInput.value = String(page.value)
  await loadRecords()
})
</script>

<template>
  <div class="vectors-page">
    <div class="vectors-toolbar">
      <div>
        <div class="toolbar-title">向量数据 <span class="provider-chip vector" v-if="vectorProvider" :title="`向量库: ${vectorProvider}`">{{ vectorProvider }}</span></div>
        <div class="toolbar-subtitle">查看分片后的索引记录、同步状态，以及当前向量库的命中数据。</div>
      </div>
      <div class="toolbar-meta">
        <span class="meta-chip">当前页: {{ records.length }}</span>
        <span class="meta-chip">总记录: {{ total }}</span>
      </div>
    </div>

    <div class="vectors-card filter-card">
      <div class="section-head">
        <div class="section-title">记录筛选</div>
        <div class="section-subtitle">筛选已经入库的分片记录，不会触发向量召回。</div>
      </div>
      <div class="vectors-filters">
        <select v-model="selectedKbId">
          <option value="">全部知识库</option>
          <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
        </select>
        <input
          v-model="searchText"
          type="text"
          placeholder="筛选文件名、分片内容或向量 ID"
          @keydown.enter="submitFilters"
        >
        <label class="toggle-box">
          <input v-model="onlyUnsynced" type="checkbox">
          <span>仅看异常状态</span>
        </label>
        <select v-model="pageSize" @change="onPageSizeChange">
          <option :value="10">10 / 页</option>
          <option :value="20">20 / 页</option>
          <option :value="50">50 / 页</option>
          <option :value="100">100 / 页</option>
        </select>
        <button class="btn primary" :disabled="recordsLoading" @click="submitFilters">查询记录</button>
      </div>
    </div>

    <div class="tools-grid">
      <section class="vectors-card tool-card">
        <div class="tool-title">召回测试</div>
        <div class="tool-subtitle">输入问题，直接调用当前知识库的向量库做 topK 相似召回。</div>
        <div class="test-controls">
          <input
            v-model="testQuery"
            type="text"
            placeholder="输入要测试召回的问题，比如：什么是 RLHF？"
            @keydown.enter="runSearchTest"
          >
          <select v-model="testTopK">
            <option :value="5">Top 5</option>
            <option :value="8">Top 8</option>
            <option :value="10">Top 10</option>
          </select>
          <button
            class="btn primary"
            :disabled="testLoading || !selectedKbId || !testQuery.trim()"
            @click="runSearchTest"
          >
            测试
          </button>
        </div>
        <div v-if="testResults.length" class="test-results">
          <div
            v-for="item in testResults"
            :key="`${item.rank}-${item.file_id}-${item.start_offset}`"
            class="test-item"
          >
            <div class="test-item-head">
              <span class="test-rank">#{{ item.rank }}</span>
              <span class="test-file">{{ item.file_name }}</span>
              <span class="test-score">{{ Math.round(item.score * 100) }}%</span>
            </div>
            <div class="test-snippet">{{ item.chunk_text }}</div>
          </div>
        </div>
        <div v-else-if="testLoading" class="loading-row">
          <span class="spinner"></span>
          相似检索测试中...
        </div>
      </section>

      <section class="vectors-card tool-card">
        <div class="tool-title">导出索引摘要</div>
        <div class="tool-subtitle">导出当前筛选条件下的统计结果，便于排查和存档。</div>
        <div class="export-actions">
          <button class="btn" :disabled="exportLoading" @click="exportSummary('json')">导出 JSON</button>
          <button class="btn" :disabled="exportLoading" @click="exportSummary('md')">导出 Markdown</button>
        </div>
        <div class="tool-note">
          摘要会包含 provider、业务表来源、向量表来源、知识库统计和同步情况。
        </div>
      </section>
    </div>

    <div v-if="error" class="vectors-card">
      <div class="error-text">{{ error }}</div>
    </div>

    <div v-else-if="recordsLoading && !records.length" class="vectors-card">
      <div class="loading-row">
        <span class="spinner"></span>
        加载向量记录中...
      </div>
    </div>

    <template v-else-if="groupedRecords.length">
      <div class="vectors-groups">
        <section v-for="kb in groupedRecords" :key="kb.kb_id" class="vectors-card">
          <div class="group-head">
            <div>
              <div class="group-title">{{ kb.kb_name }}</div>
              <div class="group-sub">{{ kb.files.length }} 个文件</div>
            </div>
            <div class="inline-pager">
              <div class="pager-summary">
                {{ pageStart }} - {{ pageEnd }} / {{ total }}
                <span v-if="onlyUnsynced" class="pager-note">（原始 {{ sourceTotal }}）</span>
              </div>
              <div class="pager-actions">
                <button class="btn pager-btn" :disabled="recordsLoading || page <= 1" @click="changePage(page - 1)">上一页</button>
                <div class="pager-jump">
                  <input
                    v-model="pageInput"
                    class="pager-input"
                    type="text"
                    inputmode="numeric"
                    :disabled="recordsLoading"
                    @keydown.enter="submitPageInput"
                    @blur="submitPageInput"
                  >
                  <span class="pager-total">/ {{ totalPages }}</span>
                </div>
                <button class="btn pager-btn" :disabled="recordsLoading || page >= totalPages" @click="changePage(page + 1)">下一页</button>
              </div>
            </div>
          </div>

          <div class="group-body" :class="{ 'is-loading': recordsLoading }">
            <div class="group-loading" v-if="recordsLoading">
              <span class="spinner"></span>
              正在刷新当前页...
            </div>

            <div v-for="file in kb.files" :key="file.file_id" class="file-group">
            <div class="file-head">
              <div class="file-title">{{ file.file_name }}</div>
              <div class="file-sub">{{ file.rows.length }} 个分片</div>
            </div>

            <div class="table-header">
              <div>分片序号</div>
              <div>向量 ID</div>
              <div class="status-header">
                <span>同步状态</span>
                <span class="help-dot" aria-hidden="true">i</span>
                <div class="status-tooltip">
                  <div v-for="item in statusHelpItems" :key="item.key" class="status-tooltip-item">
                    <strong>{{ item.label }}</strong>
                    <span>{{ item.desc }}</span>
                  </div>
                </div>
              </div>
              <div>内容预览</div>
              <div>操作</div>
            </div>

            <div v-for="row in file.rows" :key="row.chunk_id" class="vector-row">
              <div class="row-main" @click="toggleRow(row.chunk_id)">
                <div class="row-col">
                  <div class="cell-title">#{{ row.chunk_index }}</div>
                  <div class="cell-sub">{{ row.content_length }} chars</div>
                </div>
                <div class="row-col">
                  <div class="mono">{{ row.embedding_id || '--' }}</div>
                </div>
                <div class="row-col">
                  <span
                    class="status-chip"
                    :class="getStatusMeta(row).key"
                    :title="getStatusMeta(row).desc"
                  >
                    {{ getStatusMeta(row).label }}
                  </span>
                </div>
                <div class="row-col">
                  <div class="preview-text">{{ row.store_document_preview || row.content_preview }}</div>
                </div>
                <div class="row-col action-col">
                  <span class="expand-indicator">{{ expandedRows[row.chunk_id] ? '收起' : '展开' }}</span>
                </div>
              </div>

              <div v-if="expandedRows[row.chunk_id]" class="row-expanded">
                <div class="expanded-grid">
                  <div class="expanded-meta">
                    <div class="meta-row">
                      <span class="meta-name">chunk_id</span>
                      <span class="mono">{{ row.chunk_id }}</span>
                    </div>
                    <div class="meta-row">
                      <span class="meta-name">file_id</span>
                      <span class="mono">{{ row.file_id }}</span>
                    </div>
                    <div class="meta-row">
                      <span class="meta-name">kb_id</span>
                      <span class="mono">{{ row.kb_id }}</span>
                    </div>
                  </div>
                  <div class="expanded-block">
                    <div class="expanded-label">完整 Chunk</div>
                    <pre class="expanded-pre">{{ row.content_full }}</pre>
                  </div>
                  <div v-if="row.store_metadata && Object.keys(row.store_metadata).length" class="expanded-block">
                    <div class="expanded-label">向量库 Metadata</div>
                    <pre class="expanded-pre">{{ JSON.stringify(row.store_metadata, null, 2) }}</pre>
                  </div>
                </div>
              </div>
            </div>
            </div>
          </div>
        </section>
      </div>
    </template>

    <div v-else class="vectors-card empty-state">
      <div class="empty-title">暂无向量记录</div>
      <div class="empty-desc">当前筛选条件下没有查到分片后的向量数据。</div>
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
  background: rgba(206, 147, 216, 0.18); color: #ce93d8;
}
.provider-chip.vector {
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
  background: #fff;
  color: var(--c-secondary);
  font-size: 12px;
}

.vectors-card {
  background: #fff;
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
  grid-template-columns: 220px minmax(240px, 1fr) auto 110px auto;
  gap: 10px;
  align-items: center;
}

.vectors-filters select,
.vectors-filters input,
.test-controls select,
.test-controls input {
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: #fff;
  font-size: 14px;
  outline: none;
}

.vectors-filters input:focus,
.vectors-filters select:focus,
.test-controls input:focus,
.test-controls select:focus {
  border-color: #bfb6ff;
  box-shadow: 0 0 0 4px rgba(118, 88, 255, 0.08);
}

.toggle-box {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--c-secondary);
  font-size: 13px;
}

.btn {
  height: 42px;
  padding: 0 16px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: #fff;
  color: var(--c-fg);
  font-weight: 600;
  cursor: pointer;
}

.btn.primary {
  background: #171717;
  color: #fff;
  border-color: #171717;
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.tools-grid {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr;
  gap: 14px;
}

.tool-card {
  padding: 18px;
}

.tool-title {
  font-size: 16px;
  font-weight: 700;
}

.tool-subtitle,
.tool-note {
  margin-top: 6px;
  color: var(--c-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.test-controls {
  display: grid;
  grid-template-columns: 1fr 110px auto;
  gap: 10px;
  margin-top: 14px;
}

.test-results {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 340px;
  margin-top: 14px;
  overflow: auto;
}

.test-item {
  padding: 12px;
  border: 1px solid #f0f0f0;
  border-radius: 12px;
  background: #fafafa;
}

.test-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 12px;
}

.test-rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  height: 24px;
  border-radius: 999px;
  background: #171717;
  color: #fff;
  font-weight: 700;
}

.test-file {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.test-score {
  color: var(--c-accent);
  font-weight: 700;
}

.test-snippet {
  color: var(--c-fg);
  font-size: 13px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
  overflow: hidden;
}

.export-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}

.pager-summary {
  color: var(--c-fg);
  font-size: 13px;
}

.pager-note {
  color: var(--c-secondary);
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
  background: #fff;
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

.vectors-groups {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.group-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 20px;
  border-bottom: 1px solid #f0f0f0;
}

.inline-pager {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.group-body {
  position: relative;
}

.group-body.is-loading {
  pointer-events: none;
}

.group-loading {
  position: absolute;
  top: 12px;
  right: 16px;
  z-index: 3;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid #ece7df;
  border-radius: 12px;
  background: rgba(255, 252, 247, 0.95);
  color: var(--c-secondary);
  font-size: 12px;
  box-shadow: 0 12px 30px rgba(92, 78, 58, 0.08);
}

.group-body.is-loading::after {
  content: '';
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.58);
  backdrop-filter: blur(1px);
  z-index: 2;
}

.group-title {
  font-size: 18px;
  font-weight: 700;
}

.group-sub,
.file-sub,
.cell-sub {
  margin-top: 4px;
  color: var(--c-secondary);
  font-size: 12px;
}

.file-group + .file-group {
  border-top: 1px solid #f5f5f5;
}

.file-head {
  padding: 16px 20px 10px;
}

.file-title {
  font-size: 14px;
  font-weight: 600;
}

.table-header {
  display: grid;
  grid-template-columns: 110px minmax(180px, 1.2fr) 120px minmax(260px, 2fr) 56px;
  gap: 12px;
  padding: 10px 20px;
  border-top: 1px solid #f8f8f8;
  border-bottom: 1px solid #f0f0f0;
  background: #fafafa;
  color: var(--c-secondary);
  font-size: 12px;
  font-weight: 700;
}

.status-header {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.help-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 17px;
  height: 17px;
  border-radius: 999px;
  border: 1px solid #d8d8d8;
  background: #fbfbfb;
  color: var(--c-secondary);
  font-size: 10px;
  font-weight: 700;
  font-family: Georgia, "Times New Roman", serif;
  font-style: italic;
  line-height: 1;
  cursor: help;
}

.status-tooltip {
  position: absolute;
  left: 0;
  top: calc(100% + 10px);
  z-index: 20;
  width: 320px;
  padding: 12px;
  border: 1px solid #ececec;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 16px 40px rgba(23, 23, 23, 0.12);
  opacity: 0;
  visibility: hidden;
  transform: translateY(4px);
  transition: opacity 0.18s ease, transform 0.18s ease, visibility 0.18s ease;
}

.status-header:hover .status-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateY(0);
}

.status-tooltip-item + .status-tooltip-item {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #f3f3f3;
}

.status-tooltip-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  line-height: 1.5;
}

.status-tooltip-item strong {
  color: var(--c-fg);
  font-size: 12px;
}

.status-tooltip-item span {
  color: var(--c-secondary);
  font-size: 12px;
  font-weight: 400;
}

.vector-row + .vector-row {
  border-top: 1px solid #f8f8f8;
}

.row-main {
  display: grid;
  grid-template-columns: 110px minmax(180px, 1.2fr) 120px minmax(260px, 2fr) 56px;
  gap: 12px;
  align-items: start;
  padding: 14px 20px;
  cursor: pointer;
}

.row-main:hover {
  background: #fcfcfc;
}

.cell-title {
  font-size: 13px;
  font-weight: 600;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--c-fg);
  word-break: break-all;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  background: #f5f5f5;
  color: var(--c-secondary);
  font-size: 12px;
  font-weight: 600;
}

.status-chip.ok {
  background: #ecfdf3;
  color: #15803d;
}

.status-chip.miss {
  background: #fef2f2;
  color: #b91c1c;
}

.status-chip.synced {
  background: #ecfdf3;
  color: #15803d;
}

.status-chip.missing_vector {
  background: #fef2f2;
  color: #b91c1c;
}

.status-chip.missing_id {
  background: #fff7ed;
  color: #c2410c;
}

.status-chip.unchecked {
  background: #f5f5f5;
  color: var(--c-secondary);
}

.preview-text {
  color: var(--c-fg);
  font-size: 13px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
}

.action-col {
  text-align: right;
}

.expand-indicator {
  color: var(--c-secondary);
  font-size: 12px;
}

.row-expanded {
  padding: 0 20px 16px;
}

.expanded-grid {
  display: grid;
  gap: 10px;
}

.expanded-meta {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid #f0f0f0;
  border-radius: 12px;
  background: #fafafa;
}

.meta-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.meta-name {
  min-width: 72px;
  color: var(--c-secondary);
  font-size: 12px;
  font-weight: 700;
}

.expanded-block {
  overflow: hidden;
  border: 1px solid #f0f0f0;
  border-radius: 12px;
  background: #fafafa;
}

.expanded-label {
  padding: 10px 12px;
  border-bottom: 1px solid #efefef;
  background: #fcfcfc;
  color: var(--c-secondary);
  font-size: 12px;
  font-weight: 700;
}

.expanded-pre {
  margin: 0;
  padding: 12px;
  color: var(--c-fg);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
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

  .tools-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .vectors-toolbar,
  .group-head {
    flex-direction: column;
    align-items: stretch;
  }

  .vectors-filters,
  .test-controls {
    grid-template-columns: 1fr;
  }

  .pager-actions {
    width: 100%;
    justify-content: space-between;
  }

  .table-header {
    display: none;
  }

  .row-main {
    grid-template-columns: 1fr;
  }

  .action-col {
    text-align: left;
  }
}
</style>
