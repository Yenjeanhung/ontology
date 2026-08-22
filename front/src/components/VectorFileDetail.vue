<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchVectorFileChunks, fetchVectorSearchTest, fetchVectorSummaryExport } from '../api'
import SearchableSelect from './common/SearchableSelect.vue'

const route = useRoute()
const router = useRouter()
const fileId = computed(() => route.params.fileId)

const fileMeta = ref(null) // { file_name, kb_name, kb_id }
const chunks = ref([])
const loading = ref(false)
const error = ref('')
const expandedRows = ref({})

const testQuery = ref('')
const testTopK = ref(8)
const testLoading = ref(false)
const testResults = ref([])
const exportLoading = ref(false)

const topKOptions = [
  { value: 5, label: 'Top 5' },
  { value: 8, label: 'Top 8' },
  { value: 10, label: 'Top 10' },
]

const statusHelpItems = [
  { key: 'synced', label: '已同步', desc: '业务库有这条分片，向量库里也查到了同一个 embedding_id。' },
  { key: 'missing_vector', label: '向量缺失', desc: '业务库里有分片和 embedding_id，但去向量库里没查到对应记录。' },
  { key: 'missing_id', label: '缺少 ID', desc: '业务库里有分片，但没有 embedding_id，暂时无法去向量库校验。' },
  { key: 'unchecked', label: '未校验', desc: '本次没有完成向量库校验，比如 provider 未实现、collection 读取失败或校验过程异常。' },
]

const kbId = computed(() => fileMeta.value?.kb_id || '')

async function loadDetail() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchVectorFileChunks(fileId.value)
    fileMeta.value = { file_name: data.file_name, kb_name: data.kb_name, kb_id: data.kb_id }
    chunks.value = data.items || []
  } catch (err) {
    error.value = err.message || '加载分片详情失败'
  } finally {
    loading.value = false
  }
}

function toggleRow(chunkId) {
  expandedRows.value[chunkId] = !expandedRows.value[chunkId]
}

function getStatusMeta(row) {
  if (!row.embedding_id) {
    return { key: 'missing_id', label: '缺少 ID', desc: statusHelpItems[2].desc }
  }
  if (row.store_found === true) {
    return { key: 'synced', label: '已同步', desc: statusHelpItems[0].desc }
  }
  if (row.store_found === false) {
    return { key: 'missing_vector', label: '向量缺失', desc: statusHelpItems[1].desc }
  }
  return { key: 'unchecked', label: '未校验', desc: statusHelpItems[3].desc }
}

async function runSearchTest() {
  if (!kbId.value || !testQuery.value.trim()) return
  testLoading.value = true
  try {
    const data = await fetchVectorSearchTest({
      kbId: kbId.value,
      query: testQuery.value.trim(),
      topK: testTopK.value,
    })
    testResults.value = data.items || []
  } catch (err) {
    testResults.value = []
    error.value = err.message || '相似检索测试失败'
  } finally {
    testLoading.value = false
  }
}

