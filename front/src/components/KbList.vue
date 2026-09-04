<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchKbs, updateKb, deleteKb as apiDeleteKb, getKb } from '../api'
import CreateKbModal from './CreateKbModal.vue'
import SearchableSelect from './common/SearchableSelect.vue'
import Pagination from './common/Pagination.vue'

const router = useRouter()
const kbSearch = ref('')
const statusFilter = ref('')

const statusOptions = [
  { value: '', label: '全部状态' },
  { value: 'ready', label: '已就绪' },
  { value: 'processing', label: '处理中' },
  { value: 'pending', label: '待处理' },
  { value: 'failed', label: '异常' },
]
const kbs = ref([])
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

// 知识库状态：处理中 > 异常 > 待处理 > 已就绪 > 空
function kbStatus(kb) {
  if (kb.processing_files) return 'processing'
  if (kb.failed_files) return 'failed'
  if (kb.pending_files) return 'pending'
  if (kb.file_count) return 'ready'
  return 'empty'
}

const STATUS_LABEL = { ready: '已就绪', processing: '处理中', failed: '异常', pending: '待处理', empty: '空' }
const STATUS_CHIP = { ready: 'ok', processing: 'warn', failed: 'err', pending: 'plain', empty: 'plain' }

function statusLabel(kb) { return STATUS_LABEL[kbStatus(kb)] || '—' }
function statusChip(kb) { return STATUS_CHIP[kbStatus(kb)] || 'plain' }

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function fmtNum(n) {
  return n == null ? 0 : n
}

const filteredKbs = computed(() => {
  const q = kbSearch.value.toLowerCase().trim()
  return kbs.value.filter(kb => {
    if (q && !(kb.name.toLowerCase().includes(q) || (kb.description || '').toLowerCase().includes(q))) return false
    if (statusFilter.value && kbStatus(kb) !== statusFilter.value) return false
    return true
  })
})

const page = ref(1)
const pageSize = ref(10)
const pagedKbs = computed(() =>
  filteredKbs.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value)
)
watch([kbSearch, statusFilter], () => { page.value = 1 })

