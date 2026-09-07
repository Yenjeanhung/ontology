<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import {
  fetchSharedProperties,
  createSharedProperty,
  updateSharedProperty,
  deleteSharedProperty,
  applySharedProperty,
  fetchOntologyCategories,
  fetchOntologies,
} from '../../api'
import ModalDialog from '../common/ModalDialog.vue'
import Pagination from '../common/Pagination.vue'

const DATA_TYPES = ['string', 'number', 'boolean', 'date', 'datetime', 'text', 'enum']

const search = ref('')
const searchDraft = ref('')
const props = ref([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(10)

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return props.value
  return props.value.filter(p =>
    p.name.toLowerCase().includes(q)
    || (p.code || '').toLowerCase().includes(q)
  )
})
const pagedProps = computed(() =>
  filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value)
)
watch(search, () => { page.value = 1 })

function applySearch() { search.value = searchDraft.value }
function resetSearch() { searchDraft.value = ''; search.value = '' }

async function load() {
  loading.value = true
  try {
    props.value = await fetchSharedProperties()
  } catch (e) {
    alert('加载失败：' + e.message)
    props.value = []
  } finally {
    loading.value = false
  }
}

// ── 新建 / 编辑 ──
const showForm = ref(false)
const editing = ref(null)   // null=新建
const saving = ref(false)
const form = ref({
  name: '', code: '', data_type: 'string', description: '',
  is_required: false, default_value: '', enum_values: '', unit: '', format: '',
})

function openCreate() {
  editing.value = null
  form.value = { name: '', code: '', data_type: 'string', description: '', is_required: false, default_value: '', enum_values: '', unit: '', format: '' }
  showForm.value = true
}

function openEdit(p) {
  editing.value = p
  form.value = {
    name: p.name,
    code: p.code || '',
    data_type: p.data_type,
    description: p.description || '',
    is_required: !!p.is_required,
    default_value: p.default_value || '',
    enum_values: (p.enum_values || []).join(', '),
    unit: p.unit || '',
    format: p.format || '',
  }
  showForm.value = true
}

async function submitForm() {
  if (!form.value.name.trim()) return
  const payload = {
    name: form.value.name.trim(),
    code: form.value.code.trim() || null,
    data_type: form.value.data_type,
    description: form.value.description.trim(),
    is_required: form.value.is_required,
    default_value: form.value.default_value || null,
    unit: form.value.unit.trim(),
    format: form.value.format.trim(),
  }
  if (form.value.data_type === 'enum') {
    payload.enum_values = form.value.enum_values.split(/[,，、]/).map(s => s.trim()).filter(Boolean)
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateSharedProperty(editing.value.id, payload)
    } else {
      await createSharedProperty(payload)
    }
    showForm.value = false
    await load()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

// ── 删除 ──
const showDelete = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)

function askDelete(p) {
  deleteTarget.value = p
  showDelete.value = true
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await deleteSharedProperty(deleteTarget.value.id)
    showDelete.value = false
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  } finally {
    deleting.value = false
  }
}

// ── 挂到本体 ──
const showApply = ref(false)
const applyTarget = ref(null)
const applying = ref(false)
const overwrite = ref(false)
const ontologyTree = ref([])      // [{ category, ontologies: [...] }]
const checkedOnts = ref(new Set())
const treeLoading = ref(false)

function askApply(p) {
  applyTarget.value = p
  checkedOnts.value = new Set()
  overwrite.value = false
  showApply.value = true
  loadOntologyTree()
}

async function loadOntologyTree() {
  treeLoading.value = true
  try {
    const cats = await fetchOntologyCategories()
    const tree = await Promise.all((cats || []).map(async c => ({
      category: c,
      ontologies: await fetchOntologies(c.id).catch(() => []),
    })))
    ontologyTree.value = tree
  } catch (e) {
    ontologyTree.value = []
  } finally {
    treeLoading.value = false
  }
}

