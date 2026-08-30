<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  fetchAttributeTemplates,
  getAttributeTemplate,
  createAttributeTemplate,
  updateAttributeTemplate,
  deleteAttributeTemplate,
  replaceTemplateAttributes,
  exportOntologyExcel,
  triggerDownload,
} from '../../api'
import ModalDialog from '../common/ModalDialog.vue'
import AttributeEditor from '../common/AttributeEditor.vue'
import ExcelImportExport from './ExcelImportExport.vue'

const search = ref('')
const templates = ref([])
const loading = ref(false)
const expandedId = ref(null)
// 展开时加载的详情缓存 { id: { attributes, ... } }
const details = ref({})

// 新建
const showCreate = ref(false)
const newName = ref('')
const newDesc = ref('')
const creating = ref(false)

// 编辑
const showEdit = ref(false)
const editId = ref('')
const editName = ref('')
const editDesc = ref('')
const saving = ref(false)

// 删除
const showDelete = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return templates.value
  return templates.value.filter(t =>
    t.name.toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q)
  )
})

// ===== 模板多选导出 =====
const selectedTemplateIds = ref(new Set())
const exportingSelected = ref(false)
const allFilteredSelected = computed(() => filtered.value.length > 0 && filtered.value.every(t => selectedTemplateIds.value.has(t.id)))
const someFilteredSelected = computed(() => filtered.value.some(t => selectedTemplateIds.value.has(t.id)) && !allFilteredSelected.value)

function toggleSelectTemplate(id) {
  const set = selectedTemplateIds.value
  if (set.has(id)) set.delete(id)
  else set.add(id)
  selectedTemplateIds.value = new Set(set)
}

function toggleSelectAllFiltered() {
  if (allFilteredSelected.value) {
    filtered.value.forEach(t => selectedTemplateIds.value.delete(t.id))
  } else {
    filtered.value.forEach(t => selectedTemplateIds.value.add(t.id))
  }
  selectedTemplateIds.value = new Set(selectedTemplateIds.value)
}

async function onExportSelected() {
  const ids = Array.from(selectedTemplateIds.value)
  if (!ids.length) return
  exportingSelected.value = true
  try {
    const blob = await exportOntologyExcel({ scope: 'templates', templateIds: ids })
    triggerDownload(blob, `本体导出-本体模板-${ids.length}个模板.xlsx`)
  } catch (e) {
    alert('导出失败：' + (e.message || '未知错误'))
  } finally {
    exportingSelected.value = false
  }
}

async function load() {
  loading.value = true
  try {
    templates.value = await fetchAttributeTemplates()
  } catch {
    templates.value = []
  } finally {
    loading.value = false
  }
}

async function toggleExpand(t) {
  if (expandedId.value === t.id) {
    expandedId.value = null
    return
  }
  expandedId.value = t.id
  if (!details.value[t.id]) {
    await loadDetail(t.id)
  }
}

async function loadDetail(id) {
  try {
    const data = await getAttributeTemplate(id)
    details.value[id] = data
  } catch (e) {
    details.value[id] = { error: e.message }
  }
}

// 属性保存后刷新详情
async function saveAttributes(t, payload) {
  const result = await replaceTemplateAttributes(t.id, payload)
  await loadDetail(t.id)
  await load()
  return result
}

function openCreate() {
  newName.value = ''
  newDesc.value = ''
  showCreate.value = true
}

async function submitCreate() {
  if (!newName.value.trim()) return
  creating.value = true
  try {
    await createAttributeTemplate({ name: newName.value.trim(), description: newDesc.value.trim() })
    showCreate.value = false
    await load()
  } catch (e) {
    alert('创建失败：' + e.message)
  } finally {
    creating.value = false
  }
}

function openEdit(t) {
  editId.value = t.id
  editName.value = t.name
  editDesc.value = t.description || ''
  showEdit.value = true
}

