<script setup>
import { ref, computed, watch } from 'vue'
import { createRelation, updateRelation, deleteRelation } from '../../api'
import Pagination from '../common/Pagination.vue'

const props = defineProps({
  categoryId: { type: String, required: true },
  relations: { type: Array, default: () => [] },
})
const emit = defineEmits(['changed'])

const adding = ref(false)
const newName = ref('')
const newCode = ref('')
const newDesc = ref('')
const savingNew = ref(false)

const editingId = ref('')
const editName = ref('')
const editCode = ref('')
const editDesc = ref('')
const savingId = ref('')
const page = ref(1)
const pageSize = ref(10)
const pagedRelations = computed(() =>
  props.relations.slice((page.value - 1) * pageSize.value, page.value * pageSize.value)
)
watch(() => props.relations, () => { page.value = 1 }, { deep: true })

function startAdd() {
  newName.value = ''
  newCode.value = ''
  newDesc.value = ''
  adding.value = true
}

async function submitAdd() {
  if (!newName.value.trim()) return
  savingNew.value = true
  try {
    await createRelation(props.categoryId, { name: newName.value.trim(), code: newCode.value.trim(), description: newDesc.value.trim() })
    adding.value = false
    emit('changed')
  } catch (e) {
    alert('创建失败：' + e.message)
  } finally {
    savingNew.value = false
  }
}

function startEdit(rel) {
  editingId.value = rel.id
  editName.value = rel.name
  editCode.value = rel.code || ''
  editDesc.value = rel.description || ''
}

function cancelEdit() {
  editingId.value = ''
}

async function submitEdit(rel) {
  if (!editName.value.trim()) return
  savingId.value = rel.id
  try {
    await updateRelation(props.categoryId, rel.id, { name: editName.value.trim(), code: editCode.value.trim(), description: editDesc.value.trim() })
    editingId.value = ''
    emit('changed')
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    savingId.value = ''
  }
}

async function remove(rel) {
  if (!confirm(`确认删除关系「${rel.name}」？\n引用该关系的三元组约束将一并删除。`)) return
  try {
    await deleteRelation(props.categoryId, rel.id)
    emit('changed')
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}
</script>

<template>
  <div class="rde-root">
    <div class="rde-head">
      <div class="rde-tip">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
        关系字典定义该类别下所有可用的关系类型，供三元组约束选择。
      </div>
      <button class="btn primary sm" @click="startAdd" :disabled="adding">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        新建关系
      </button>
    </div>

    <!-- 新建行 -->
    <div v-if="adding" class="rde-row editing">
      <div class="rde-form">
        <input type="text" v-model="newName" placeholder="关系名称，如：任职于" class="rde-name-input" @keydown.enter="submitAdd">
        <input type="text" v-model="newCode" placeholder="编码（该类别内唯一）" class="rde-code-input">
        <input type="text" v-model="newDesc" placeholder="描述（可选）" class="rde-desc-input">
      </div>
      <div class="rde-row-actions">
        <button class="btn sm" @click="adding = false">取消</button>
        <button class="btn primary sm" @click="submitAdd" :disabled="savingNew || !newName.trim()">
          <span v-if="savingNew" class="spinner"></span> 保存
        </button>
      </div>
    </div>

    <!-- 列表 -->
    <div v-if="relations.length" class="rde-list">
      <div v-for="rel in pagedRelations" :key="rel.id" class="rde-row">
        <template v-if="editingId === rel.id">
          <div class="rde-form">
            <input type="text" v-model="editName" class="rde-name-input" @keydown.enter="submitEdit(rel)">
            <input type="text" v-model="editCode" placeholder="编码（该类别内唯一）" class="rde-code-input">
            <input type="text" v-model="editDesc" placeholder="描述（可选）" class="rde-desc-input">
          </div>
          <div class="rde-row-actions">
            <button class="btn sm" @click="cancelEdit">取消</button>
            <button class="btn primary sm" @click="submitEdit(rel)" :disabled="savingId === rel.id || !editName.trim()">
              <span v-if="savingId === rel.id" class="spinner"></span> 保存
            </button>
          </div>
        </template>
        <template v-else>
          <div class="rde-row-body">
            <span class="rde-name">{{ rel.name }}</span>
            <span class="rde-code-tag" v-if="rel.code">{{ rel.code }}</span>
            <span class="rde-desc" v-if="rel.description">{{ rel.description }}</span>
          </div>
          <div class="rde-row-actions">
            <button class="icon-btn sm" @click="startEdit(rel)" title="编辑">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
            </button>
            <button class="rm-btn sm" @click="remove(rel)" title="删除">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </template>
      </div>
      <Pagination v-if="relations.length > pageSize" v-model:page="page" v-model:page-size="pageSize" :total="relations.length" />
    </div>

    <div v-else-if="!adding" class="rde-empty">
      暂无关系定义，点击「新建关系」开始
    </div>
  </div>
</template>

<style scoped>
.rde-root { display: flex; flex-direction: column; gap: 10px; max-width: 760px; }
.rde-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.rde-tip { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-secondary); }
.btn.sm { padding: 5px 11px; font-size: 12px; }

.rde-list, .rde-row.editing { display: flex; flex-direction: column; gap: 6px; }
.rde-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel);
}
.rde-row.editing { border-color: var(--c-fg); border-style: dashed; }
.rde-form { flex: 1; display: flex; gap: 10px; min-width: 0; }
.rde-name-input { flex: 0 0 180px; }
.rde-code-input { flex: 0 0 180px; }
.rde-desc-input { flex: 1; min-width: 0; }
.rde-form input {
  width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 13px; font-family: var(--font); outline: none;
}
.rde-form input:focus { border-color: var(--c-fg); }

.rde-row-body { flex: 1; display: flex; align-items: center; gap: 12px; min-width: 0; }
.rde-name { font-size: 14px; font-weight: 600; color: var(--c-fg); flex-shrink: 0; }
.rde-code-tag {
  font-size: 11px; padding: 2px 7px; border-radius: 10px;
  background: rgba(99, 140, 220, 0.15); color: #8bb5f5;
  font-family: var(--font-mono, monospace); flex-shrink: 0;
}
.rde-desc { font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.rde-row-actions { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; }
.icon-btn.sm {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm);
  background: transparent; color: var(--c-secondary); cursor: pointer;
}
.icon-btn.sm:hover { background: var(--c-muted); color: var(--c-fg); }
.rm-btn.sm {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm);
  background: transparent; color: var(--c-secondary); cursor: pointer;
}
.rm-btn.sm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }

.rde-empty { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }
</style>
