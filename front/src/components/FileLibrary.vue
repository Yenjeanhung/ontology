<script setup>
import { computed, onMounted, onUnmounted, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { marked } from 'marked'
import FolderTreeNode from './FolderTreeNode.vue'
import {
  attachAssetsToKb,
  createCrawlJob,
  createDirectory,
  deleteDirectory,
  updateDirectory,
  deleteAsset as apiDeleteAsset,
  fetchAssetContent,
  fetchAssets,
  fetchConfig,
  fetchDirectories,
  fetchKbs,
  getAssetPreviewUrl,
  getCrawlJob,
  getLatestCrawlJob,
  updateAsset,
  uploadAssetChunk,
} from '../api'

const CHUNK_SIZE = 512 * 1024
const uuid = () => ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(
  /[018]/g,
  c => (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16),
)

const directories = ref([])
const assets = ref([])
const kbs = ref([])
const selectedDirectoryId = ref('')
const expandedDirectories = ref(new Set(['']))
const search = ref('')
const draggingNode = ref(null)
const dragOverNodeId = ref(null)
const uploading = ref({})
const selectedAssets = ref(new Set())
const selectedKbId = ref('')

// 文件夹编辑相关状态
const showFolderModal = ref(false)
const folderModalMode = ref('create')
const editingFolderId = ref('')
const editingFolderName = ref('')
const processingFolder = ref(false)

// 确认对话框
const showConfirmDialog = ref(false)
const confirmDialogTitle = ref('')
const confirmDialogMessage = ref('')
const confirmDialogConfirmText = ref('确定')
const confirmDialogCancelText = ref('取消')
let confirmDialogCallback = null

// 采集相关
const crawlKeyword = ref('')
const crawlMaxPages = ref(5)
const crawlDepth = ref('medium')
const crawlJobs = ref([])
const crawlTimers = {}
const showCrawlForm = ref(false)
const crawlMaxLimit = ref(20)

const activeCrawlJobs = computed(() => crawlJobs.value.filter(j => j.status === 'running' || j.status === 'queued'))
const finishedCrawlJobs = computed(() => crawlJobs.value.filter(j => j.status === 'done' || j.status === 'failed'))

// 预览相关
const previewAsset = ref(null)
const previewText = ref('')
const previewLoading = ref(false)
const previewMode = ref('preview')
const previewDraft = ref('')
const previewSaving = ref(false)

// 来源详情弹窗
const sourceDetailAsset = ref(null)

let crawlTimer = null

const confirmDialogType = ref('warning')

function showConfirm(title, message, confirmText = '确定', cancelText = '取消', type = 'warning') {
  return new Promise(resolve => {
    confirmDialogTitle.value = title
    confirmDialogMessage.value = message
    confirmDialogConfirmText.value = confirmText
    confirmDialogCancelText.value = cancelText
    confirmDialogType.value = type
    confirmDialogCallback = resolve
    showConfirmDialog.value = true
  })
}

function confirmDialogOk() {
  showConfirmDialog.value = false
  confirmDialogCallback(true)
}

function confirmDialogCancel() {
  showConfirmDialog.value = false
  confirmDialogCallback(false)
}

// 构建文件夹树
const directoryTree = computed(() => {
  const children = new Map()
  for (const item of directories.value) {
    const key = item.parent_id || ''
    if (!children.has(key)) children.set(key, [])
    children.get(key).push(item)
  }
  const build = (parentId) => {
    return (children.get(parentId) || []).map(item => ({
      ...item,
      children: build(item.id)
    }))
  }
  return build('')
})

const selectedCount = computed(() => selectedAssets.value.size)
const readyAssets = computed(() => assets.value.filter(item => item.status === 'ready'))
const route = useRoute()
const selectedDirectory = computed(() => directories.value.find(item => item.id === selectedDirectoryId.value) || null)
const selectedDirectoryName = computed(() => selectedDirectory.value?.name || '全部文件')
const crawlTargetDirectoryLabel = computed(() => selectedDirectory.value?.name || '采集')
const isUsingDefaultCrawlDirectory = computed(() => !selectedDirectory.value)
const highlightInfo = ref({ fileId: '', fileName: '', entityName: '' })
const highlightedAssetId = ref('')
const scrollToHighlightedFile = ref(false)

function fmtSize(value = 0) {
  if (value < 1024) return `${value} B`
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1048576).toFixed(1)} MB`
}

function sourceLabel(source) {
  return { upload: '上传', kb_upload: '知识库上传', crawl: '网络采集', legacy: '历史文件' }[source] || source || '未知'
}

function openSourceDetail(asset) {
  sourceDetailAsset.value = asset
}

function closeSourceDetail() {
  sourceDetailAsset.value = null
}

const CRAWL_STAGE_META = [
  { key: 'search', label: '搜索' },
  { key: 'fetch', label: '抓取' },
  { key: 'llm', label: '整理' },
  { key: 'save', label: '保存' },
]

function getCrawlStages(job) {
  const stages = job?.detail?.stages
  if (!stages) return []
  return CRAWL_STAGE_META.map(m => {
    const s = stages[m.key] || {}
    return { key: m.key, label: m.label, progress: s.progress || 0, elapsed_ms: s.elapsed_ms || 0, started_at: s.started_at }
  })
}

function fmtLogTime(iso) {
  try { return new Date(iso).toLocaleTimeString() } catch { return '' }
}

function isEditableTextAsset(asset) {
  return ['txt', 'md', 'csv', 'json', 'html'].includes((asset?.ext || '').toLowerCase())
}

function isMarkdownAsset(asset) {
  return (asset?.ext || '').toLowerCase() === 'md'
}

function scrollToHighlighted() {
  if (!highlightedAssetId.value) return
  nextTick(() => {
    const el = document.querySelector(`[data-asset-id="${highlightedAssetId.value}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      scrollToHighlightedFile.value = false
    }
  })
}