async function submitEdit() {
  if (!editName.value.trim()) return
  saving.value = true
  try {
    await updateAttributeTemplate(editId.value, { name: editName.value.trim(), description: editDesc.value.trim() })
    showEdit.value = false
    await load()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

function askDelete(t) {
  if (t.is_system) {
    alert('系统内置模板不可删除')
    return
  }
  deleteTarget.value = t
  showDelete.value = true
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await deleteAttributeTemplate(deleteTarget.value.id)
    showDelete.value = false
    if (expandedId.value === deleteTarget.value.id) expandedId.value = null
    deleteTarget.value = null
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  } finally {
    deleting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">属性模板</h2>
        <span class="page-subtitle">跨领域复用的全局属性模板，本体可引用以合并属性</span>
      </div>
    </div>

    <div class="toolbar">
      <label class="tpl-checkbox" title="全选当前列表">
        <input type="checkbox" :checked="allFilteredSelected" :indeterminate.prop="someFilteredSelected" @change="toggleSelectAllFiltered">
      </label>
      <div class="search-wrap">
        <svg class="search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="text" v-model="search" placeholder="搜索属性模板...">
      </div>
      <button class="icon-btn refresh-btn" @click="load" title="刷新">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
      </button>
      <ExcelImportExport scope="templates" @success="load" />
      <button class="btn" @click="onExportSelected" :disabled="exportingSelected || !selectedTemplateIds.size" title="导出选中模板">
        <span v-if="exportingSelected" class="spinner xs"></span>
        <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        {{ exportingSelected ? '导出中...' : `导出选中 (${selectedTemplateIds.size})` }}
      </button>
      <button class="btn primary" @click="openCreate">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        新建
      </button>
    </div>

    <div v-if="filtered.length" class="tpl-list">
      <div
        v-for="t in filtered"
        :key="t.id"
        class="tpl-card"
        :class="{ expanded: expandedId === t.id }"
      >
        <div class="tpl-card-head" @click="toggleExpand(t)">
          <label class="tpl-checkbox" @click.stop>
            <input type="checkbox" :checked="selectedTemplateIds.has(t.id)" @change="toggleSelectTemplate(t.id)">
          </label>
          <svg class="tpl-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="17.5" height="17.5" rx="2.25"/><path d="M3.25 9.25h17.5"/><path d="M9.25 9.25v11.5"/><path d="M15.25 9.25v11.5"/></svg>
          <span class="tpl-name">{{ t.name }}</span>
          <span v-if="t.is_system" class="tag system">系统</span>
          <span class="tpl-count">{{ t.attribute_count }} 属性</span>
          <span class="tpl-spacer"></span>
          <span class="tpl-hover-actions">
            <button class="icon-btn sm" @click.stop="openEdit(t)" title="编辑">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
            </button>
            <button class="rm-btn sm" @click.stop="askDelete(t)" title="删除" :disabled="t.is_system">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </span>
          <svg class="tpl-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </div>
        <div v-if="t.description && expandedId !== t.id" class="tpl-desc-preview">{{ t.description }}</div>

        <div v-if="expandedId === t.id" class="tpl-card-body">
          <div v-if="t.description" class="tpl-desc">{{ t.description }}</div>
          <div v-if="details[t.id]?.error" class="tpl-error">{{ details[t.id].error }}</div>
          <div v-else-if="details[t.id]" class="tpl-attrs">
            <AttributeEditor
              :attributes="details[t.id].attributes || []"
              :save-fn="(payload) => saveAttributes(t, payload)"
              title="模板属性"
            />
          </div>
          <div v-else class="tpl-loading"><span class="spinner"></span> 加载中...</div>
        </div>
      </div>
    </div>

    <div class="empty-state" v-else-if="!loading">
      <div class="empty-icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="17.5" height="17.5" rx="2.25"/><path d="M3.25 9.25h17.5"/><path d="M9.25 9.25v11.5"/><path d="M15.25 9.25v11.5"/></svg>
      </div>
      <div class="empty-title">{{ search ? '没有匹配的属性模板' : '暂无属性模板' }}</div>
      <div class="empty-desc" v-if="!search">点击「新建」创建第一个属性模板</div>
    </div>

    <!-- 新建弹窗 -->
    <ModalDialog
      v-model="showCreate"
      title="新建属性模板"
      size="sm"
      :confirm-text="creating ? '创建中...' : '创建'"
      :confirm-loading="creating"
      :confirm-disabled="!newName.trim()"
      @confirm="submitCreate"
    >
      <div class="field">
        <label>名称</label>
        <input type="text" v-model="newName" placeholder="例如：基础实体属性" @keydown.enter="submitCreate">
      </div>
      <div class="field">
        <label>描述（可选）</label>
        <textarea v-model="newDesc" rows="3" placeholder="该模板覆盖的共性属性说明..."></textarea>
      </div>
    </ModalDialog>

    <!-- 编辑弹窗 -->
    <ModalDialog
      v-model="showEdit"
      title="编辑属性模板"
      size="sm"
      :confirm-text="saving ? '保存中...' : '保存'"
      :confirm-loading="saving"
      :confirm-disabled="!editName.trim()"
      @confirm="submitEdit"
    >
      <div class="field">
        <label>名称</label>
        <input type="text" v-model="editName" placeholder="模板名称">
      </div>
      <div class="field">
        <label>描述（可选）</label>
        <textarea v-model="editDesc" rows="3" placeholder="该模板覆盖的共性属性说明..."></textarea>
      </div>
    </ModalDialog>

    <!-- 删除确认 -->
    <ModalDialog
      v-model="showDelete"
      title="删除属性模板"
      size="sm"
      confirm-text="删除"
      confirm-variant="danger"
      :confirm-loading="deleting"
      @confirm="confirmDelete"
    >
      <p class="confirm-text">确认要删除「<strong>{{ deleteTarget?.name }}</strong>」吗？</p>
      <p class="confirm-warn">所有本体对该模板的引用将被解除，该操作不可恢复。</p>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }

.toolbar { display: flex; align-items: center; gap: 10px; }
.search-wrap { flex: 1; display: flex; align-items: center; gap: 8px; padding: 0 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); height: 38px; }
.search-wrap:focus-within { border-color: var(--c-fg); }
.search-icon { color: var(--c-secondary); flex-shrink: 0; }
.search-wrap input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--c-fg); font-size: 14px; font-family: var(--font); }
.search-wrap input::placeholder { color: var(--c-secondary); opacity: 0.7; }
.icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 38px; height: 38px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; transition: background 150ms, color 150ms; }
.icon-btn:hover { background: var(--c-muted); color: var(--c-fg); }
.icon-btn.sm { width: 28px; height: 28px; border: 0; background: transparent; }
.tpl-checkbox { display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; width: 20px; height: 20px; cursor: pointer; }
.tpl-checkbox input[type="checkbox"] { width: 14px; height: 14px; accent-color: var(--c-accent); cursor: pointer; }
.toolbar .tpl-checkbox { margin-right: -4px; }