async function exportSummary(format) {
  exportLoading.value = true
  try {
    const data = await fetchVectorSummaryExport({ kbId: kbId.value, format })
    const content = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
    const blob = new Blob([content], {
      type: format === 'md' ? 'text/markdown;charset=utf-8' : 'application/json;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `vector-summary-${fileMeta.value?.file_name || fileId.value}.${format === 'md' ? 'md' : 'json'}`
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    error.value = err.message || '导出失败'
  } finally {
    exportLoading.value = false
  }
}

function goBack() {
  router.push('/vectors')
}

onMounted(loadDetail)
</script>

<template>
  <div class="vfd-page">
    <!-- 页头 -->
    <div class="page-head">
      <button class="back-btn" @click="goBack">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
      </button>
      <div class="head-title">
        <div class="head-name">{{ fileMeta?.file_name || '加载中...' }}</div>
        <div class="head-sub" v-if="fileMeta">
          {{ fileMeta.kb_name }} · {{ chunks.length }} 个分片
        </div>
      </div>
      <div class="head-actions">
        <button class="btn" :disabled="exportLoading" @click="exportSummary('json')">导出 JSON</button>
        <button class="btn" :disabled="exportLoading" @click="exportSummary('md')">导出 Markdown</button>
      </div>
    </div>

    <!-- 召回测试 -->
    <div class="vectors-card tool-card">
      <div class="tool-title">召回测试</div>
      <div class="tool-subtitle">对所属知识库「{{ fileMeta?.kb_name || '...' }}」的向量库做 topK 相似召回。</div>
      <div class="test-controls">
        <input
          v-model="testQuery"
          type="text"
          placeholder="输入要测试召回的问题，比如：什么是 RLHF？"
          @keydown.enter="runSearchTest"
        >
        <div class="topk-select-wrap">
          <SearchableSelect v-model="testTopK" :options="topKOptions" :searchable="false" />
        </div>
        <button
          class="btn primary"
          :disabled="testLoading || !kbId || !testQuery.trim()"
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
    </div>

    <!-- 分片列表 -->
    <div class="vectors-card">
      <div v-if="loading" class="loading-row">
        <span class="spinner"></span>
        加载分片详情...
      </div>
      <div v-else-if="error && !chunks.length" class="error-text">{{ error }}</div>
      <template v-else-if="chunks.length">
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

        <div v-for="row in chunks" :key="row.chunk_id" class="vector-row">
          <div class="row-main" @click="toggleRow(row.chunk_id)">
            <div class="row-col">
              <div class="cell-title">#{{ row.chunk_index }}</div>
              <div class="cell-sub">{{ row.content_length }} chars</div>
            </div>
            <div class="row-col">
              <div class="mono">{{ row.embedding_id || '--' }}</div>
            </div>
            <div class="row-col">
              <span class="status-chip" :class="getStatusMeta(row).key" :title="getStatusMeta(row).desc">
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
      </template>
      <div v-else class="empty-state">
        <div class="empty-title">该文件暂无分片</div>
        <div class="empty-desc">文件尚未分片或分片数据已清理。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vfd-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 页头 */
.page-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.back-btn {
  width: 36px; height: 36px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--c-border); border-radius: 10px;
  background: var(--c-panel); color: var(--c-secondary); cursor: pointer;
  transition: background 150ms, color 150ms;
}
.back-btn:hover { background: var(--c-muted); color: var(--c-fg); }

.head-title { min-width: 0; }
.head-name {
  font-size: 18px; font-weight: 700; color: var(--c-fg);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.head-sub { margin-top: 3px; font-size: 12.5px; color: var(--c-secondary); }
.head-actions { margin-left: auto; display: flex; gap: 8px; flex-shrink: 0; }

.vectors-card {
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 18px;
  box-shadow: 0 10px 30px rgba(23, 23, 23, 0.04);
  overflow: hidden;
}

/* 按钮 */
.btn {
  height: 38px; padding: 0 14px;
  border: 1px solid var(--c-border); border-radius: 10px;
  background: var(--c-panel); color: var(--c-fg);
  font-weight: 600; cursor: pointer; font-family: var(--font); font-size: 13px;
  transition: background 150ms, border-color 150ms;
}
.btn:hover { background: var(--c-muted); }
.btn.primary { background: var(--c-fg); color: #fff; border-color: var(--c-fg); }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }

/* 召回测试 */
.tool-card { padding: 18px; }
.tool-title { font-size: 16px; font-weight: 700; color: var(--c-fg); }
.tool-subtitle { margin-top: 6px; color: var(--c-secondary); font-size: 13px; line-height: 1.6; }

.test-controls {
  display: grid;
  grid-template-columns: 1fr 110px auto;
  gap: 10px;
  margin-top: 14px;
}

.test-controls input {
  height: 42px; padding: 0 12px;
  border: 1px solid var(--c-border); border-radius: 12px;
  background: var(--c-panel); font-size: 14px; outline: none; color: var(--c-fg);
  font-family: var(--font);
}

.test-controls input:focus {
  border-color: var(--c-accent);
  box-shadow: 0 0 0 4px var(--c-accent-weak);
}

.test-controls :deep(.ss-trigger) {
  height: 42px;
  min-height: 42px;
  border-radius: 12px;
}

.test-results {
  display: flex; flex-direction: column; gap: 10px;
  max-height: 360px; margin-top: 14px; overflow: auto;
}

.test-item {
  padding: 12px; border: 1px solid var(--c-border); border-radius: 12px;
  background: var(--c-muted);
}
.test-item-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: 12px; }
.test-rank {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 32px; height: 24px; border-radius: 999px;
  background: var(--c-fg); color: var(--c-bg); font-weight: 700;
}
.test-file { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
.test-score { color: var(--c-accent); font-weight: 700; }
.test-snippet {
  color: var(--c-fg); font-size: 13px; line-height: 1.6;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 4; overflow: hidden;
}

/* 分片表格 */
.table-header {
  display: grid;
  grid-template-columns: 110px minmax(180px, 1.2fr) 120px minmax(260px, 2fr) 56px;
  gap: 12px; padding: 10px 20px;
  border-bottom: 1px solid var(--c-border);
  background: var(--c-muted);
  color: var(--c-secondary); font-size: 12px; font-weight: 700;
}

.status-header { position: relative; display: inline-flex; align-items: center; gap: 6px; }
.help-dot {
  display: inline-flex; align-items: center; justify-content: center;
  width: 17px; height: 17px; border-radius: 999px;
  border: 1px solid var(--c-border); background: var(--c-muted); color: var(--c-secondary);
  font-size: 10px; font-weight: 700; font-family: Georgia, "Times New Roman", serif;
  font-style: italic; line-height: 1; cursor: help;
}
.status-tooltip {
  position: absolute; left: 0; top: calc(100% + 10px); z-index: 20; width: 320px;
  padding: 12px; border: 1px solid var(--c-border); border-radius: 14px;
  background: var(--c-panel-elevated); box-shadow: 0 16px 40px rgba(23, 23, 23, 0.12);
  opacity: 0; visibility: hidden; transform: translateY(4px);
  transition: opacity 0.18s ease, transform 0.18s ease, visibility 0.18s ease;
}
.status-header:hover .status-tooltip { opacity: 1; visibility: visible; transform: translateY(0); }
.status-tooltip-item + .status-tooltip-item { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--c-border); }
.status-tooltip-item { display: flex; flex-direction: column; gap: 4px; line-height: 1.5; }
.status-tooltip-item strong { color: var(--c-fg); font-size: 12px; }
.status-tooltip-item span { color: var(--c-secondary); font-size: 12px; font-weight: 400; }

.vector-row + .vector-row { border-top: 1px solid var(--c-border); }
.row-main {
  display: grid;
  grid-template-columns: 110px minmax(180px, 1.2fr) 120px minmax(260px, 2fr) 56px;
  gap: 12px; align-items: start; padding: 14px 20px; cursor: pointer;
}
.row-main:hover { background: var(--c-muted); }
.cell-title { font-size: 13px; font-weight: 600; }
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px; color: var(--c-fg); word-break: break-all;
}
.cell-sub { margin-top: 4px; color: var(--c-secondary); font-size: 12px; }

.status-chip {
  display: inline-flex; align-items: center; padding: 4px 10px;
  border-radius: 999px; background: var(--c-muted); color: var(--c-secondary);
  font-size: 12px; font-weight: 600;
}
.status-chip.synced, .status-chip.ok { background: #ecfdf3; color: #15803d; }
.status-chip.missing_vector, .status-chip.miss { background: #fef2f2; color: #b91c1c; }
.status-chip.missing_id { background: #fff7ed; color: #c2410c; }
.status-chip.unchecked { background: var(--c-muted); color: var(--c-secondary); }

.preview-text {
  color: var(--c-fg); font-size: 13px; line-height: 1.6;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden;
}
.action-col { text-align: right; }
.expand-indicator { color: var(--c-secondary); font-size: 12px; }

.row-expanded { padding: 0 20px 16px; }
.expanded-grid { display: grid; gap: 10px; }
.expanded-meta {
  display: grid; gap: 8px; padding: 12px;
  border: 1px solid var(--c-border); border-radius: 12px; background: var(--c-muted);
}
.meta-row { display: flex; gap: 12px; align-items: flex-start; }
.meta-name { min-width: 72px; color: var(--c-secondary); font-size: 12px; font-weight: 700; }
.expanded-block {
  overflow: hidden; border: 1px solid var(--c-border); border-radius: 12px; background: var(--c-muted);
}
.expanded-label {
  padding: 10px 12px; border-bottom: 1px solid var(--c-border);
  background: var(--c-panel); color: var(--c-secondary); font-size: 12px; font-weight: 700;
}
.expanded-pre {
  margin: 0; padding: 12px; color: var(--c-fg); font-size: 12px; line-height: 1.65;
  white-space: pre-wrap; word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.loading-row { display: flex; align-items: center; gap: 8px; padding: 22px 18px; color: var(--c-secondary); }
.error-text { padding: 18px; color: var(--c-danger); }
.empty-state { padding: 40px 20px; text-align: center; }
.empty-title { font-size: 16px; font-weight: 700; color: var(--c-fg); }
.empty-desc { margin-top: 8px; color: var(--c-secondary); font-size: 13px; }

@media (max-width: 900px) {
  .page-head { flex-wrap: wrap; }
  .head-actions { width: 100%; margin-left: 0; }
  .test-controls { grid-template-columns: 1fr; }
  .table-header { display: none; }
  .row-main { grid-template-columns: 1fr; }
  .action-col { text-align: left; }
}
</style>