function applyRouteHighlight() {
  const query = route.query
  if (!query.file_id && !query.file_name) return
  const fileId = String(query.file_id || '')
  const fileName = String(query.file_name || '')
  const entityName = String(query.entity_name || '')
  highlightInfo.value = { fileId, fileName, entityName }

  const target = assets.value.find(item => item.id === fileId || item.name === fileName)
  if (!target) {
    highlightedAssetId.value = ''
    return
  }

  highlightedAssetId.value = target.id
  scrollToHighlightedFile.value = true
  if (entityName) {
    openPreview(target)
  }
  if (scrollToHighlightedFile.value) {
    scrollToHighlighted()
  }
}

watch(() => route.query, () => {
  if (assets.value.length) {
    applyRouteHighlight()
  }
})

function renderMarkdown(text) {
  return marked.parse(text || '')
}

async function loadDirectories() {
  try { directories.value = await fetchDirectories() } catch {}
}

async function loadAssets() {
  try {
    assets.value = await fetchAssets({ directoryId: selectedDirectoryId.value, q: search.value })
    const ids = new Set(assets.value.map(item => item.id))
    selectedAssets.value = new Set([...selectedAssets.value].filter(id => ids.has(id)))
    await applyRouteHighlight()
  } catch {}
}

async function loadKbs() {
  try { kbs.value = await fetchKbs() } catch {}
}

async function refreshAll() {
  const [, cfg] = await Promise.all([Promise.all([loadDirectories(), loadAssets(), loadKbs()]), fetchConfig().catch(() => ({}))])
  if (cfg.crawl_max_pages) {
    crawlMaxLimit.value = cfg.crawl_max_pages
    if (crawlMaxPages.value > cfg.crawl_max_pages) crawlMaxPages.value = cfg.crawl_max_pages
  }
}

function toggleDirectory(directoryId) {
  const next = new Set(expandedDirectories.value)
  if (next.has(directoryId)) {
    next.delete(directoryId)
  } else {
    next.add(directoryId)
  }
  expandedDirectories.value = next
}

function selectDirectory(id) {
  selectedDirectoryId.value = id
  loadAssets()
}

// 拖拽处理
function handleDragStart(node) {
  draggingNode.value = node
}

function handleDragEnd() {
  draggingNode.value = null
  dragOverNodeId.value = null
}

function handleDragOver(node) {
  if (draggingNode.value && draggingNode.value.id !== node.id) {
    dragOverNodeId.value = node.id
  }
}

function handleDragLeave(node) {
  if (dragOverNodeId.value === node.id) {
    dragOverNodeId.value = null
  }
}

async function handleDrop(targetNode) {
  if (!draggingNode.value) return
  
  const sourceNode = draggingNode.value
  
  // 不能拖到自己或自己的子目录下
  if (sourceNode.id === targetNode.id) {
    handleDragEnd()
    return
  }
  
  // 检查是否拖到自己的子目录下
  const isDescendant = isNodeDescendant(targetNode, sourceNode)
  if (isDescendant) {
    handleDragEnd()
    return
  }
  
  try {
      await updateDirectory(sourceNode.id, { parentId: targetNode.id })
      await loadDirectories()
    } catch (error) {
    console.error('Failed to move folder:', error)
  }
  
  handleDragEnd()
}

function isNodeDescendant(parent, child) {
  if (!parent.children || parent.children.length === 0) return false
  for (const childNode of parent.children) {
    if (childNode.id === child.id) return true
    if (isNodeDescendant(childNode, child)) return true
  }
  return false
}

// 根目录拖拽处理
function handleRootDragOver() {
  if (draggingNode.value) {
    dragOverNodeId.value = 'root'
  }
}

function handleRootDragLeave() {
  if (dragOverNodeId.value === 'root') {
    dragOverNodeId.value = null
  }
}

async function handleRootDrop() {
  if (!draggingNode.value) return
  
  const sourceNode = draggingNode.value
  
  try {
    await updateDirectory(sourceNode.id, { parentId: null })
    await loadDirectories()
  } catch (error) {
    console.error('Failed to move folder to root:', error)
  }
  
  handleDragEnd()
}

// 文件夹操作
function openCreateFolder() {
  folderModalMode.value = 'create'
  editingFolderId.value = ''
  editingFolderName.value = ''
  showFolderModal.value = true
}

function openEditFolder(folder) {
  folderModalMode.value = 'edit'
  editingFolderId.value = folder.id
  editingFolderName.value = folder.name
  showFolderModal.value = true
}

async function saveFolder() {
  const name = editingFolderName.value.trim()
  if (!name || processingFolder.value) return
  processingFolder.value = true
  try {
    if (folderModalMode.value === 'create') {
      await createDirectory({ name, parentId: selectedDirectoryId.value || null })
    } else {
      await updateDirectory(editingFolderId.value, { name })
    }
    showFolderModal.value = false
    await loadDirectories()
  } catch {
    window.alert('文件夹操作失败')
  }
  processingFolder.value = false
}

async function deleteFolder(folder) {
  const confirmed = await showConfirm(
    '删除文件夹',
    `确定要删除文件夹「${folder.name}」吗？`,
    '删除',
    '取消'
  )
  if (!confirmed) return
  try {
    await deleteDirectory(folder.id)
    if (selectedDirectoryId.value === folder.id) {
      selectedDirectoryId.value = ''
    }
    await loadDirectories()
    await loadAssets()
  } catch {
    window.alert('删除文件夹失败')
  }
}

// 文件操作
function triggerUpload() {
  const el = document.getElementById('assetFileInput')
  if (el) el.click()
}

async function handlePick(event) {
  await uploadFiles(event.target.files)
  event.target.value = ''
}

async function uploadFiles(fileList) {
  for (const file of fileList) {
    const assetId = uuid()
    uploading.value = { ...uploading.value, [assetId]: { name: file.name, progress: 0 } }
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE) || 1
    try {
      for (let index = 0; index < totalChunks; index += 1) {
        const chunk = file.slice(index * CHUNK_SIZE, (index + 1) * CHUNK_SIZE)
        await uploadAssetChunk({
          assetId,
          fileName: file.name,
          fileSize: file.size,
          directoryId: selectedDirectoryId.value || null,
          chunkIndex: index,
          totalChunks,
          chunk,
        })
        uploading.value = {
          ...uploading.value,
          [assetId]: { ...uploading.value[assetId], progress: Math.round(((index + 1) / totalChunks) * 100) }
        }
      }
    } catch {
      // upload failed
    }
    const next = { ...uploading.value }
    delete next[assetId]
    uploading.value = next
    await loadAssets()
  }
}

