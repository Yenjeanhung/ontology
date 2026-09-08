<script setup>
import { ref, computed, watch } from 'vue'
import {
  createRelation, updateRelation, deleteRelation,
  fetchRelationProperties, createRelationProperty, updateRelationProperty, deleteRelationProperty,
} from '../../api'
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
const newCardinality = ref('MANY_TO_MANY')
const newInverse = ref('')
const newSymmetric = ref(false)
const newTransitive = ref(false)
const newStatus = ref('active')
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

const CARD_OPTIONS = [
  { value: 'ONE_TO_ONE', label: '一对一 (1:1)' },
  { value: 'ONE_TO_MANY', label: '一对多 (1:N)' },
  { value: 'MANY_TO_MANY', label: '多对多 (N:N)' },
]
function cardLabel(c) { return (CARD_OPTIONS.find(o => o.value === c) || {}).label || c || '多对多' }

function startAdd() {
  newName.value = ''
  newCode.value = ''
  newDesc.value = ''
  newCardinality.value = 'MANY_TO_MANY'
  newInverse.value = ''
  newSymmetric.value = false
  newTransitive.value = false
  newStatus.value = 'active'
  adding.value = true
}

async function submitAdd() {
  if (!newName.value.trim()) return
  savingNew.value = true
  try {
    await createRelation(props.categoryId, {
      name: newName.value.trim(), code: newCode.value.trim(), description: newDesc.value.trim(),
      cardinality: newCardinality.value, inverse_name: newInverse.value,
      is_symmetric: newSymmetric.value, is_transitive: newTransitive.value, status: newStatus.value,
    })
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

// ══════════ S5：链接高级语义 + 关系属性 ══════════
const showAdv = ref(false)
const advRel = ref(null)
const advForm = ref({ cardinality: 'MANY_TO_MANY', inverse_name: '', is_symmetric: false, is_transitive: false, status: 'active' })
const advSaving = ref(false)
const relProps = ref([])
const propsLoading = ref(false)
const propEditor = ref({ id: '', name: '', code: '', data_type: 'string', description: '', is_required: false, enum_text: '' })

async function openAdv(rel) {
  advRel.value = rel
  advForm.value = {
    cardinality: rel.cardinality || 'MANY_TO_MANY',
    inverse_name: rel.inverse_name || '',
    is_symmetric: !!rel.is_symmetric,
    is_transitive: !!rel.is_transitive,
    status: rel.status || 'active',
  }
  showAdv.value = true
  await loadProps()
}

async function loadProps() {
  if (!advRel.value) return
  propsLoading.value = true
  try {
    relProps.value = await fetchRelationProperties(props.categoryId, advRel.value.id)
  } catch (e) {
    alert('加载关系属性失败：' + e.message)
  } finally {
    propsLoading.value = false
  }
}

function resetPropEditor() {
  propEditor.value = { id: '', name: '', code: '', data_type: 'string', description: '', is_required: false, enum_text: '' }
}
function editProp(p) {
  propEditor.value = {
    id: p.id, name: p.name, code: p.code || '', data_type: p.data_type,
    description: p.description || '', is_required: !!p.is_required,
    enum_text: (p.enum_values || []).join(','),
  }
}
async function saveProp() {
  if (!propEditor.value.name.trim() || !advRel.value) return
  const payload = {
    name: propEditor.value.name.trim(),
    code: propEditor.value.code.trim(),
    data_type: propEditor.value.data_type,
    description: propEditor.value.description,
    is_required: !!propEditor.value.is_required,
    enum_values: propEditor.value.enum_text
      ? propEditor.value.enum_text.split(',').map(s => s.trim()).filter(Boolean)
      : null,
  }
  try {
    if (propEditor.value.id) await updateRelationProperty(propEditor.value.id, payload)
    else await createRelationProperty(props.categoryId, advRel.value.id, payload)
    resetPropEditor()
    await loadProps()
  } catch (e) {
    alert('保存关系属性失败：' + e.message)
  }
}
async function removeProp(p) {
  if (!confirm(`确认删除关系属性「${p.name}」？`)) return
  try {
    await deleteRelationProperty(p.id)
    if (propEditor.value.id === p.id) resetPropEditor()
    await loadProps()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

async function saveAdv() {
  if (!advRel.value) return
  advSaving.value = true
  try {
    await updateRelation(props.categoryId, advRel.value.id, {
      cardinality: advForm.value.cardinality,
      inverse_name: advForm.value.inverse_name,
      is_symmetric: advForm.value.is_symmetric,
      is_transitive: advForm.value.is_transitive,
      status: advForm.value.status,
    })
    showAdv.value = false
    emit('changed')
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    advSaving.value = false
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
        <select v-model="newCardinality" class="rde-card-input" title="端点基数">
          <option v-for="o in CARD_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
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
            <span class="rde-pill" v-if="rel.cardinality && rel.cardinality !== 'MANY_TO_MANY'">{{ cardLabel(rel.cardinality) }}</span>
            <span class="rde-pill sym" v-if="rel.is_symmetric">对称</span>
            <span class="rde-pill tr" v-if="rel.is_transitive">传递</span>
            <span class="rde-pill inv" v-if="rel.inverse_name">⇄ {{ rel.inverse_name }}</span>
            <span class="rde-pill dep" v-if="rel.status === 'deprecated'">已弃用</span>
          </div>
          <div class="rde-row-actions">
            <button class="icon-btn sm" @click="startEdit(rel)" title="编辑基本信息">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
            </button>
            <button class="icon-btn sm" @click="openAdv(rel)" title="高级：基数 / 反向 / 关系属性">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M3 12h18M3 18h18"/></svg>
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

    <!-- S5：高级语义 + 关系属性 弹窗 -->
    <div v-if="showAdv" class="rde-mask">
      <div class="rde-modal">
        <div class="rde-modal-head">
          <h3>关系高级配置：{{ advRel?.name }}</h3>
          <button class="rde-close" @click="showAdv = false">×</button>
        </div>
        <div class="rde-modal-body">
          <div class="rde-grid2">
            <div class="rde-field">
              <label>链接基数（端点基数）</label>
              <select v-model="advForm.cardinality">
                <option v-for="o in CARD_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
            <div class="rde-field">
              <label>状态</label>
              <select v-model="advForm.status">
                <option value="active">启用</option>
                <option value="deprecated">已弃用</option>
              </select>
            </div>
          </div>
          <div class="rde-field">
            <label>反向展示名（如：雇佣 ↔ 任职于）</label>
            <input type="text" v-model="advForm.inverse_name" placeholder="反向关系名称">
          </div>
          <div class="rde-checks">
            <label class="rde-check"><input type="checkbox" v-model="advForm.is_symmetric"> 对称关系（如：合作）</label>
            <label class="rde-check"><input type="checkbox" v-model="advForm.is_transitive"> 传递关系（如：位于/属于）</label>
          </div>

          <div class="rde-subblock">
            <div class="rde-subhead">
              <span>关系属性（链接本身可带属性：时间区间 / 权重 / 置信度 / 来源等）</span>
            </div>
            <div v-if="propsLoading" class="rde-hint">加载中...</div>
            <div v-else-if="!relProps.length" class="rde-hint">暂无关系属性。添加后，抽取链路按此写入 relations.properties。</div>
            <table v-else class="rde-ptable">
              <thead><tr><th>名称</th><th>编码</th><th>类型</th><th>必填</th><th></th></tr></thead>
              <tbody>
                <tr v-for="p in relProps" :key="p.id">
                  <td>{{ p.name }}</td>
                  <td class="rde-mono">{{ p.code }}</td>
                  <td>{{ p.data_type }}</td>
                  <td>{{ p.is_required ? '✓' : '—' }}</td>
                  <td class="rde-pops">
                    <button class="btn sm" @click="editProp(p)">编辑</button>
                    <button class="btn sm danger" @click="removeProp(p)">删除</button>
                  </td>
                </tr>
              </tbody>
            </table>

            <div class="rde-pform">
              <div class="rde-pform-title">{{ propEditor.id ? '编辑属性' : '新增属性' }}</div>
              <div class="rde-grid2">
                <input type="text" v-model="propEditor.name" placeholder="名称，如：任职开始">
                <input type="text" v-model="propEditor.code" placeholder="编码，如：start_date">
                <select v-model="propEditor.data_type">
                  <option value="string">string</option>
                  <option value="number">number</option>
                  <option value="date">date</option>
                  <option value="enum">enum</option>
                  <option value="boolean">boolean</option>
                </select>
                <input type="text" v-model="propEditor.enum_text" placeholder="枚举值（逗号分隔，仅 enum）">
              </div>
              <input type="text" v-model="propEditor.description" placeholder="描述（可选）">
              <label class="rde-check"><input type="checkbox" v-model="propEditor.is_required"> 必填</label>
              <div class="rde-pform-actions">
                <button v-if="propEditor.id" class="btn sm" @click="resetPropEditor">取消</button>
                <button class="btn sm primary" @click="saveProp" :disabled="!propEditor.name.trim()">保存属性</button>
              </div>
            </div>
          </div>
        </div>
        <div class="rde-modal-foot">
          <button class="btn sm" @click="showAdv = false">关闭</button>
          <button class="btn sm primary" :disabled="advSaving" @click="saveAdv">保存语义</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rde-root { display: flex; flex-direction: column; gap: 10px; max-width: 860px; }
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
.rde-form { flex: 1; display: flex; gap: 10px; min-width: 0; flex-wrap: wrap; }
.rde-name-input { flex: 0 0 180px; }
.rde-code-input { flex: 0 0 160px; }
.rde-desc-input { flex: 1; min-width: 0; }
.rde-card-input { flex: 0 0 150px; }
.rde-form input, .rde-form select {
  width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 13px; font-family: var(--font); outline: none;
  box-sizing: border-box;
}
.rde-form input:focus, .rde-form select:focus { border-color: var(--c-fg); }

.rde-row-body { flex: 1; display: flex; align-items: center; gap: 8px; min-width: 0; flex-wrap: wrap; }
.rde-name { font-size: 14px; font-weight: 600; color: var(--c-fg); flex-shrink: 0; }
.rde-code-tag {
  font-size: 11px; padding: 2px 7px; border-radius: 10px;
  background: rgba(99, 140, 220, 0.15); color: #8bb5f5;
  font-family: var(--font-mono, monospace); flex-shrink: 0;
}
.rde-desc { font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.rde-pill { font-size: 11px; padding: 1px 7px; border-radius: 9px; background: var(--c-muted); color: var(--c-secondary); }
.rde-pill.sym { background: rgba(139, 92, 246, 0.14); color: #a78bfa; }
.rde-pill.tr { background: rgba(59, 130, 246, 0.14); color: #60a5fa; }
.rde-pill.inv { background: rgba(34, 197, 94, 0.14); color: #4ade80; }
.rde-pill.dep { background: rgba(220, 38, 38, 0.12); color: var(--c-danger); }

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

.btn.danger { border-color: rgba(220,38,38,0.4); color: var(--c-danger); }
.btn.danger:hover { background: rgba(220,38,38,0.08); }

/* S5 弹窗 */
.rde-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 300; }
.rde-modal { width: 680px; max-width: 94vw; max-height: 88vh; overflow-y: auto; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius); display: flex; flex-direction: column; }
.rde-modal-head { display: flex; align-items: center; justify-content: space-between; padding: 13px 16px; border-bottom: 1px solid var(--c-border); }
.rde-modal-head h3 { margin: 0; font-size: 14px; font-weight: 700; color: var(--c-fg); }
.rde-close { width: 26px; height: 26px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); font-size: 16px; cursor: pointer; }
.rde-close:hover { background: var(--c-muted); color: var(--c-fg); }
.rde-modal-body { padding: 16px; display: flex; flex-direction: column; gap: 14px; }
.rde-field { display: flex; flex-direction: column; gap: 4px; }
.rde-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.rde-field input, .rde-field select { width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; box-sizing: border-box; }
.rde-field input:focus, .rde-field select:focus { border-color: var(--c-fg); }
.rde-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.rde-checks { display: flex; gap: 18px; }
.rde-check { display: flex; align-items: center; gap: 5px; font-size: 13px; color: var(--c-fg); }

.rde-subblock { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.rde-subhead { font-size: 12px; font-weight: 700; color: var(--c-fg); }
.rde-hint { font-size: 12px; color: var(--c-secondary); }
.rde-ptable { width: 100%; border-collapse: collapse; font-size: 12px; }
.rde-ptable th, .rde-ptable td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--c-border); color: var(--c-fg); }
.rde-ptable th { color: var(--c-secondary); font-weight: 600; }
.rde-mono { font-family: var(--font-mono, monospace); color: var(--c-secondary); }
.rde-pops { display: flex; gap: 6px; }
.rde-pform { border-top: 1px dashed var(--c-border); padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.rde-pform-title { font-size: 12px; font-weight: 700; color: var(--c-fg); }
.rde-pform input { width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; box-sizing: border-box; }
.rde-pform input:focus { border-color: var(--c-fg); }
.rde-pform-actions { display: flex; gap: 8px; justify-content: flex-end; }
.rde-modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--c-border); }
</style>