const stats = computed(() => {
  const sum = fn => kbs.value.reduce((a, kb) => a + (fmtNum(fn(kb)) || 0), 0)
  return {
    total: kbs.value.length,
    files: sum(kb => kb.file_count),
    chunks: sum(kb => kb.chunk_count),
    processing: sum(kb => kb.processing_files),
    failed: sum(kb => kb.failed_files),
  }
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

function goDetail(kbId) { router.push('/kb/' + kbId) }

onMounted(loadKbs)
</script>

<template>
  <div class="kb-page">
    <!-- 页头 -->
    <div class="page-head">
      <div>
        <div class="page-title">知识库</div>
        <div class="page-desc">将文件组织为知识库，完成分片、向量索引与关系抽取</div>
      </div>
      <div class="page-actions">
        <button class="btn primary" @click="showCreateModal = true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          新建知识库
        </button>
      </div>
    </div>

    <!-- 统计卡 -->
    <div class="stats">
      <div class="stat-card">
        <div class="stat-label">知识库</div>
        <div class="stat-value">{{ stats.total }}</div>
        <div class="stat-trend">全部知识库</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">文件总数</div>
        <div class="stat-value">{{ stats.files }}</div>
        <div class="stat-trend">已上传文件</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">分片</div>
        <div class="stat-value">{{ stats.chunks }}</div>
        <div class="stat-trend">分片总数</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">处理中</div>
        <div class="stat-value">{{ stats.processing }}</div>
        <div class="stat-trend warn">正在处理</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">失败</div>
        <div class="stat-value">{{ stats.failed }}</div>
        <div class="stat-trend err">处理失败</div>
      </div>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <svg class="filter-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input class="filter-input" type="text" v-model="kbSearch" placeholder="搜索知识库名称...">
      <div class="filter-select-wrap">
        <SearchableSelect v-model="statusFilter" :options="statusOptions" placeholder="全部状态" />
      </div>
      <span class="filter-count">共 {{ filteredKbs.length }} 个知识库</span>
    </div>

    <!-- 表格 -->
    <div class="table-card">
      <table>
        <thead>
          <tr>
            <th>知识库</th>
            <th style="width:80px">文件</th>
            <th style="width:90px">分片</th>
            <th style="width:100px">状态</th>
            <th style="width:160px">更新时间</th>
            <th style="width:170px">操作</th>
          </tr>
        </thead>
        <tbody v-if="pagedKbs.length">
          <tr v-for="kb in pagedKbs" :key="kb.id" @click="goDetail(kb.id)">
            <td>
              <div class="kb-name">
                <span class="kb-icon">{{ (kb.name || 'K').charAt(0) }}</span>
                <div class="kb-name-body">
                  <div class="kb-title">{{ kb.name }}</div>
                  <div class="kb-sub" v-if="kb.description">{{ kb.description }}</div>
                </div>
              </div>
            </td>
            <td class="num">{{ fmtNum(kb.file_count) }}</td>
            <td class="num">{{ fmtNum(kb.chunk_count) }}</td>
            <td><span class="chip" :class="statusChip(kb)">{{ statusLabel(kb) }}</span></td>
            <td class="time">{{ fmtTime(kb.updated_at || kb.created_at) }}</td>
            <td>
              <button class="btn link" @click.stop="goDetail(kb.id)">详情</button>
              <button class="btn link" @click.stop="openEdit(kb, $event)">编辑</button>
              <button class="btn link danger" @click.stop="removeKb(kb.id, $event)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <Pagination v-if="filteredKbs.length > pageSize" v-model:page="page" v-model:page-size="pageSize" :total="filteredKbs.length" />
      <div v-if="!filteredKbs.length" class="table-empty">
        <div class="empty-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/></svg>
        </div>
        <div class="empty-title">{{ kbSearch || statusFilter ? '没有匹配的知识库' : '暂无知识库' }}</div>
        <div class="empty-desc" v-if="!kbSearch && !statusFilter">点击「新建知识库」创建第一个知识库</div>
      </div>
    </div>

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
          <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
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
.kb-page { display: flex; flex-direction: column; gap: 16px; }

/* 页头 */
.page-head { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 2px; }
.page-title { font-size: 19px; font-weight: 700; letter-spacing: -0.2px; color: var(--c-fg); }
.page-desc { font-size: 12.5px; color: var(--c-secondary); margin-top: 4px; }
.page-actions { margin-left: auto; display: flex; gap: 8px; }

/* 统计卡 */
.stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }
.stat-card {
  background: var(--c-panel); border: 1px solid var(--c-border);
  border-radius: var(--radius); padding: 16px;
}
.stat-label { font-size: 12px; color: var(--c-secondary); }
.stat-value { font-size: 26px; font-weight: 700; margin-top: 8px; color: var(--c-fg); letter-spacing: -0.5px; }
.stat-trend { font-size: 11px; margin-top: 6px; color: var(--c-secondary); }
.stat-trend.warn { color: #d97706; }
.stat-trend.err { color: var(--c-danger); }

/* 筛选栏 */
.filter-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 14px;
  background: var(--c-panel); border: 1px solid var(--c-border);
  border-radius: var(--radius);
}
.filter-search-icon { color: var(--c-secondary); flex-shrink: 0; }
.filter-input {
  width: 220px; height: 38px; padding: 0 12px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  font-size: 12.5px; font-family: var(--font); color: var(--c-fg); background: var(--c-panel);
  outline: none;
}
.filter-input:focus { border-color: var(--c-fg); }
.filter-input::placeholder { color: var(--c-secondary); opacity: 0.8; }
.filter-select-wrap { width: 170px; flex-shrink: 0; }
.filter-count { font-size: 12px; color: var(--c-secondary); margin-left: auto; }

/* 表格 */
.table-card {
  background: var(--c-panel); border: 1px solid var(--c-border);
  border-radius: var(--radius); overflow: hidden;
}
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  text-align: left; padding: 10px 14px;
  font-size: 12px; font-weight: 600; color: var(--c-secondary);
  background: var(--c-muted); border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
}
tbody td { padding: 12px 14px; border-bottom: 1px solid var(--c-border); vertical-align: middle; }
tbody tr:last-child td { border-bottom: none; }
tbody tr { cursor: pointer; transition: background 120ms; }
tbody tr:hover { background: var(--c-muted); }