async function deleteAsset(asset) {
  const confirmed = await showConfirm(
    '删除文件',
    `确认要删除「${asset.name}」吗？`,
    '删除',
    '取消'
  )
  if (!confirmed) return
  try {
    await apiDeleteAsset(asset.id)
  } catch (error) {
    console.error('Delete error:', error)
    await showConfirm(
      '删除失败',
      '该文件已被知识库使用，无法删除。请先从知识库中移除该文件后再尝试删除。',
      '确定',
      '',
      'warning'
    )
  }
  await loadAssets()
}

async function openPreview(asset) {
  previewAsset.value = asset
  highlightedAssetId.value = asset.id
  previewLoading.value = true
  previewMode.value = 'preview'
  previewDraft.value = ''
  try {
    if (asset.ext !== 'pdf') {
      previewText.value = await fetchAssetContent(asset.id)
      previewDraft.value = previewText.value
    } else {
      previewText.value = ''
    }
  } catch {
    previewText.value = ''
    previewDraft.value = ''
  }
  previewLoading.value = false
}

function closePreview() {
  previewAsset.value = null
  previewText.value = ''
  previewDraft.value = ''
  previewMode.value = 'preview'
  previewSaving.value = false
}

function togglePreviewMode(mode) {
  previewMode.value = mode
  if (mode === 'edit') {
    previewDraft.value = previewText.value
  }
}

async function savePreviewContent() {
  if (!previewAsset.value || !isEditableTextAsset(previewAsset.value) || previewSaving.value) return
  previewSaving.value = true
  try {
    const updated = await updateAsset(previewAsset.value.id, { content: previewDraft.value })
    previewText.value = previewDraft.value
    previewAsset.value = { ...previewAsset.value, ...updated }
    assets.value = assets.value.map(item => (
      item.id === updated.id
        ? { ...item, ...updated }
        : item
    ))
    previewMode.value = 'preview'
  } catch {
    window.alert('保存失败')
  }
  previewSaving.value = false
}