function toggleOnt(id) {
  const next = new Set(checkedOnts.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  checkedOnts.value = next
}

function toggleCategory(group) {
  const next = new Set(checkedOnts.value)
  const ids = group.ontologies.map(o => o.id)
  const all = ids.every(id => next.has(id))
  ids.forEach(id => (all ? next.delete(id) : next.add(id)))
  checkedOnts.value = next
}

async function confirmApply() {
  if (!applyTarget.value || !checkedOnts.value.size) return
  applying.value = true
  try {
    const res = await applySharedProperty(applyTarget.value.id, {
      ontology_ids: Array.from(checkedOnts.value),
      overwrite: overwrite.value,
    })
    const msg = res.created ? `新建属性 ${res.created} 处` : ''
      + (res.updated ? `${res.updated} 处已同步` : '')
      + (res.skipped ? `，${res.skipped} 处跳过` : '')
    alert('应用完成：' + (msg || '无变更'))
    showApply.value = false
    await load()
  } catch (e) {
    alert('应用失败：' + e.message)
  } finally {
    applying.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2 class="page-title">共享属性</h2>
        <p class="page-desc">
          跨本体统一定义属性契约（类型/枚举/单位），本体属性引用后改一处全局生效；与「本体模板」（定义拷贝）互补。
        </p>
      </div>
      <button class="btn primary" @click="openCreate">+ 新建共享属性</button>
    </div>

    <div class="filter-bar">
      <svg class="filter-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input class="filter-input" v-model="searchDraft" type="text" placeholder="搜索名称或编码..." @keydown.enter="applySearch">
      <button class="btn primary" @click="applySearch">查询</button>
      <button class="btn" @click="resetSearch">重置</button>
      <span class="filter-count">共 {{ filtered.length }} 个</span>
    </div>

    <div class="card">
      <div v-if="loading" class="table-state"><span class="spinner"></span> 加载中...</div>
      <div v-else-if="!filtered.length" class="table-state empty">
        {{ search ? '没有匹配的共享属性' : '暂无共享属性，点击右上角新建' }}
      </div>
      <table v-else class="table">
        <thead>
          <tr>
            <th>名称</th>
            <th style="width:130px">编码</th>
            <th style="width:90px">类型</th>
            <th style="width:60px">必填</th>
            <th style="width:70px">单位</th>
            <th style="width:80px">引用本体</th>
            <th style="width:230px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in pagedProps" :key="p.id">
            <td>
              <div class="prop-name">{{ p.name }}</div>
              <div class="prop-desc" v-if="p.description">{{ p.description }}</div>
              <div class="prop-enum" v-if="p.data_type === 'enum' && p.enum_values?.length">
                枚举：{{ p.enum_values.join(' / ') }}
              </div>
            </td>
            <td><code class="mono">{{ p.code || '—' }}</code></td>
            <td><span class="type-tag">{{ p.data_type }}</span></td>
            <td>{{ p.is_required ? '是' : '否' }}</td>
            <td>{{ p.unit || '—' }}</td>
            <td>
              <span class="usage" :class="{ used: p.usage_count > 0 }">{{ p.usage_count }}</span>
            </td>
            <td>
              <button class="btn sm" @click="openEdit(p)">编辑</button>
              <button class="btn sm primary" @click="askApply(p)">挂到本体</button>
              <button class="btn sm danger" :disabled="p.is_system" @click="askDelete(p)"
                :title="p.is_system ? '系统内置不可删除' : ''">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <Pagination v-if="filtered.length > pageSize" v-model:page="page" v-model:pageSize="pageSize" :total="filtered.length" />
    </div>

    <!-- 新建/编辑 -->
    <ModalDialog v-model="showForm" :title="editing ? '编辑共享属性' : '新建共享属性'" confirmText="保存"
      :confirmLoading="saving" :confirmDisabled="!form.name.trim()" @confirm="submitForm">
      <div class="form">
        <div class="form-row">
          <label>名称 *</label>
          <input v-model="form.name" type="text" placeholder="如：统一社会信用代码">
        </div>
        <div class="form-row">
          <label>编码</label>
          <input v-model="form.code" type="text" placeholder="如 uscc（留空自动忽略）">
        </div>
        <div class="form-row two">
          <div>
            <label>类型</label>
            <select v-model="form.data_type">
              <option v-for="t in DATA_TYPES" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>
          <div>
            <label>单位</label>
            <input v-model="form.unit" type="text" placeholder="如 万元 / %">
          </div>
        </div>
        <div class="form-row two" v-if="form.data_type === 'enum'">
          <div class="full">
            <label>枚举值（逗号分隔）</label>
            <input v-model="form.enum_values" type="text" placeholder="如：国企, 民企, 外企">
          </div>
        </div>
        <div class="form-row two">
          <div>
            <label>默认值</label>
            <input v-model="form.default_value" type="text">
          </div>
          <div>
            <label>格式</label>
            <input v-model="form.format" type="text" placeholder="如 #,##0.00 / YYYY-MM-DD">
          </div>
        </div>
        <div class="form-row">
          <label class="check-label">
            <input type="checkbox" v-model="form.is_required"> 必填
          </label>
        </div>
        <div class="form-row">
          <label>描述</label>
          <textarea v-model="form.description" rows="2"></textarea>
        </div>
      </div>
    </ModalDialog>

    <!-- 删除确认 -->
    <ModalDialog v-model="showDelete" title="删除共享属性" confirmText="删除" confirmVariant="danger"
      :confirmLoading="deleting" @confirm="confirmDelete">
      <p class="dialog-text">
        确定删除共享属性「{{ deleteTarget?.name }}」吗？
        引用它的 {{ deleteTarget?.usage_count || 0 }} 个本体属性将保留，仅解除引用。
      </p>
    </ModalDialog>

    <!-- 挂到本体 -->
    <ModalDialog v-model="showApply" title="挂到本体" confirmText="应用" size="lg"
      :confirmLoading="applying" :confirmDisabled="!checkedOnts.size" @confirm="confirmApply">
      <p class="dialog-text">
        把「{{ applyTarget?.name }}」挂到选中的本体：本体无同名属性则新建（自动绑定引用）；勾选「同步已有」时会用共享定义覆盖已存在的同名属性。
      </p>
      <div v-if="treeLoading" class="table-state"><span class="spinner"></span> 加载本体列表...</div>
      <div v-else class="tree">
        <div v-for="group in ontologyTree" :key="group.category.id" class="tree-group">
          <label class="tree-cat">
            <input type="checkbox"
              :checked="group.ontologies.length > 0 && group.ontologies.every(o => checkedOnts.has(o.id))"
              :disabled="!group.ontologies.length"
              @change="toggleCategory(group)">
            <span class="tree-cat-name">{{ group.category.name }}</span>
            <span class="tree-cat-count">{{ group.ontologies.length }} 个本体</span>
          </label>
          <div class="tree-onts" v-if="group.ontologies.length">
            <label v-for="o in group.ontologies" :key="o.id" class="tree-ont">
              <input type="checkbox" :checked="checkedOnts.has(o.id)" @change="toggleOnt(o.id)">
              {{ o.name }}
            </label>
          </div>
        </div>
      </div>
      <label class="check-label apply-overwrite">
        <input type="checkbox" v-model="overwrite"> 同步已有同名属性（覆盖类型/必填/默认值并绑定引用）
      </label>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-desc { font-size: 12px; color: var(--c-secondary); margin-top: 4px; max-width: 640px; }

.filter-bar {
  display: flex; align-items: center; gap: 8px;
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); padding: 10px 14px;
}
.filter-search-icon { color: var(--c-secondary); flex-shrink: 0; }
.filter-input {
  flex: 1; min-width: 200px; border: none; outline: none; background: transparent;
  color: var(--c-fg); font-size: 13px; font-family: var(--font);
}
.filter-count { font-size: 12px; color: var(--c-secondary); }

.card { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); overflow: hidden; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th {
  text-align: left; padding: 10px 14px; font-size: 12px; font-weight: 600;
  color: var(--c-secondary); border-bottom: 1px solid var(--c-border); background: var(--c-muted);
}
.table td { padding: 10px 14px; border-bottom: 1px solid var(--c-border); vertical-align: top; }
.table tbody tr:hover { background: var(--c-muted); }
.prop-name { font-weight: 600; color: var(--c-fg); }
.prop-desc { font-size: 12px; color: var(--c-secondary); margin-top: 2px; }
.prop-enum { font-size: 12px; color: var(--c-secondary); margin-top: 2px; }
.mono { font-family: ui-monospace, 'SF Mono', Consolas, monospace; font-size: 12px; }
.type-tag {
  display: inline-block; padding: 1px 8px; border-radius: 9px; font-size: 12px;
  background: var(--c-muted); color: var(--c-secondary);
}
.usage { color: var(--c-secondary); font-size: 13px; }
.usage.used { color: var(--c-accent, #14b8a6); font-weight: 700; }
.table-state { padding: 40px; text-align: center; color: var(--c-secondary); font-size: 13px; }
.table-state.empty { color: var(--c-secondary); }
.spinner {
  display: inline-block; width: 14px; height: 14px; border: 2px solid var(--c-border);
  border-top-color: var(--c-fg); border-radius: 50%; animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.dialog-text { font-size: 13px; color: var(--c-secondary); line-height: 1.6; margin-bottom: 14px; }

.form { display: flex; flex-direction: column; gap: 12px; }
.form-row { display: flex; flex-direction: column; gap: 5px; }
.form-row.two { flex-direction: row; gap: 12px; }
.form-row.two > div { flex: 1; display: flex; flex-direction: column; gap: 5px; }
.form-row label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.form input[type='text'], .form select, .form textarea {
  width: 100%; padding: 7px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 13px; font-family: var(--font); outline: none;
}
.form input:focus, .form select:focus, .form textarea:focus { border-color: var(--c-fg); }
.check-label { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; color: var(--c-fg); cursor: pointer; }
.apply-overwrite { margin-top: 12px; }

.tree { max-height: 320px; overflow: auto; border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 8px 12px; }
.tree-group { padding: 6px 0; border-bottom: 1px dashed var(--c-border); }
.tree-group:last-child { border-bottom: none; }
.tree-cat { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.tree-cat-name { font-size: 13px; font-weight: 700; color: var(--c-fg); }
.tree-cat-count { font-size: 12px; color: var(--c-secondary); }
.tree-onts { display: flex; flex-wrap: wrap; gap: 6px 16px; padding: 6px 0 2px 24px; }
.tree-ont { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--c-fg); cursor: pointer; }
</style>
