<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { fetchKbs, updateKb, deleteKb as apiDeleteKb, getKb } from '../api'
import CreateKbModal from './CreateKbModal.vue'

const router = useRouter()
const kbSearch = ref('')
const kbs = ref([])
const viewMode = ref('list')
const showCreateModal = ref(false)

const showEditModal = ref(false)
const editId = ref('')
const editName = ref('')
const editDesc = ref('')

// Confirm dialog state
const showConfirmDialog = ref(false)
const confirmDialogTitle = ref('')
const confirmDialogMessage = ref('')
const confirmDialogConfirmText = ref('确定')
const confirmDialogCancelText = ref('取消')
const confirmDialogType = ref('warning') // 'warning', 'error', 'info'
let confirmDialogCallback = null

// Alert dialog state (info only, no cancel)
const showAlertDialog = ref(false)
const alertDialogTitle = ref('')
const alertDialogMessage = ref('')

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

function showAlert(title, message) {
  alertDialogTitle.value = title
  alertDialogMessage.value = message
  showAlertDialog.value = true
}

function confirmDialogOk() {
  showConfirmDialog.value = false
  confirmDialogCallback(true)
}

function confirmDialogCancel() {
  showConfirmDialog.value = false
  confirmDialogCallback(false)
}

function closeAlertDialog() {
  showAlertDialog.value = false
}

const filteredKbs = computed(() => {
  const q = kbSearch.value.toLowerCase().trim()
  if (!q) return kbs.value
  return kbs.value.filter(kb => kb.name.toLowerCase().includes(q) || (kb.description || '').toLowerCase().includes(q))
})

async function loadKbs() { try { kbs.value = await fetchKbs() } catch {} }

async function removeKb(kbId, e) {
  e && e.stopPropagation()
  const kb = kbs.value.find(k => k.id === kbId)
  
  // 先检查知识库是否有文件
  let hasFiles = false
  try {
    const kbDetail = await getKb(kbId)
    hasFiles = kbDetail.files && kbDetail.files.length > 0
  } catch {}
  
  if (hasFiles) {
    showAlert('提示', '该知识库中还有文件，请先删除所有文件后再删除知识库。')
    return
  }
  
  const confirmed = await showConfirm('删除知识库', `确认要删除「${kb?.name}」吗？`, '删除', '取消')
  if (!confirmed) return
  try { await apiDeleteKb(kbId) } catch {}
  await loadKbs()
}

function openEdit(kb, e) {
  e && e.stopPropagation()
  editId.value = kb.id
  editName.value = kb.name
  editDesc.value = kb.description || ''
  showEditModal.value = true
}

async function saveEdit() {
  if (!editName.value.trim()) return
  try {
    await updateKb(editId.value, { name: editName.value, description: editDesc.value })
    const kb = kbs.value.find(k => k.id === editId.value)
    if (kb) { kb.name = editName.value; kb.description = editDesc.value }
  } catch {}
  showEditModal.value = false
}

function onKbCreated(kbId) { showCreateModal.value = false; router.push('/kb/' + kbId) }

function normalizeType(type) {
  return String(type || '').toLowerCase()
}

function fileTypeMeta(type) {
  const normalized = normalizeType(type)
  const meta = {
    pdf: { label: 'PDF', color: '#ef4444', icon: 'pdf' },
    md: { label: 'MD', color: '#8b5cf6', icon: 'markdown' },
    doc: { label: 'DOC', color: '#3b82f6', icon: 'doc' },
    docx: { label: 'DOC', color: '#3b82f6', icon: 'doc' },
    txt: { label: 'TXT', color: '#94a3b8', icon: 'text' },
    csv: { label: 'CSV', color: '#10b981', icon: 'sheet' },
    json: { label: 'JSON', color: '#f97316', icon: 'code' },
    html: { label: 'HTML', color: '#ec4899', icon: 'code' },
  }
  return meta[normalized] || { label: (type || 'FILE').toUpperCase(), color: '#94a3b8', icon: 'file' }
}