function toggleAssetSelection(id) {
  const next = new Set(selectedAssets.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedAssets.value = next
}

async function attachSelected() {
  if (!selectedKbId.value || selectedCount.value === 0) return
  try {
    await attachAssetsToKb(selectedKbId.value, [...selectedAssets.value])
    window.alert(`已添加 ${selectedCount.value} 个文件`)
    selectedAssets.value = new Set()
  } catch {
    window.alert('添加失败')
  }
}

// 采集相关
async function startCrawl() {
  if (!crawlKeyword.value.trim()) return
  try {
    const job = await createCrawlJob({
      keyword: crawlKeyword.value.trim(),
      directoryId: selectedDirectoryId.value || null,
      maxPages: crawlMaxPages.value,
      analysisDepth: crawlDepth.value,
    })
    crawlJobs.value = [...crawlJobs.value, job]
    crawlKeyword.value = ''
    showCrawlForm.value = false
    watchCrawlJob(job.id)
  } catch {
    window.alert('创建采集任务失败')
  }
}

function watchCrawlJob(jobId) {
  if (crawlTimers[jobId]) clearInterval(crawlTimers[jobId])
  crawlTimers[jobId] = setInterval(async () => {
    try {
      const job = await getCrawlJob(jobId)
      crawlJobs.value = crawlJobs.value.map(j => j.id === jobId ? job : j)
      if (job.status === 'done' || job.status === 'failed') {
        clearInterval(crawlTimers[jobId])
        delete crawlTimers[jobId]
        await loadAssets()
        // 失败的任务保留更长时间，让用户看到错误信息
        const delay = job.status === 'failed' ? 30000 : 5000
        setTimeout(() => {
          crawlJobs.value = crawlJobs.value.filter(j => j.id !== jobId)
        }, delay)
      }
    } catch {
      clearInterval(crawlTimers[jobId])
      delete crawlTimers[jobId]
    }
  }, 1500)
}

function dismissCrawlJob(jobId) {
  crawlJobs.value = crawlJobs.value.filter(j => j.id !== jobId)
}

async function restoreCrawlJobs() {
  try {
    const job = await getLatestCrawlJob()
    if (!job) return
    if (!job.finished_at && (job.status === 'running' || job.status === 'queued')) {
      crawlJobs.value = [job]
      watchCrawlJob(job.id)
    }
  } catch (e) {
    console.warn('恢复采集任务失败:', e)
  }
}

onMounted(async () => {
  await Promise.all([refreshAll(), restoreCrawlJobs()])
})

onUnmounted(() => {
  Object.values(crawlTimers).forEach(clearInterval)
})
</script>

<template>
  <div class="library-page">
    <div class="library-toolbar">
      <div class="toolbar-action-group">
        <button class="btn primary" :class="{ active: showCrawlForm }" @click="showCrawlForm = !showCrawlForm">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>
          联网采集
        </button>
        <button class="btn primary" @click="triggerUpload">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          上传文件
        </button>
        <input type="file" id="assetFileInput" multiple style="display: none" @change="handlePick">
      </div>
    </div>

    <div class="library-layout">
      <!-- 左侧文件夹树 -->
      <aside class="folder-panel">
        <div class="folder-tree-header">
          <div class="tree-header-meta">
            <span class="tree-title">文件夹</span>
            <span class="tree-selection-pill">当前: {{ selectedDirectoryName }}</span>
          </div>
          <button class="tree-action-btn" @click="openCreateFolder" title="新建文件夹">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 20V4"/>
              <path d="M4 12h16"/>
            </svg>
          </button>
        </div>
        <div class="folder-tree">
          <div
            :class="['tree-root-item', { active: selectedDirectoryId === '', 'drag-over': dragOverNodeId === 'root' }]"
            @click="selectDirectory('')"
            @dragover.prevent="handleRootDragOver"
            @dragleave="handleRootDragLeave"
            @drop="handleRootDrop"
          >
            <span class="tree-active-marker" aria-hidden="true"></span>
            <svg class="folder-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
            <span class="folder-name">全部文件</span>
            <span v-if="selectedDirectoryId === ''" class="selected-badge">当前</span>
          </div>
          
          <FolderTreeNode
            v-for="node in directoryTree"
            :key="node.id"
            :node="node"
            :expanded="expandedDirectories"
            :selected-id="selectedDirectoryId"
            :dragging-node-id="draggingNode?.id || ''"
            :drag-over-node-id="dragOverNodeId || ''"
            @toggle="toggleDirectory"
            @select="selectDirectory"
            @edit="openEditFolder"
            @delete="deleteFolder"
            @dragstart="handleDragStart"
            @dragend="handleDragEnd"
            @dragover="handleDragOver"
            @dragleave="handleDragLeave"
            @drop="handleDrop"
          />
        </div>
      </aside>

      <!-- 右侧文件列表 -->
      <main class="asset-panel">
        <!-- 文件搜索：紧贴文件列表上方，避免与采集/上传按钮混淆 -->
        <div class="asset-search-bar">
          <div class="search-wrap">
            <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
            <input type="text" v-model="search" placeholder="搜索文件名、来源、摘要..." @input="loadAssets">
          </div>
          <button class="icon-btn refresh-btn" @click="refreshAll" title="刷新">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
          </button>
        </div>

        <!-- 采集表单 - 点击采集按钮展开 -->
        <div class="crawl-band" v-if="showCrawlForm || activeCrawlJobs.length > 0 || finishedCrawlJobs.length > 0">
          <div class="crawl-fields">
            <input type="text" v-model="crawlKeyword" placeholder="输入关键词联网采集资料" @keydown.enter="startCrawl">
            <input type="number" class="small-input" v-model.number="crawlMaxPages" :min="1" :max="crawlMaxLimit" placeholder="页数">
            <select v-model="crawlDepth" class="depth-select">
              <option value="low">低</option>
              <option value="medium">中</option>
              <option value="high">高</option>
            </select>
            <button class="btn primary crawl-btn" @click="startCrawl" :disabled="!crawlKeyword.trim()">
              开始采集
            </button>
            <button class="icon-btn" v-if="!activeCrawlJobs.length" @click="showCrawlForm = false" title="收起">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="18 15 12 9 6 15"/></svg>
            </button>
          </div>
          <div :class="['crawl-target-banner', { warning: isUsingDefaultCrawlDirectory }]">
            <div class="crawl-target-copy">
              <span class="crawl-target-label">采集保存位置</span>
              <strong class="crawl-target-name">{{ crawlTargetDirectoryLabel }}</strong>
            </div>
            <span v-if="isUsingDefaultCrawlDirectory" class="crawl-target-tip">未选择左侧目录时，将保存到默认“采集”目录。</span>
            <span v-else class="crawl-target-tip">本次采集会保存到左侧当前选中的目录。</span>
          </div>
          <div class="crawl-hint">
            <span class="hint-item">页数：要采集的网页数量</span>
            <span class="hint-item">维度：越高分析越详细</span>
          </div>

          <!-- 进行中的采集任务 -->
          <div v-for="job in activeCrawlJobs" :key="job.id" class="crawl-task-card">
            <div class="task-card-head">
              <span class="task-dot running"></span>
              <span class="task-keyword">{{ job.keyword }}</span>
              <span class="task-pct">{{ job.progress || 0 }}%</span>
            </div>
            <div class="task-progress-bar">
              <div :style="{ width: `${job.progress || 0}%` }"></div>
            </div>
            <div class="task-stages" v-if="job.detail?.stages">
              <span v-for="s in getCrawlStages(job)" :key="s.key" class="task-stage-chip" :class="{ done: s.progress >= 100, active: s.progress > 0 && s.progress < 100 }">
                <span class="chip-dot">{{ s.progress >= 100 ? '✓' : s.progress > 0 ? '●' : '○' }}</span>
                {{ s.label }} {{ s.progress }}%
              </span>
            </div>
            <details v-if="job.logs && job.logs.length" class="task-logs" open>
              <summary class="task-logs-toggle">日志 ({{ job.logs.length }})</summary>
              <div class="task-logs-body">
                <div v-for="(log, i) in job.logs" :key="i" class="task-log-line">
                  <span class="tl-time">[{{ fmtLogTime(log.time) }}]</span>
                  <span class="tl-level" :class="`tl-${log.level}`">{{ log.level === 'error' ? 'ERR' : log.level === 'warning' ? 'WRN' : 'INF' }}</span>
                  <span class="tl-msg">{{ log.message }}</span>
                </div>
              </div>
            </details>
          </div>

          <!-- 已完成的采集任务（成功/失败） -->
          <div v-for="job in finishedCrawlJobs" :key="job.id" class="crawl-task-card" :class="{ 'task-failed': job.status === 'failed', 'task-done': job.status === 'done' }">
            <div class="task-card-head">
              <span class="task-dot" :class="job.status === 'failed' ? 'failed' : 'done'"></span>
              <span class="task-keyword">{{ job.keyword }}</span>
              <span class="task-status-badge" :class="job.status">{{ job.status === 'failed' ? '失败' : '完成' }}</span>
              <button class="icon-btn task-dismiss" @click="dismissCrawlJob(job.id)" title="关闭">✕</button>
            </div>
            <div class="task-message" v-if="job.message">{{ job.message }}</div>
            <details v-if="job.logs && job.logs.length" class="task-logs">
              <summary class="task-logs-toggle">日志 ({{ job.logs.length }})</summary>
              <div class="task-logs-body">
                <div v-for="(log, i) in job.logs" :key="i" class="task-log-line">
                  <span class="tl-time">[{{ fmtLogTime(log.time) }}]</span>
                  <span class="tl-level" :class="`tl-${log.level}`">{{ log.level === 'error' ? 'ERR' : log.level === 'warning' ? 'WRN' : 'INF' }}</span>
                  <span class="tl-msg">{{ log.message }}</span>
                </div>
              </div>
            </details>
          </div>
        </div>

        <!-- 上传进度 -->
        <div v-if="Object.keys(uploading).length > 0" class="uploading-list">
          <div v-for="([assetId, upload], index) in Object.entries(uploading)" :key="assetId" class="uploading-item">
            <span>{{ upload.name }}</span>
            <div class="mini-bar"><div class="mini-fill" :style="{ width: `${upload.progress}%` }"></div></div>
            <span>{{ upload.progress }}%</span>
          </div>
        </div>

        <!-- 批量操作区域 -->
        <div v-if="selectedCount > 0" class="attach-bar">
          <span>已选 {{ selectedCount }} 个文件</span>
          <select v-model="selectedKbId">
            <option value="">选择知识库...</option>
            <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
          </select>
          <button class="btn primary" @click="attachSelected" :disabled="!selectedKbId">添加到知识库</button>
        </div>

        <!-- 文件列表 -->
        <div class="asset-table">
          <div class="table-head">
            <div></div>
            <div>文件</div>
            <div>来源</div>
            <div>知识库</div>
            <div>操作</div>
          </div>
          <div v-if="assets.length > 0">
            <div v-for="asset in assets" :key="asset.id" :class="['asset-row', { selected: selectedAssets.has(asset.id), highlighted: highlightedAssetId === asset.id }]" :data-asset-id="asset.id">
              <button class="check-btn" @click.stop="toggleAssetSelection(asset.id)" :disabled="asset.status !== 'ready'">
                {{ selectedAssets.has(asset.id) ? '✓' : '' }}
              </button>
              <div class="asset-main" @click="openPreview(asset)">
                <div class="asset-name">{{ asset.name }}</div>
                <div class="asset-meta">
                  <span>{{ fmtSize(asset.size) }}</span>
                  <span v-if="asset.ext">{{ asset.ext.toUpperCase() }}</span>
                  <span>{{ asset.status }}</span>
                </div>
                <div class="asset-summary" v-if="asset.summary">{{ asset.summary }}</div>
              </div>
              <div class="asset-source" :class="{ clickable: asset.sources && asset.sources.length }" @click="asset.sources && asset.sources.length && openSourceDetail(asset)">
                <span class="source-chip">{{ sourceLabel(asset.source_type) }}</span>
                <a v-if="asset.source_url && !(asset.sources && asset.sources.length)" :href="asset.source_url" target="_blank" rel="noreferrer">来源</a>
                <span v-if="asset.sources && asset.sources.length" class="source-count">{{ asset.sources.length }} 个来源</span>
              </div>
              <div class="asset-kb">
                <template v-if="asset.kb_names && asset.kb_names.length">
                  <span v-for="(name, i) in asset.kb_names" :key="i" class="kb-tag">{{ name }}</span>
                </template>
                <span v-else class="kb-none">—</span>
              </div>
              <div class="asset-actions">
                <button class="icon-btn" title="预览" @click="openPreview(asset)">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
                <button class="rm-btn" title="删除" @click="deleteAsset(asset)">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>
                </button>
              </div>
            </div>
          </div>
          <div v-else class="empty-state">
            <div class="empty-title">暂无文件</div>
            <div class="empty-desc">上传或采集后会出现在这里</div>
          </div>
        </div>
      </main>
    </div>

    <!-- 文件夹编辑弹窗 -->
    <div class="modal-mask" v-if="showFolderModal" @click.self="showFolderModal = false">
      <div class="create-dir-modal" @click.stop>
        <h3>{{ folderModalMode === 'create' ? '新建文件夹' : '编辑文件夹' }}</h3>
        <div class="field">
          <label>名称</label>
          <input
            v-model="editingFolderName"
            type="text"
            placeholder="例如：项目文档"
            autofocus
            @keydown.enter="saveFolder"
          >
        </div>
        <div class="actions">
          <button class="btn" @click="showFolderModal = false">取消</button>
          <button class="btn primary" :disabled="!editingFolderName.trim() || processingFolder" @click="saveFolder">
            {{ processingFolder ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 预览弹窗 -->
    <div class="modal-mask" v-if="previewAsset" @click.self="closePreview">
      <div class="preview-modal">
        <div class="preview-head">
          <div>
            <div class="preview-title">{{ previewAsset.name }}</div>
            <div class="preview-sub">{{ sourceLabel(previewAsset.source_type) }} · {{ fmtSize(previewAsset.size) }}</div>
          </div>
          <div class="preview-actions">
            <div v-if="isEditableTextAsset(previewAsset)" class="preview-mode-switch">
              <button :class="{ active: previewMode === 'preview' }" @click="togglePreviewMode('preview')">预览</button>
              <button :class="{ active: previewMode === 'edit' }" @click="togglePreviewMode('edit')">编辑</button>
            </div>
            <button
              v-if="previewMode === 'edit' && isEditableTextAsset(previewAsset)"
              class="btn primary preview-save-btn"
              :disabled="previewSaving"
              @click="savePreviewContent"
            >
              {{ previewSaving ? '保存中...' : '保存' }}
            </button>
            <button class="icon-btn" @click="closePreview">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
            </button>
          </div>
        </div>
        <div class="preview-body">
          <div v-if="previewLoading" class="empty-state">加载中...</div>
          <iframe v-else-if="previewAsset.ext === 'pdf'" :src="getAssetPreviewUrl(previewAsset.id)" class="pdf-frame"></iframe>
          <textarea
            v-else-if="previewMode === 'edit' && isEditableTextAsset(previewAsset)"
            v-model="previewDraft"
            class="preview-editor"
            spellcheck="false"
          ></textarea>
          <div
            v-else-if="isMarkdownAsset(previewAsset)"
            class="preview-markdown markdown-body"
            v-html="renderMarkdown(previewText || '当前 Markdown 内容为空')"
          ></div>
          <pre v-else class="preview-text">{{ previewText || '当前格式暂不支持文本预览' }}</pre>
        </div>
      </div>
    </div>

    <!-- 来源详情弹窗 -->
    <div class="modal-mask" v-if="sourceDetailAsset" @click.self="closeSourceDetail">
      <div class="source-detail-modal" @click.stop>
        <div class="preview-head">
          <div>
            <div class="preview-title">{{ sourceDetailAsset.name }}</div>
            <div class="preview-sub">{{ sourceLabel(sourceDetailAsset.source_type) }} · {{ sourceDetailAsset.sources.length }} 个来源</div>
          </div>
          <div class="preview-actions">
            <button class="icon-btn" @click="closeSourceDetail">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
            </button>
          </div>
        </div>
        <div class="source-detail-body">
          <a v-for="(s, i) in sourceDetailAsset.sources" :key="i" :href="s.url" target="_blank" rel="noreferrer" class="source-item">
            <span class="source-item-title">{{ s.title || s.url }}</span>
            <span class="source-item-url">{{ s.url }}</span>
          </a>
        </div>
      </div>
    </div>

    <!-- 确认对话框 -->
    <div class="modal-mask" v-if="showConfirmDialog" @click.self="confirmDialogCancel">
      <div class="modal confirm-modal" @click.stop>
        <div :class="['confirm-icon', confirmDialogType]">
          <svg v-if="confirmDialogType === 'error'" width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>
          </svg>
          <svg v-else width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2L2 20h20L12 2zm0 15l-.5-6h1l-.5 6zm0-8l-.5-3h1l-.5 3z"/>
          </svg>
        </div>
        <div class="confirm-title">{{ confirmDialogTitle }}</div>
        <div class="confirm-message">{{ confirmDialogMessage }}</div>
        <div class="confirm-actions">
          <button v-if="confirmDialogCancelText" class="confirm-btn cancel" @click="confirmDialogCancel">{{ confirmDialogCancelText }}</button>
          <button :class="['confirm-btn ok', confirmDialogType]" @click="confirmDialogOk">{{ confirmDialogConfirmText }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.library-page { display: flex; flex-direction: column; gap: 16px; }
.library-toolbar { display: flex; gap: 12px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }
.toolbar-action-group { display: flex; gap: 8px; align-items: center; }
.asset-search-bar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
.asset-search-bar .search-wrap { flex: 1; min-width: 0; }
.search-wrap { position: relative; }
.search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--c-secondary); }
.search-wrap input { padding-left: 34px; }
.icon-btn { background: none; border: none; cursor: pointer; color: var(--c-secondary); padding: 7px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; }
.icon-btn:hover { color: var(--c-fg); background: var(--c-muted); }
.icon-btn.danger:hover { color: var(--c-danger); background: rgba(239, 68, 68, 0.08); }
.rm-btn { background: #ef4444; color: #fff; cursor: pointer; padding: 6px 12px; border-radius: 6px; display: flex; align-items: center; justify-content: center; transition: all 150ms; flex-shrink: 0; border: none; font-size: 13px; font-weight: 600; }
.rm-btn:hover { background: #dc2626; }
.library-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; align-items: start; }

/* 左侧文件夹树 */
.folder-panel { 
  border-right: 1px solid var(--c-border); 
  padding-right: 10px; 
  min-height: 70vh; 
  display: flex;
  flex-direction: column;
}

.folder-tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0 12px;
  border-bottom: 1px solid var(--c-border);
  margin-bottom: 8px;
}

.tree-header-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tree-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--c-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.tree-selection-pill {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 4px 10px;
  border: 1px solid rgba(245, 158, 11, 0.24);
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.18), rgba(245, 158, 11, 0.08));
  color: #fbbf24;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tree-action-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 5px;
  border-radius: 4px;
  color: var(--c-secondary);
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.tree-action-btn:hover {
  background-color: var(--c-muted);
  color: var(--c-fg);
}

.folder-tree { 
  flex: 1;
  display: flex; 
  flex-direction: column; 
  gap: 1px;
  overflow-y: auto;
}

.tree-root-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 10px;
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
  color: var(--c-secondary);
  position: relative;
}

