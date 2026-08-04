<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchOntologyCategories,
  createOntologyCategory,
  updateOntologyCategory,
  deleteOntologyCategory,
} from '../../api'
import ModalDialog from '../common/ModalDialog.vue'

const router = useRouter()
const search = ref('')
const categories = ref([])
const loading = ref(false)

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

async function load() {
  loading.value = true
  try {
    categories.value = await fetchOntologyCategories()
  } catch (e) {
    categories.value = []
  } finally {
    loading.value = false
  }
}

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
    await createOntologyCategory({ name: n, description: createDesc.value.trim() })
    showCreate.value = false
    await load()
  } catch (e) {
    alert('创建失败：' + e.message)
  } finally {
    creating.value = false
  }
}

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
    await load()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

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
        <h2 class="page-title">本体类别</h2>
        <span class="page-subtitle">定义领域本体、属性、关系与三元组约束</span>
      </div>
    </div>

    <div class="toolbar">
      <div class="search-wrap">
        <svg class="search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input type="text" v-model="search" placeholder="搜索本体类别...">
      </div>
      <button class="icon-btn refresh-btn" @click="load" title="刷新">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
      </button>
      <button class="btn primary" @click="openCreate">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        新建
      </button>
    </div>

    <div class="cat-list" v-if="filtered.length">
      <div
        v-for="cat in filtered"
        :key="cat.id"
        class="cat-row"
        @click="router.push('/ontology-categories/' + cat.id)"
      >
        <div class="cat-icon-box">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="6" height="6" rx="1.5"/><rect x="14.75" y="3.25" width="6" height="6" rx="1.5"/><rect x="9" y="14.75" width="6" height="6" rx="1.5"/><path d="M6.25 9.25v1.75a1.5 1.5 0 0 0 1.5 1.5h1.25"/><path d="M17.75 9.25v1.75a1.5 1.5 0 0 1-1.5 1.5H15.25"/></svg>
        </div>
        <div class="cat-body">
          <div class="cat-title-row">
            <span class="cat-title">{{ cat.name }}</span>
            <span v-if="cat.is_system" class="tag system">系统</span>
          </div>
          <div class="cat-meta">
            <span class="meta-chip">{{ cat.ontology_count }} 个本体</span>
            <span class="cat-desc" v-if="cat.description">{{ cat.description }}</span>
          </div>
        </div>
        <div class="cat-hover-actions">
          <button class="icon-btn" @click="openEdit(cat, $event)" title="编辑">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
          </button>
          <button class="rm-btn" @click="askDelete(cat, $event)" title="删除" :disabled="cat.is_system">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
      </div>
    </div>

    <div class="empty-state" v-else-if="!loading">
      <div class="empty-icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="6" height="6" rx="1.5"/><rect x="14.75" y="3.25" width="6" height="6" rx="1.5"/><rect x="9" y="14.75" width="6" height="6" rx="1.5"/></svg>
      </div>
      <div class="empty-title">{{ search ? '没有匹配的本体类别' : '暂无本体类别' }}</div>
      <div class="empty-desc" v-if="!search">点击「新建」创建第一个本体类别</div>
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
.page-shell { display: flex; flex-direction: column; gap: 16px; }
.page-head {
  display: flex; align-items: flex-end; justify-content: space-between; gap: 12px;
  padding-bottom: 12px; border-bottom: 1px solid var(--c-border);
}
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }

.toolbar {
  display: flex; align-items: center; gap: 10px;
}
.search-wrap {
  flex: 1; display: flex; align-items: center; gap: 8px;
  padding: 0 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); height: 38px;
}
.search-wrap:focus-within { border-color: var(--c-fg); }
.search-icon { color: var(--c-secondary); flex-shrink: 0; }
.search-wrap input {
  flex: 1; min-width: 0; border: 0; outline: none; background: transparent;
  color: var(--c-fg); font-size: 14px; font-family: var(--font);
}
.search-wrap input::placeholder { color: var(--c-secondary); opacity: 0.7; }

.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 38px; height: 38px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-secondary); cursor: pointer;
  transition: background 150ms, color 150ms;
}
.icon-btn:hover { background: var(--c-muted); color: var(--c-fg); }

.cat-list { display: flex; flex-direction: column; gap: 8px; }
.cat-row {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 16px; border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); cursor: pointer;
  transition: background 150ms, border-color 150ms;
}
.cat-row:hover { background: var(--c-muted); border-color: var(--c-fg); }
.cat-icon-box {
  flex-shrink: 0; width: 40px; height: 40px; display: inline-flex; align-items: center; justify-content: center;
  border-radius: var(--radius-sm); background: var(--c-muted); color: var(--c-accent);
}
.cat-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.cat-title-row { display: flex; align-items: center; gap: 8px; }
.cat-title { font-size: 15px; font-weight: 600; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag {
  display: inline-flex; align-items: center; padding: 1px 7px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.tag.system { background: var(--c-muted-hover); color: var(--c-secondary); }
.cat-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.meta-chip {
  font-size: 12px; color: var(--c-secondary);
  padding: 2px 8px; border-radius: 10px; background: var(--c-muted);
}
.cat-desc { font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.cat-hover-actions { display: flex; align-items: center; gap: 6px; opacity: 0; transition: opacity 150ms; }
.cat-row:hover .cat-hover-actions { opacity: 1; }
.rm-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border: 0; border-radius: var(--radius-sm);
  background: transparent; color: var(--c-secondary); cursor: pointer;
  transition: background 150ms, color 150ms;
}
.rm-btn:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.rm-btn:disabled { opacity: 0.3; cursor: not-allowed; }

.empty-state { text-align: center; padding: 48px 20px; color: var(--c-secondary); }
.empty-state .empty-icon { margin-bottom: 12px; color: var(--c-border); }
.empty-state .empty-title { font-size: 15px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.empty-state .empty-desc { font-size: 13px; }

.field { margin-bottom: 14px; }
.field:last-child { margin-bottom: 0; }
.field label { display: block; font-size: 13px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; }
.field input, .field textarea {
  width: 100%; padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 14px; font-family: var(--font); outline: none;
  transition: border-color 150ms; resize: vertical;
}
.field input:focus, .field textarea:focus { border-color: var(--c-fg); }
.field input::placeholder, .field textarea::placeholder { color: var(--c-secondary); opacity: 0.7; }

.confirm-text { font-size: 14px; color: var(--c-fg); margin-bottom: 8px; }
.confirm-warn { font-size: 12px; color: var(--c-danger); }
</style>