function fileTypeIcon(type) {
  const icon = fileTypeMeta(type).icon
  const base = '<path d="M4 1.5h5l3 3V13a1.5 1.5 0 0 1-1.5 1.5h-6A1.5 1.5 0 0 1 3 13V3A1.5 1.5 0 0 1 4.5 1.5Z" stroke="currentColor" stroke-width="1.2"/><path d="M9 1.5V5h3" stroke="currentColor" stroke-width="1.2"/>'
  const icons = {
    pdf: `${base}<path d="M4.75 10.9h1.2c.75 0 1.15-.34 1.15-.95 0-.58-.4-.92-1.15-.92h-1.2v2.7Zm.85-1.94h.3c.26 0 .43.14.43.4 0 .29-.17.44-.43.44h-.3v-.84Z" fill="currentColor"/><path d="M7.8 11.73V9.03h1.05c.88 0 1.47.52 1.47 1.35 0 .83-.59 1.35-1.47 1.35H7.8Zm.85-.71h.17c.43 0 .77-.24.77-.64s-.34-.64-.77-.64h-.17v1.28Z" fill="currentColor"/>`,
    markdown: `${base}<path d="M4.7 11.1V8.8l.95 1.18.95-1.18v2.3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.8 8.95h2.35" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><path d="M9.95 8.2v1.5" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
    doc: `${base}<path d="M4.9 8.8 5.7 11l.8-2.2.8 2.2.8-2.2" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>`,
    sheet: `${base}<path d="M4.9 8.7h5.2M4.9 10.3h5.2M6.6 7.9v4M8.4 7.9v4" stroke="currentColor" stroke-width="1.05" stroke-linecap="round"/>`,
    code: `${base}<path d="m6.6 9.1-1.2 1.2 1.2 1.2M9.4 9.1l1.2 1.2-1.2 1.2" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>`,
    text: `${base}<path d="M5 8.9h5.2M5 10.4h5.2" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/>`,
    file: base,
  }
  return icons[icon] || icons.file
}

onMounted(loadKbs)
</script>

<template>
  <div>
    <!-- Toolbar -->
    <div class="toolbar">
      <div class="search-wrap">
        <svg class="search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="text" v-model="kbSearch" placeholder="搜索知识库...">
      </div>
      <button class="icon-btn refresh-btn" @click="loadKbs" title="刷新">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
      </button>
      <div class="view-toggle">
        <button :class="{ on: viewMode === 'list' }" @click="viewMode = 'list'">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg>
        </button>
        <button :class="{ on: viewMode === 'card' }" @click="viewMode = 'card'">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>
        </button>
      </div>
      <button class="btn primary" @click="showCreateModal = true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        新建
      </button>
    </div>

    <!-- === LIST VIEW === -->
    <template v-if="viewMode === 'list'">
      <div class="kb-list" v-if="filteredKbs.length">
        <div class="kb-row" v-for="kb in filteredKbs" :key="kb.id" @click="router.push('/kb/' + kb.id)">
          <div class="kb-icon-box">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
              <span class="kb-badge" v-if="kb.file_count">{{ kb.file_count }}</span>
              <span class="processing-indicator" v-if="kb.processing_files">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                  <circle class="spin" cx="12" cy="12" r="10"/>
                </svg>
              </span>
              <span v-if="kb.failed_files" class="failed-indicator">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="12" cy="12" r="10"/>
                </svg>
              </span>
              <span v-else-if="kb.pending_files" class="pending-indicator">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="12" cy="12" r="10"/>
                </svg>
              </span>
            </div>
            <div class="kb-body">
              <div class="kb-title">{{ kb.name }}</div>
              <div v-if="kb.processing_files || kb.pending_files || kb.failed_files" class="status-chips">
                <span v-if="kb.processing_files" class="status-chip processing">处理中</span>
                <span v-if="kb.pending_files" class="status-chip pending">未处理 {{ kb.pending_files }}</span>
                <span v-if="kb.failed_files" class="status-chip failed">失败 {{ kb.failed_files }}</span>
              </div>
            <div class="kb-meta">
              <span v-for="t in kb.file_types?.slice(0, 4)" :key="t" class="file-chip">
                <svg class="file-chip-icon" width="12" height="12" viewBox="0 0 16 16" fill="none" :style="{ color: fileTypeMeta(t).color }" v-html="fileTypeIcon(t)"></svg>
                <span class="file-chip-label">{{ fileTypeMeta(t).label }}</span>
              </span>
              <span class="kb-desc" v-if="kb.description">{{ kb.description }}</span>
            </div>
            <div class="processing-bar" v-if="kb.processing_files">
              <div class="processing-fill" :style="{ width: `${kb.overall_progress}%` }"></div>
            </div>
          </div>
          <div class="kb-hover-actions">
            <button class="icon-btn" @click="openEdit(kb, $event)" title="编辑">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
            </button>
            <button class="rm-btn" @click="removeKb(kb.id, $event)" title="删除">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </div>
      </div>
      <div class="empty-state" v-else>
        <div class="empty-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
        </div>
        <div class="empty-title">{{ kbSearch ? '没有匹配的知识库' : '暂无知识库' }}</div>
        <div class="empty-desc" v-if="!kbSearch">点击「新建」创建第一个知识库</div>
      </div>
    </template>

    <!-- === CARD VIEW === -->
    <template v-if="viewMode === 'card'">
      <div class="kb-cards" v-if="filteredKbs.length">
        <div class="kb-card" v-for="kb in filteredKbs" :key="kb.id" @click="router.push('/kb/' + kb.id)">
          <div class="card-top-row">
            <div class="card-icon-box">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
              <span class="card-badge" v-if="kb.file_count">{{ kb.file_count }}</span>
              <span class="card-processing-indicator" v-if="kb.processing_files">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                  <circle class="spin" cx="12" cy="12" r="10"/>
                </svg>
              </span>
              <span v-if="kb.failed_files" class="card-failed-indicator">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="12" cy="12" r="10"/>
                </svg>
              </span>
              <span v-else-if="kb.pending_files" class="card-pending-indicator">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                  <circle cx="12" cy="12" r="10"/>
                </svg>
              </span>
            </div>
            <div class="card-actions">
              <button class="icon-btn" @click="openEdit(kb, $event)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg></button>
              <button class="rm-btn" @click="removeKb(kb.id, $event)"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </div>
          </div>
          <div class="card-name">
            {{ kb.name }}
          </div>
          <div v-if="kb.processing_files || kb.pending_files || kb.failed_files" class="status-chips compact">
            <span v-if="kb.processing_files" class="status-chip processing">处理中</span>
            <span v-if="kb.pending_files" class="status-chip pending">未处理 {{ kb.pending_files }}</span>
            <span v-if="kb.failed_files" class="status-chip failed">失败 {{ kb.failed_files }}</span>
          </div>
          <div class="card-desc" v-if="kb.description">{{ kb.description }}</div>
          <div class="card-progress-bar" v-if="kb.processing_files">
            <div class="card-progress-fill" :style="{ width: `${kb.overall_progress}%` }"></div>
          </div>
          <div class="card-foot">
            <span>{{ kb.file_count }} 个文件</span>
            <div class="file-chip-row">
              <span v-for="t in kb.file_types?.slice(0, 3)" :key="t" class="file-chip">
                <svg class="file-chip-icon" width="12" height="12" viewBox="0 0 16 16" fill="none" :style="{ color: fileTypeMeta(t).color }" v-html="fileTypeIcon(t)"></svg>
                <span class="file-chip-label">{{ fileTypeMeta(t).label }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
      <div class="empty-state" v-else>
        <div class="empty-icon"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg></div>
        <div class="empty-title">{{ kbSearch ? '没有匹配的知识库' : '暂无知识库' }}</div>
        <div class="empty-desc" v-if="!kbSearch">点击「新建」创建第一个知识库</div>
      </div>
    </template>

    <!-- Edit Modal -->
    <div class="modal-mask" v-if="showEditModal" @click.self="showEditModal = false">
      <div class="modal" @click.stop>
        <h3>编辑知识库</h3>
        <div class="field"><label>名称</label><input type="text" v-model="editName" placeholder="知识库名称" @keydown.enter="saveEdit" autofocus></div>
        <div class="field"><label>描述</label><textarea v-model="editDesc" placeholder="简要描述知识库内容" rows="3"></textarea></div>
        <div class="modal-btns"><button class="btn" @click="showEditModal = false">取消</button><button class="btn primary" @click="saveEdit" :disabled="!editName.trim()">保存</button></div>
      </div>
    </div>

    <div class="modal-mask" v-if="showConfirmDialog" @click.self="confirmDialogCancel">
      <div class="modal confirm-modal" @click.stop>
        <div :class="['confirm-icon', confirmDialogType]">
          <svg v-if="confirmDialogType === 'error'" width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2L2 20h20L12 2zm0 15l-.5-6h1l-.5 6zm0-8l-.5-3h1l-.5 3z"/>
          </svg>
          <svg v-else width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2L2 20h20L12 2zm0 15l-.5-6h1l-.5 6zm0-8l-.5-3h1l-.5 3z"/>
          </svg>
        </div>
        <div class="confirm-title">{{ confirmDialogTitle }}</div>
        <div class="confirm-message">{{ confirmDialogMessage }}</div>
        <div class="confirm-actions">
          <button class="confirm-btn cancel" @click="confirmDialogCancel">{{ confirmDialogCancelText }}</button>
          <button class="confirm-btn ok" @click="confirmDialogOk">{{ confirmDialogConfirmText }}</button>
        </div>
      </div>
    </div>

    <!-- Alert Dialog -->
    <div class="modal-mask" v-if="showAlertDialog" @click.self="closeAlertDialog">
      <div class="modal alert-modal" @click.stop>
        <div class="alert-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2L2 20h20L12 2zm0 15l-.5-6h1l-.5 6zm0-8l-.5-3h1l-.5 3z"/>
          </svg>
        </div>
        <div class="alert-title">{{ alertDialogTitle }}</div>
        <div class="alert-message">{{ alertDialogMessage }}</div>
        <div class="alert-actions">
          <button class="alert-btn cancel" @click="closeAlertDialog">取消</button>
          <button class="alert-btn ok" @click="closeAlertDialog">确定</button>
        </div>
      </div>
    </div>

    <CreateKbModal v-if="showCreateModal" @close="showCreateModal = false" @created="onKbCreated" />
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 20px; align-items: center; }
.search-wrap { flex: 1; position: relative; }
.search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--c-secondary); pointer-events: none; }
.search-wrap input { padding-left: 34px; }

.view-toggle { display: flex; border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; }
.view-toggle button { background: none; border: none; padding: 7px 9px; cursor: pointer; color: var(--c-secondary); display: flex; transition: all 150ms; }
.view-toggle button.on { background: var(--c-fg); color: var(--c-bg); }
.view-toggle button:hover:not(.on) { background: var(--c-muted); color: var(--c-fg); }

/* List */
.kb-list { display: flex; flex-direction: column; gap: 2px; }
.kb-row { display: flex; align-items: center; gap: 14px; padding: 14px 16px; border-radius: var(--radius); cursor: pointer; transition: background 150ms; border: 1px solid transparent; }
.kb-row:hover { background: var(--c-muted); border-color: var(--c-border); }
.kb-icon-box { position: relative; width: 40px; height: 40px; border-radius: 10px; background: var(--c-muted); display: flex; align-items: center; justify-content: center; color: var(--c-secondary); flex-shrink: 0; }
.kb-badge { position: absolute; top: -4px; right: -6px; background: var(--c-fg); color: var(--c-bg); font-size: 10px; font-weight: 700; min-width: 17px; height: 17px; border-radius: 9px; display: flex; align-items: center; justify-content: center; padding: 0 5px; }
.kb-body { flex: 1; min-width: 0; }
.kb-title { font-size: 14px; font-weight: 600; line-height: 1.35; }
.status-chips { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 4px; margin-bottom: 4px; }
.status-chip { display: inline-flex; align-items: center; min-height: 20px; padding: 0 8px; border-radius: 999px; font-size: 11px; font-weight: 600; border: 1px solid transparent; }
.status-chip.processing { color: #4ade80; background: rgba(74, 222, 128, 0.12); border-color: rgba(74, 222, 128, 0.24); }
.status-chip.pending { color: #fbbf24; background: rgba(251, 191, 36, 0.12); border-color: rgba(251, 191, 36, 0.24); }
.status-chip.failed { color: #f87171; background: rgba(239, 68, 68, 0.12); border-color: rgba(239, 68, 68, 0.24); }
.kb-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.kb-desc { font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 240px; }
.kb-hover-actions { display: flex; gap: 2px; opacity: 0; transition: opacity 150ms; flex-shrink: 0; }
.kb-row:hover .kb-hover-actions { opacity: 1; }

.processing-indicator {
  position: absolute;
  bottom: -2px;
  right: -2px;
  color: #4ade80;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.spin {
  animation: spin 1s linear infinite;
  stroke: currentColor;
  stroke-width: 3;
  fill: none;
}

.pending-indicator {
  position: absolute;
  top: -2px;
  left: -2px;
  color: #fbbf24;
}

.failed-indicator {
  position: absolute;
  top: -2px;
  left: -2px;
  color: #ef4444;
}

.processing-bar {
  height: 4px;
  background: var(--c-muted);
  border-radius: 2px;
  margin-top: 6px;
  overflow: hidden;
}

.processing-fill {
  height: 100%;
  background: linear-gradient(90deg, #4ade80, #22c55e);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.file-chip-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.file-chip { display: inline-flex; align-items: center; gap: 5px; min-height: 22px; padding: 0 7px; border-radius: 999px; background: rgba(148, 163, 184, 0.08); border: 1px solid rgba(148, 163, 184, 0.12); color: var(--c-secondary); }
.file-chip-icon { flex-shrink: 0; }
.file-chip-label { font-size: 10px; font-weight: 700; letter-spacing: 0; text-transform: uppercase; color: var(--c-fg); }

.icon-btn { background: none; border: none; cursor: pointer; color: var(--c-secondary); padding: 5px; border-radius: 6px; display: flex; transition: all 150ms; }
.icon-btn:hover { color: var(--c-fg); background: var(--c-muted); }
.icon-btn.danger:hover { color: #ef4444; background: #fef2f2; }

.rm-btn { background: #ef4444; color: #fff; cursor: pointer; padding: 6px 12px; border-radius: 6px; display: flex; align-items: center; justify-content: center; transition: all 150ms; flex-shrink: 0; border: none; font-size: 13px; font-weight: 600; }
.rm-btn:hover { background: #dc2626; }

/* Confirm Dialog */
.confirm-modal {
  width: 360px;
  max-width: 90vw;
  padding: 24px;
  text-align: center;
}

.confirm-icon {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(239,68,68,0.1);
  color: #ef4444;
}

.confirm-icon.warning {
  background: rgba(251, 191, 36, 0.1);
  color: #fbbf24;
}

.confirm-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--c-fg);
  margin-bottom: 8px;
}

.confirm-message {
  font-size: 13px;
  color: var(--c-secondary);
  line-height: 1.5;
  margin-bottom: 20px;
}

.confirm-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}

.confirm-btn {
  padding: 10px 24px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 150ms;
  border: none;
}

.confirm-btn.cancel {
  background: var(--c-muted);
  color: var(--c-secondary);
}

.confirm-btn.cancel:hover {
  background: var(--c-border);
  color: var(--c-fg);
}

.confirm-btn.ok {
  background: #ef4444;
  color: #fff;
}

.confirm-btn.ok:hover {
  background: #dc2626;
}

/* Alert Dialog */
.alert-modal {
  width: 360px;
  max-width: 90vw;
  padding: 24px;
  text-align: center;
}

.alert-icon {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(251, 191, 36, 0.15);
  color: #fbbf24;
}

.alert-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--c-fg);
  margin-bottom: 8px;
}

.alert-message {
  font-size: 13px;
  color: var(--c-secondary);
  line-height: 1.5;
  margin-bottom: 20px;
}

.alert-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
}

.alert-btn {
  padding: 10px 24px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 150ms;
  border: none;
}

.alert-btn.cancel {
  background: var(--c-muted);
  color: var(--c-fg);
}

.alert-btn.cancel:hover {
  background: var(--c-border);
}

.alert-btn.ok {
  background: #ef4444;
  color: #fff;
}

.alert-btn.ok:hover {
  background: #dc2626;
}

/* Cards */
.kb-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.kb-card { border: 1px solid var(--c-border); border-radius: var(--radius); padding: 18px; cursor: pointer; transition: all 200ms; display: flex; flex-direction: column; gap: 10px; }
.kb-card:hover { border-color: #aaa; box-shadow: 0 2px 12px rgba(0,0,0,0.05); transform: translateY(-1px); }
.card-top-row { display: flex; justify-content: space-between; align-items: center; color: var(--c-secondary); }
.card-actions { display: flex; gap: 2px; opacity: 0; transition: opacity 150ms; }
.kb-card:hover .card-actions { opacity: 1; }
.card-name { font-size: 15px; font-weight: 600; line-height: 1.35; }
.card-desc { font-size: 12px; color: var(--c-secondary); line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.card-foot { display: flex; justify-content: space-between; align-items: center; margin-top: auto; padding-top: 8px; border-top: 1px solid var(--c-border); font-size: 11px; color: var(--c-secondary); gap: 6px; flex-wrap: wrap; }

.card-icon-box { position: relative; width: 40px; height: 40px; border-radius: 10px; background: var(--c-muted); display: flex; align-items: center; justify-content: center; }
.card-badge { position: absolute; top: -4px; right: -6px; background: var(--c-fg); color: var(--c-bg); font-size: 10px; font-weight: 700; min-width: 17px; height: 17px; border-radius: 9px; display: flex; align-items: center; justify-content: center; padding: 0 5px; }
.card-processing-indicator { position: absolute; bottom: -2px; right: -2px; color: #4ade80; }
.card-pending-indicator { position: absolute; top: -2px; left: -2px; color: #fbbf24; }
.card-failed-indicator { position: absolute; top: -2px; left: -2px; color: #ef4444; }
.card-progress-bar { height: 4px; background: var(--c-muted); border-radius: 2px; overflow: hidden; margin-top: 4px; }
.card-progress-fill { height: 100%; background: linear-gradient(90deg, #4ade80, #22c55e); border-radius: 2px; transition: width 0.3s ease; }
.status-chips.compact { margin-top: -2px; margin-bottom: 0; }

/* Empty */
.empty-icon { color: var(--c-border); margin-bottom: 10px; }

/* Modal */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 200; }
.modal { background: var(--c-bg); border-radius: 12px; padding: 24px; width: 420px; max-width: 90vw; box-shadow: 0 12px 40px rgba(0,0,0,0.15); }
h3 { font-size: 16px; font-weight: 700; margin-bottom: 18px; }
.field { margin-bottom: 14px; }
.field label { display: block; font-size: 12px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
.field input, .field textarea { width: 100%; padding: 9px 12px; font-size: 14px; font-family: var(--font); border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-bg); color: var(--c-fg); outline: none; transition: border-color 150ms; resize: vertical; }
.field input:focus, .field textarea:focus { border-color: var(--c-fg); }
.modal-btns { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
