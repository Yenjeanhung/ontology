<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import {
  fetchOntologyCategories,
  getOntologyCategoryDetail,
  createOntologyCategory,
  updateOntologyCategory,
  deleteOntologyCategory,
} from '../../api'
import OntologyEditor from './OntologyEditor.vue'
import ModalDialog from '../common/ModalDialog.vue'

const search = ref('')
const categories = ref([])
const loadingList = ref(false)

const selectedId = ref('')
const detail = ref(null)
const loadingDetail = ref(false)
const loadError = ref('')

// 基本信息 编辑
const editingInfo = ref(false)
const infoName = ref('')
const infoDesc = ref('')
const savingInfo = ref(false)

// 新建弹窗
const showCreate = ref(false)
const createName = ref('')
const createDesc = ref('')
const creating = ref(false)

// 编辑弹窗
const showEdit = ref(false)
const editId = ref('')
const editName = ref('')
const editDesc = ref('')
const saving = ref(false)

// 删除确认
const showDelete = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return categories.value
  return categories.value.filter(c =>
    c.name.toLowerCase().includes(q) || (c.description || '').toLowerCase().includes(q)
  )
})

async function loadCategories() {
  loadingList.value = true
  try {
    categories.value = await fetchOntologyCategories()
    // 默认选中第一个
    if (!selectedId.value && categories.value.length) {
      selectCategory(categories.value[0].id)
    }
  } catch {
    categories.value = []
  } finally {
    loadingList.value = false
  }
}

async function loadDetail() {
  if (!selectedId.value) return
  loadingDetail.value = true
  loadError.value = ''
  try {
    const data = await getOntologyCategoryDetail(selectedId.value)
    if (!data) {
      loadError.value = '未找到该本体类别'
      detail.value = null
    } else {
      detail.value = data
      infoName.value = data.name
      infoDesc.value = data.description || ''
    }
  } catch (e) {
    loadError.value = '加载失败：' + e.message
    detail.value = null
  } finally {
    loadingDetail.value = false
  }
}

function selectCategory(id) {
  selectedId.value = id
  loadDetail()
}

function startEditInfo() {
  infoName.value = detail.value.name
  infoDesc.value = detail.value.description || ''
  editingInfo.value = true
}

async function saveInfo() {
  if (!infoName.value.trim()) return
  savingInfo.value = true
  try {
    await updateOntologyCategory(selectedId.value, {
      name: infoName.value.trim(),
      description: infoDesc.value.trim(),
    })
    if (detail.value) {
      detail.value.name = infoName.value.trim()
      detail.value.description = infoDesc.value.trim()
    }
    editingInfo.value = false
    await loadCategories()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    savingInfo.value = false
  }
}

function cancelEditInfo() {
  editingInfo.value = false
  infoName.value = detail.value?.name || ''
  infoDesc.value = detail.value?.description || ''
}

function onSubChanged() {
  loadDetail()
}

// 新建类别
function openCreate() {
  createName.value = ''
  createDesc.value = ''
  showCreate.value = true
}

async function submitCreate() {
  const n = createName.value.trim()
  if (!n) return
  creating.value = true
  try {
    const cat = await createOntologyCategory({ name: n, description: createDesc.value.trim() })
    showCreate.value = false
    await loadCategories()
    selectCategory(cat.id)
  } catch (e) {
    alert('创建失败：' + e.message)
  } finally {
    creating.value = false
  }
}

// 编辑类别
function openEdit(cat) {
  editId.value = cat.id
  editName.value = cat.name
  editDesc.value = cat.description || ''
  showEdit.value = true
}