.tree-root-item:hover {
  background-color: var(--c-muted);
  color: var(--c-fg);
  transform: translateX(2px);
}

.tree-root-item.active {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.18), rgba(245, 158, 11, 0.08));
  border-color: rgba(245, 158, 11, 0.28);
  box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.08);
  color: #fff3d6;
}

.tree-root-item.drag-over {
  background-color: var(--c-accent-muted);
  border-left: 2px solid var(--c-accent);
}

.tree-active-marker {
  width: 3px;
  align-self: stretch;
  border-radius: 999px;
  background: transparent;
  flex-shrink: 0;
}

.tree-root-item.active .tree-active-marker {
  background: linear-gradient(180deg, #f59e0b, #fcd34d);
  box-shadow: 0 0 12px rgba(245, 158, 11, 0.45);
}

.expand-placeholder {
  width: 20px;
  flex-shrink: 0;
}

.folder-icon { 
  width: 16px; 
  flex-shrink: 0; 
  color: #f59e0b;
}

.folder-name { 
  flex: 1;
  min-width: 0; 
  white-space: nowrap; 
  overflow: hidden; 
  text-overflow: ellipsis;
  font-size: 13px;
}

.selected-badge {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: #fde68a;
  font-size: 11px;
  font-weight: 700;
}

.asset-panel { min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.crawl-band, .attach-bar, .uploading-list { border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-panel); padding: 12px; }
.crawl-fields { display: flex; gap: 8px; align-items: center; }
.small-input { width: 60px !important; text-align: center; }
.depth-select { padding: 6px 8px; border: 1px solid var(--c-border); border-radius: 6px; font-size: 12px; background: var(--c-bg); min-width: 72px; }
.crawl-btn { padding: 8px 16px !important; gap: 6px; white-space: nowrap; }
.crawl-target-banner {
  margin-top: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(245, 158, 11, 0.16);
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.12), rgba(15, 23, 42, 0.18));
}
.crawl-target-banner.warning {
  border-color: rgba(248, 113, 113, 0.24);
  background: linear-gradient(135deg, rgba(248, 113, 113, 0.12), rgba(15, 23, 42, 0.18));
}
.crawl-target-copy {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.crawl-target-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--c-secondary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.crawl-target-name {
  font-size: 15px;
  color: var(--c-fg);
}
.crawl-target-tip {
  display: block;
  margin-top: 6px;
  font-size: 12px;
  color: var(--c-secondary);
}
.crawl-hint { display: flex; gap: 16px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--c-border); font-size: 11px; color: var(--c-secondary); }
.hint-item { display: flex; align-items: center; gap: 4px; }