.tpl-list { display: flex; flex-direction: column; gap: 8px; }
.tpl-card { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); overflow: hidden; transition: border-color 150ms; }
.tpl-card.expanded { border-color: var(--c-fg); }
.tpl-card-head { display: flex; align-items: center; gap: 10px; padding: 12px 16px; cursor: pointer; user-select: none; }
.tpl-card-head:hover { background: var(--c-muted); }
.tpl-icon { color: var(--c-accent); flex-shrink: 0; }
.tpl-name { font-size: 14px; font-weight: 600; color: var(--c-fg); }
.tag { display: inline-flex; padding: 1px 7px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.tag.system { background: rgba(161, 98, 7, 0.12); color: var(--c-accent); }
.tpl-count { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); }
.tpl-spacer { flex: 1; }
.tpl-hover-actions { display: inline-flex; align-items: center; gap: 4px; opacity: 0; transition: opacity 150ms; }
.tpl-card-head:hover .tpl-hover-actions { opacity: 1; }
.rm-btn.sm { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; }
.rm-btn.sm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.rm-btn.sm:disabled { opacity: 0.3; cursor: not-allowed; }
.tpl-caret { color: var(--c-secondary); transition: transform 180ms ease; }
.tpl-card.expanded .tpl-caret { transform: rotate(180deg); }

.tpl-desc-preview { padding: 0 16px 10px 44px; font-size: 12px; color: var(--c-secondary); }

.tpl-card-body { padding: 14px 18px; border-top: 1px solid var(--c-border); display: flex; flex-direction: column; gap: 12px; }
.tpl-desc { font-size: 13px; color: var(--c-secondary); }
.tpl-error { color: var(--c-danger); font-size: 13px; }
.tpl-loading { padding: 20px; text-align: center; color: var(--c-secondary); font-size: 13px; }

.empty-state { text-align: center; padding: 48px 20px; color: var(--c-secondary); }
.empty-state .empty-icon { margin-bottom: 12px; color: var(--c-border); }
.empty-state .empty-title { font-size: 15px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.empty-state .empty-desc { font-size: 13px; }

.field { margin-bottom: 14px; }
.field:last-child { margin-bottom: 0; }
.field label { display: block; font-size: 13px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; }
.field input, .field textarea { width: 100%; padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 14px; font-family: var(--font); outline: none; transition: border-color 150ms; resize: vertical; }
.field input:focus, .field textarea:focus { border-color: var(--c-fg); }
.field input::placeholder, .field textarea::placeholder { color: var(--c-secondary); opacity: 0.7; }

.confirm-text { font-size: 14px; color: var(--c-fg); margin-bottom: 8px; }
.confirm-warn { font-size: 12px; color: var(--c-danger); }
</style>