.kb-name { display: flex; align-items: center; gap: 10px; font-weight: 600; color: var(--c-fg); }
.kb-icon {
  width: 30px; height: 30px; border-radius: var(--radius-sm);
  display: flex; align-items: center; justify-content: center;
  background: var(--c-accent-weak); color: var(--c-accent);
  font-size: 13px; font-weight: 700; flex-shrink: 0;
}
.kb-name-body { min-width: 0; }
.kb-title { font-size: 13px; font-weight: 600; }
.kb-sub { font-size: 11.5px; color: var(--c-secondary); font-weight: 400; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 340px; }

.num { font-size: 12.5px; color: var(--c-secondary); }
.time { font-size: 12px; color: var(--c-secondary); white-space: nowrap; }

/* 状态标签 */
.chip {
  display: inline-flex; align-items: center; gap: 5px;
  height: 22px; padding: 0 8px; border-radius: 3px;
  font-size: 11.5px; font-weight: 500; white-space: nowrap;
}
.chip::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.chip.ok { color: var(--c-success); background: rgba(34, 197, 94, 0.12); }
.chip.warn { color: #d97706; background: rgba(217, 119, 6, 0.12); }
.chip.err { color: var(--c-danger); background: rgba(239, 68, 68, 0.12); }
.chip.plain { color: var(--c-secondary); background: var(--c-muted); }
.chip.plain::before { opacity: 0.5; }

/* 链接按钮 */
.btn.link { border: none; background: none; color: var(--c-accent); padding: 0 4px; height: 24px; cursor: pointer; font-size: 12.5px; font-family: var(--font); }
.btn.link:hover { opacity: 0.8; }
.btn.link.danger { color: var(--c-danger); }

/* 空状态 */
.table-empty { padding: 48px 20px; text-align: center; color: var(--c-secondary); }
.empty-icon { color: var(--c-border); margin-bottom: 10px; }
.empty-title { font-size: 14px; font-weight: 700; color: var(--c-fg); }
.empty-desc { font-size: 12px; margin-top: 4px; }

/* 弹窗 */
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 200; }
.modal { background: var(--c-panel); border-radius: 12px; padding: 24px; width: 420px; max-width: 90vw; box-shadow: 0 12px 40px rgba(0,0,0,0.15); border: 1px solid var(--c-border); }
h3 { font-size: 16px; font-weight: 700; margin-bottom: 18px; color: var(--c-fg); }
.field { margin-bottom: 14px; }
.field label { display: block; font-size: 12px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; }
.field input, .field textarea { width: 100%; padding: 9px 12px; font-size: 14px; font-family: var(--font); border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-panel); color: var(--c-fg); outline: none; transition: border-color 150ms; resize: vertical; }
.field input:focus, .field textarea:focus { border-color: var(--c-fg); }
.modal-btns { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }

.confirm-modal, .alert-modal { width: 360px; max-width: 90vw; text-align: center; }
.confirm-icon, .alert-icon {
  width: 56px; height: 56px; margin: 0 auto 16px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%;
}
.confirm-icon { background: rgba(239,68,68,0.1); color: #ef4444; }
.confirm-icon.warning { background: rgba(251, 191, 36, 0.1); color: #fbbf24; }
.alert-icon { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
.confirm-title, .alert-title { font-size: 16px; font-weight: 700; color: var(--c-fg); margin-bottom: 8px; }
.confirm-message, .alert-message { font-size: 13px; color: var(--c-secondary); line-height: 1.5; margin-bottom: 20px; }
.confirm-actions, .alert-actions { display: flex; gap: 10px; justify-content: center; }
.confirm-btn, .alert-btn { padding: 10px 24px; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 150ms; border: none; }
.confirm-btn.cancel, .alert-btn.cancel { background: var(--c-muted); color: var(--c-secondary); }
.confirm-btn.cancel:hover, .alert-btn.cancel:hover { background: var(--c-border); color: var(--c-fg); }
.confirm-btn.ok, .alert-btn.ok { background: #ef4444; color: #fff; }
.confirm-btn.ok:hover, .alert-btn.ok:hover { background: #dc2626; }

@media (max-width: 1100px) { .stats { grid-template-columns: repeat(3, 1fr); } }
</style>