async function submitEdit() {
  const n = editName.value.trim()
  if (!n) return
  saving.value = true
  try {
    await updateOntologyCategory(editId.value, { name: n, description: editDesc.value.trim() })
    showEdit.value = false
    await loadCategories()
    if (editId.value === selectedId.value) loadDetail()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

// 删除类别
function askDelete(cat) {
  if (cat.is_system) {
    alert('系统内置类别不可删除')
    return
  }
  deleteTarget.value = cat
  showDelete.value = true
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await deleteOntologyCategory(deleteTarget.value.id)
    showDelete.value = false
    if (deleteTarget.value.id === selectedId.value) {
      selectedId.value = ''
      detail.value = null
    }
    deleteTarget.value = null
    await loadCategories()
  } catch (e) {
    alert('删除失败：' + e.message)
  } finally {
    deleting.value = false
  }
}

onMounted(loadCategories)
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">本体管理</h2>
        <span class="page-subtitle">管理本体类别与本体定义，支持本体分类</span>
      </div>
    </div>

    <div class="split-layout">
      <!-- 左侧：本体类别列表 -->
      <div class="cat-panel">
        <div class="cat-panel-toolbar">
          <div class="search-wrap">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" v-model="search" placeholder="搜索类别...">
          </div>
          <button class="icon-btn sm" @click="openCreate" title="新建类别">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          </button>
        </div>

        <div v-if="loadingList && !categories.length" class="loading-state sm"><span class="spinner"></span></div>

        <div class="cat-scroll" v-else-if="filtered.length">
          <div
            v-for="cat in filtered"
            :key="cat.id"
            class="cat-item"
            :class="{ active: cat.id === selectedId }"
            @click="selectCategory(cat.id)"
          >
            <div class="cat-item-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="6" height="6" rx="1.5"/><rect x="14.75" y="3.25" width="6" height="6" rx="1.5"/><rect x="9" y="14.75" width="6" height="6" rx="1.5"/></svg>
            </div>
            <div class="cat-item-body">
              <div class="cat-item-title">
                {{ cat.name }}
                <span v-if="cat.is_system" class="tag system">系统</span>
              </div>
              <div class="cat-item-meta">{{ cat.ontology_count }} 个本体</div>
            </div>
            <div class="cat-item-actions" @click.stop>
              <button class="rm-btn xs" @click="openEdit(cat)" title="编辑">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
              </button>
              <button class="rm-btn xs" @click="askDelete(cat)" title="删除" :disabled="cat.is_system">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </div>
          </div>
        </div>

        <div class="empty-sm" v-else>
          <div class="empty-sm-text">{{ search ? '无匹配类别' : '暂无类别' }}</div>
        </div>
      </div>

      <!-- 右侧：本体编辑区 -->
      <div class="detail-panel">
        <div v-if="loadingDetail" class="loading-state"><span class="spinner"></span> 加载中...</div>
        <div v-else-if="loadError" class="error-state">{{ loadError }}</div>
        <div v-else-if="!selectedId" class="empty-state">
          <div class="empty-icon">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="6" height="6" rx="1.5"/><rect x="14.75" y="3.25" width="6" height="6" rx="1.5"/><rect x="9" y="14.75" width="6" height="6" rx="1.5"/></svg>
          </div>
          <div class="empty-title">请选择左侧的本体类别</div>
          <div class="empty-desc">选择类别后可编辑本体定义与属性</div>
        </div>

        <template v-else-if="detail">
          <!-- 基本信息 -->
          <div class="info-card">
            <div class="info-row">
              <span class="info-label">类别名称</span>
              <div class="info-value">
                <input v-if="editingInfo" type="text" v-model="infoName" class="info-input">
                <span v-else>{{ detail.name }}</span>
              </div>
            </div>
            <div class="info-row">
              <span class="info-label">描述</span>
              <div class="info-value">
                <textarea v-if="editingInfo" v-model="infoDesc" rows="2" class="info-input"></textarea>
                <span v-else>{{ detail.description || '—' }}</span>
              </div>
            </div>
            <div class="info-row">
              <span class="info-label">类型</span>
              <span class="info-value">
                <span v-if="detail.is_system" class="tag system">系统内置</span>
                <span v-else class="tag custom">自定义</span>
              </span>
            </div>
            <div class="info-actions">
              <template v-if="editingInfo">
                <button class="btn" @click="cancelEditInfo">取消</button>
                <button class="btn primary" @click="saveInfo" :disabled="savingInfo || !infoName.trim()">
                  <span v-if="savingInfo" class="spinner"></span> 保存
                </button>
              </template>
              <button v-else class="btn" @click="startEditInfo">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
                编辑
              </button>
            </div>
          </div>

          <!-- 本体编辑器 -->
          <OntologyEditor :category-id="selectedId" :detail="detail" @changed="onSubChanged" />
        </template>
      </div>
    </div>

    <!-- 新建弹窗 -->
    <ModalDialog
      v-model="showCreate"
      title="新建本体类别"
      size="sm"
      :confirm-text="creating ? '创建中...' : '创建'"
      :confirm-loading="creating"
      :confirm-disabled="!createName.trim()"
      @confirm="submitCreate"
    >
      <div class="field">
        <label>名称</label>
        <input type="text" v-model="createName" placeholder="例如：金融领域本体" @keydown.enter="submitCreate">
      </div>
      <div class="field">
        <label>描述（可选）</label>
        <textarea v-model="createDesc" rows="3" placeholder="该本体类别覆盖的业务场景..."></textarea>
      </div>
    </ModalDialog>

    <!-- 编辑弹窗 -->
    <ModalDialog
      v-model="showEdit"
      title="编辑本体类别"
      size="sm"
      :confirm-text="saving ? '保存中...' : '保存'"
      :confirm-loading="saving"
      :confirm-disabled="!editName.trim()"
      @confirm="submitEdit"
    >
      <div class="field">
        <label>名称</label>
        <input type="text" v-model="editName" placeholder="类别名称">
      </div>
      <div class="field">
        <label>描述（可选）</label>
        <textarea v-model="editDesc" rows="3" placeholder="该本体类别覆盖的业务场景..."></textarea>
      </div>
    </ModalDialog>

    <!-- 删除确认 -->
    <ModalDialog
      v-model="showDelete"
      title="删除本体类别"
      size="sm"
      confirm-text="删除"
      confirm-variant="danger"
      :confirm-loading="deleting"
      @confirm="confirmDelete"
    >
      <p class="confirm-text">
        确认要删除「<strong>{{ deleteTarget?.name }}</strong>」吗？
      </p>
      <p class="confirm-warn">该操作将级联删除其下所有本体、属性、关系、三元组约束及知识库绑定，且不可恢复。</p>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }

.split-layout { display: flex; gap: 16px; flex: 1; min-height: 0; }

/* 左侧类别面板 */
.cat-panel { flex: 0 0 280px; display: flex; flex-direction: column; gap: 10px; border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 12px; overflow: hidden; }
.cat-panel-toolbar { display: flex; align-items: center; gap: 8px; }
.search-wrap { flex: 1; display: flex; align-items: center; gap: 6px; padding: 0 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); height: 34px; }
.search-wrap:focus-within { border-color: var(--c-fg); }
.search-icon { color: var(--c-secondary); flex-shrink: 0; }
.search-wrap input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--c-fg); font-size: 13px; font-family: var(--font); }
.search-wrap input::placeholder { color: var(--c-secondary); opacity: 0.7; }
.icon-btn.sm { display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; transition: background 150ms, color 150ms; flex-shrink: 0; }
.icon-btn.sm:hover { background: var(--c-muted); color: var(--c-fg); }

.cat-scroll { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.cat-item { display: flex; align-items: center; gap: 10px; padding: 10px 10px; border-radius: var(--radius-sm); cursor: pointer; transition: background 120ms; }
.cat-item:hover { background: var(--c-muted); }
.cat-item.active { background: var(--c-muted); }
.cat-item.active .cat-item-title { color: var(--c-fg); font-weight: 700; }
.cat-item-icon { flex-shrink: 0; width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; border-radius: var(--radius-sm); background: var(--c-muted); color: var(--c-accent); }
.cat-item-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.cat-item-title { font-size: 13px; font-weight: 600; color: var(--c-fg); display: flex; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cat-item-meta { font-size: 11px; color: var(--c-secondary); }
.cat-item-actions { display: flex; gap: 2px; opacity: 0; transition: opacity 150ms; }
.cat-item:hover .cat-item-actions { opacity: 1; }
.rm-btn.xs { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border: 0; border-radius: 6px; background: transparent; color: var(--c-secondary); cursor: pointer; }
.rm-btn.xs:hover { background: var(--c-muted-hover); color: var(--c-fg); }
.rm-btn.xs:disabled { opacity: 0.3; cursor: not-allowed; }

.empty-sm { padding: 24px 12px; text-align: center; }
.empty-sm-text { font-size: 13px; color: var(--c-secondary); }

.tag { display: inline-flex; padding: 1px 7px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.tag.system { background: var(--c-muted-hover); color: var(--c-secondary); }
.tag.custom { background: var(--c-muted); color: var(--c-secondary); }

/* 右侧详情面板 */
.detail-panel { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }

.info-card { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 4px 20px; max-width: 720px; }
.info-row { display: flex; align-items: flex-start; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--c-border); }
.info-row:last-child { border-bottom: 0; }
.info-label { flex: 0 0 90px; font-size: 13px; font-weight: 600; color: var(--c-secondary); padding-top: 2px; }
.info-value { flex: 1; min-width: 0; font-size: 14px; color: var(--c-fg); word-break: break-word; }
.info-input { width: 100%; padding: 7px 11px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 14px; font-family: var(--font); outline: none; resize: vertical; }
.info-input:focus { border-color: var(--c-fg); }
.info-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 0; }

.loading-state { padding: 40px; text-align: center; color: var(--c-secondary); font-size: 14px; }
.loading-state.sm { padding: 20px; }
.error-state { padding: 40px; text-align: center; color: var(--c-danger); font-size: 14px; }
.empty-state { text-align: center; padding: 48px 20px; color: var(--c-secondary); }
.empty-state .empty-icon { margin-bottom: 12px; color: var(--c-border); }
.empty-state .empty-title { font-size: 15px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.empty-state .empty-desc { font-size: 13px; }

.field { margin-bottom: 14px; }
.field:last-child { margin-bottom: 0; }
.field label { display: block; font-size: 13px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; }
.field input, .field textarea { width: 100%; padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 14px; font-family: var(--font); outline: none; transition: border-color 150ms; resize: vertical; }
.field input:focus, .field textarea:focus { border-color: var(--c-fg); }
.confirm-text { font-size: 14px; color: var(--c-fg); margin-bottom: 8px; }
.confirm-warn { font-size: 12px; color: var(--c-danger); }
</style>