/* 进行中的采集任务卡片 */
.crawl-task-card { margin-top: 10px; padding: 10px 12px; background: var(--c-muted); border-radius: 8px; border-left: 3px solid #22c55e; }
.crawl-task-card + .crawl-task-card { margin-top: 6px; }
.task-card-head { display: flex; align-items: center; gap: 8px; }
.task-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.task-dot.running { background: #22c55e; box-shadow: 0 0 8px rgba(34, 197, 94, 0.5); animation: pulse-dot 1.5s ease-in-out infinite; }
.task-dot.done { background: #22c55e; }
.task-dot.failed { background: #ef4444; }
@keyframes pulse-dot { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.task-keyword { font-weight: 600; font-size: 13px; color: var(--c-text); flex: 1; }
.task-pct { font-weight: 700; font-size: 12px; color: var(--c-text); }
.task-status-badge { font-size: 11px; padding: 1px 8px; border-radius: 999px; font-weight: 600; white-space: nowrap; }
.task-status-badge.done { background: rgba(34, 197, 94, 0.12); color: #16a34a; }
.task-status-badge.failed { background: rgba(239, 68, 68, 0.12); color: #dc2626; }
.task-dismiss { padding: 2px 6px !important; font-size: 12px; line-height: 1; opacity: 0.5; }
.task-dismiss:hover { opacity: 1; }
.task-message { font-size: 12px; color: #dc2626; margin-top: 6px; padding: 6px 8px; background: rgba(239, 68, 68, 0.06); border-radius: 6px; white-space: pre-wrap; word-break: break-word; }
.task-failed { border-left-color: #ef4444 !important; }
.task-done { border-left-color: #22c55e !important; }
.task-progress-bar { height: 3px; background: var(--c-border); border-radius: 999px; overflow: hidden; margin: 6px 0; }
.task-progress-bar div { height: 100%; background: var(--c-fg); transition: width 300ms ease; }
.task-stages { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.task-stage-chip { display: inline-flex; align-items: center; gap: 3px; font-size: 11px; padding: 2px 7px; border-radius: 999px; background: var(--c-bg); color: var(--c-secondary); }
.task-stage-chip .chip-dot { font-size: 8px; }
.task-stage-chip.active { color: #22c55e; background: rgba(34, 197, 94, 0.08); }
.task-stage-chip.done { color: var(--c-success); }
.task-logs { margin-top: 6px; }
.task-logs-toggle { cursor: pointer; font-weight: 600; color: var(--c-secondary); font-size: 11px; padding: 3px 0; }
.task-logs-toggle:hover { color: var(--c-fg); }
.task-logs-body { margin-top: 4px; max-height: 160px; overflow-y: auto; font-family: monospace; font-size: 11px; background: var(--c-bg); border-radius: 6px; padding: 8px; }
.task-log-line { display: flex; gap: 6px; padding: 1px 0; line-height: 1.5; }
.tl-time { color: var(--c-secondary); white-space: nowrap; }
.tl-level { font-weight: 700; min-width: 28px; }
.tl-level.tl-error { color: #ef4444; }
.tl-level.tl-warning { color: #f59e0b; }
.tl-level.tl-info { color: var(--c-secondary); }
.tl-msg { color: var(--c-text); word-break: break-word; }
.attach-bar { display: flex; gap: 10px; align-items: center; }
.attach-bar span { flex: 1; font-size: 13px; font-weight: 600; }
.attach-bar select { min-width: 180px; padding: 7px 10px; border: 1px solid var(--c-border); border-radius: 6px; }
.uploading-list { display: flex; flex-direction: column; gap: 8px; }
.uploading-item { display: grid; grid-template-columns: minmax(0, 1fr) 120px 44px; align-items: center; gap: 10px; font-size: 12px; color: var(--c-secondary); }
.mini-bar { height: 4px; background: var(--c-muted); border-radius: 999px; overflow: hidden; }
.mini-bar div { height: 100%; background: var(--c-accent); }
.asset-table { border: 1px solid var(--c-border); border-radius: 8px; overflow: hidden; background: var(--c-panel); }
.table-head, .asset-row { display: grid; grid-template-columns: 44px minmax(260px, 1fr) 140px 180px 92px; align-items: center; gap: 10px; padding: 10px 12px; }
.table-head { background: var(--c-muted); color: var(--c-secondary); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.4px; }
.asset-row + .asset-row { border-top: 1px solid var(--c-border); }
.asset-row:hover, .asset-row.selected { background: var(--c-muted); }
.check-btn { width: 22px; height: 22px; border: 1px solid var(--c-border); border-radius: 6px; background: var(--c-panel); color: var(--c-fg); cursor: pointer; }
.check-btn:disabled { opacity: 0.35; cursor: not-allowed; }
.asset-main { min-width: 0; cursor: pointer; }
.asset-name { font-size: 13px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.asset-meta { display: flex; gap: 8px; color: var(--c-secondary); font-size: 11px; margin-top: 2px; }
.asset-summary { color: var(--c-secondary); font-size: 12px; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.asset-source { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.asset-source.clickable { cursor: pointer; }
.asset-source.clickable:hover { opacity: 0.8; }
.source-chip { padding: 2px 7px; border-radius: 999px; background: var(--c-muted); color: var(--c-secondary); font-weight: 600; }
.asset-source a { color: var(--c-accent); text-decoration: none; }
.source-count { color: var(--c-accent); font-weight: 600; }
.asset-kb { display: flex; flex-wrap: wrap; gap: 4px; }
.kb-tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; background: var(--c-muted); color: var(--c-text); white-space: nowrap; }
.kb-none { font-size: 13px; color: var(--c-secondary); }
.asset-actions { display: flex; gap: 4px; }
.source-detail-modal { width: min(560px, 90vw); max-height: 70vh; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; }
.source-detail-body { padding: 12px 16px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.source-item { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 8px; background: var(--c-muted); text-decoration: none; transition: background 0.15s; }
.source-item:hover { background: var(--c-border); }
.source-item-title { font-size: 13px; font-weight: 600; color: var(--c-text); word-break: break-word; }
.source-item-url { font-size: 11px; color: var(--c-secondary); word-break: break-all; }

.modal-mask { position: fixed; inset: 0; z-index: 300; display: flex; align-items: center; justify-content: center; background: var(--c-overlay); padding: 24px; }
.modal { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 12px; padding: 24px; max-width: 420px; width: 100%; }
.create-dir-modal h3 { font-size: 16px; font-weight: 700; margin-bottom: 16px; }
.field { margin-bottom: 16px; }
.field label { display: block; font-size: 13px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.preview-modal { width: min(860px, 94vw); max-height: 86vh; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; }
.preview-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--c-border); }
.preview-title { font-weight: 700; font-size: 14px; }
.preview-sub { color: var(--c-secondary); font-size: 12px; margin-top: 2px; }
.preview-body { min-height: 360px; overflow: auto; }
.preview-actions { display: flex; align-items: center; gap: 10px; }
.preview-mode-switch { display: inline-flex; align-items: center; padding: 2px; border: 1px solid var(--c-border); border-radius: 999px; background: var(--c-muted); }
.preview-mode-switch button { border: 0; background: transparent; color: var(--c-secondary); padding: 6px 12px; border-radius: 999px; cursor: pointer; font-size: 12px; font-weight: 600; }
.preview-mode-switch button.active { background: var(--c-panel); color: var(--c-fg); }
.preview-save-btn { min-width: 92px; justify-content: center; }
.preview-text { padding: 16px; white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.65; color: var(--c-fg); }
.preview-editor { width: 100%; min-height: 70vh; border: 0; resize: none; background: var(--c-panel); color: var(--c-fg); padding: 16px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; line-height: 1.7; outline: none; }
.preview-markdown { padding: 16px; color: var(--c-fg); line-height: 1.7; }
.preview-markdown h1, .preview-markdown h2, .preview-markdown h3 { margin: 14px 0 8px; font-weight: 700; }
.preview-markdown h1 { font-size: 1.35em; }
.preview-markdown h2 { font-size: 1.18em; }
.preview-markdown h3 { font-size: 1.06em; }
.preview-markdown p { margin: 8px 0; }
.preview-markdown ul, .preview-markdown ol { margin: 8px 0; padding-left: 1.4em; }
.preview-markdown li { margin: 4px 0; }
.preview-markdown code { background: var(--c-muted); padding: 2px 6px; border-radius: 4px; font-size: 0.92em; }
.preview-markdown pre { background: #0f141a; color: #e5edf5; padding: 14px 16px; border-radius: 8px; overflow-x: auto; margin: 10px 0; }
.preview-markdown pre code { background: transparent; padding: 0; color: inherit; }
.preview-markdown table { width: 100%; border-collapse: collapse; margin: 10px 0; }
.preview-markdown th, .preview-markdown td { border: 1px solid var(--c-border); padding: 8px 10px; text-align: left; vertical-align: top; }
.preview-markdown th { background: var(--c-muted); }
.preview-markdown blockquote { margin: 10px 0; padding: 8px 12px; border-left: 3px solid var(--c-accent); background: rgba(161, 98, 7, 0.08); color: var(--c-secondary); }
.preview-markdown a { color: var(--c-accent); }
.preview-markdown hr { border: 0; border-top: 1px solid var(--c-border); margin: 14px 0; }
.preview-markdown img { max-width: 100%; border-radius: 8px; }
.pdf-frame { width: 100%; height: 70vh; border: 0; }
.empty-state { padding: 40px 24px; text-align: center; color: var(--c-secondary); }
.empty-title { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
.empty-desc { font-size: 12px; }

/* 确认对话框 */
.confirm-modal { text-align: center; }
.confirm-icon { width: 56px; height: 56px; margin: 0 auto 16px; display: flex; align-items: center; justify-content: center; border-radius: 50%; background: rgba(239, 68, 68, 0.1); color: #ef4444; }
.confirm-icon.warning { background: rgba(251, 191, 36, 0.1); color: #fbbf24; }
.confirm-title { font-size: 16px; font-weight: 700; color: var(--c-fg); margin-bottom: 8px; }
.confirm-message { font-size: 13px; color: var(--c-secondary); line-height: 1.5; margin-bottom: 20px; }
.confirm-actions { display: flex; gap: 10px; justify-content: center; }
.confirm-btn { padding: 10px 24px; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 150ms; border: none; }
.confirm-btn.cancel { background: var(--c-muted); color: var(--c-secondary); }
.confirm-btn.cancel:hover { background: var(--c-border); color: var(--c-fg); }
.confirm-btn.ok { background: #ef4444; color: #fff; }
.confirm-btn.ok:hover { background: #dc2626; }

@media (max-width: 820px) {
  .library-toolbar, .crawl-fields, .attach-bar { flex-wrap: wrap; }
  .library-layout { grid-template-columns: 1fr; }
  .folder-panel { border-right: 0; border-bottom: 1px solid var(--c-border); padding-right: 0; padding-bottom: 10px; min-height: auto; }
  .table-head { display: none; }
  .asset-row { grid-template-columns: 32px minmax(0, 1fr) auto; }
  .asset-source, .asset-kb { display: none; }
  .preview-head { align-items: flex-start; }
  .preview-actions { width: 100%; justify-content: flex-end; flex-wrap: wrap; }
  .preview-editor { min-height: 60vh; }
}
</style>
