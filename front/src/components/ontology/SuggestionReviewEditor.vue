<script setup>
import { ref, reactive, onMounted } from 'vue'
import { getOntologySuggestion, updateOntologySuggestion, approveOntologySuggestion, rejectOntologySuggestion } from '../../api'

const props = defineProps({
  suggestionId: { type: String, required: true },
})
const emit = defineEmits(['done'])

const DATA_TYPE_OPTIONS = ['string', 'text', 'number', 'boolean', 'date', 'datetime']

const detail = ref(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const categoryName = ref('')
const categoryDesc = ref('')
const ontologies = reactive([])
const relations = reactive([])
const constraints = reactive([])

onMounted(async () => {
  loading.value = true
  error.value = ''
  try {
    const data = await getOntologySuggestion(props.suggestionId)
    detail.value = data
    initFromData(data)
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
})

function initFromData(data) {
  const sd = data.suggestion_data || {}
  categoryName.value = sd.category?.name || ''
  categoryDesc.value = sd.category?.description || ''
  ontologies.splice(0, ontologies.length, ...(sd.ontologies || []).map(o => ({
    ...o,
    attributes: [...(o.attributes || [])],
  })))
  relations.splice(0, relations.length, ...(sd.relations || []).map(r => ({ ...r })))
  constraints.splice(0, constraints.length, ...(sd.constraints || []).map(c => ({ ...c })))
}

function addAttribute(ontIndex) {
  ontologies[ontIndex].attributes.push({ name: '', code: '', data_type: 'string', is_required: false })
}

function removeAttribute(ontIndex, attrIndex) {
  ontologies[ontIndex].attributes.splice(attrIndex, 1)
}

function addOntology() {
  ontologies.push({ name: '', description: '', attributes: [] })
}

function removeOntology(index) {
  ontologies.splice(index, 1)
}

function addRelation() {
  relations.push({ name: '', code: '', description: '' })
}

function removeRelation(index) {
  relations.splice(index, 1)
}

function addConstraint() {
  constraints.push({ source: '', relation: '', target: '' })
}

function removeConstraint(index) {
  constraints.splice(index, 1)
}

function buildSuggestionData() {
  return {
    category: {
      name: categoryName.value,
      description: categoryDesc.value,
    },
    ontologies: ontologies.map(o => ({
      name: o.name,
      description: o.description,
      attributes: o.attributes.map(a => ({
        name: a.name,
        code: a.code,
        data_type: a.data_type,
        is_required: !!a.is_required,
      })),
    })),
    relations: relations.map(r => ({
      name: r.name,
      code: r.code,
      description: r.description,
    })),
    constraints: constraints.map(c => ({
      source: c.source,
      relation: c.relation,
      target: c.target,
    })),
  }
}

async function save() {
  saving.value = true
  try {
    await updateOntologySuggestion(props.suggestionId, buildSuggestionData())
  } finally {
    saving.value = false
  }
}

async function approve() {
  saving.value = true
  try {
    await updateOntologySuggestion(props.suggestionId, buildSuggestionData())
    await approveOntologySuggestion(props.suggestionId)
    emit('done')
  } catch (e) {
    alert('操作失败：' + e.message)
  } finally {
    saving.value = false
  }
}

async function reject() {
  saving.value = true
  try {
    await rejectOntologySuggestion(props.suggestionId)
    emit('done')
  } catch (e) {
    alert('操作失败：' + e.message)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="sre-root">
    <div v-if="loading" class="sre-loading">加载中...</div>
    <div v-else-if="error" class="sre-error">{{ error }}</div>
    <template v-else-if="detail">
      <!-- Section 1: Category Info -->
      <div class="sre-section">
        <h3 class="sre-section-title">类别信息</h3>
        <div class="sre-form">
          <div class="sre-field">
            <label>类别名称</label>
            <input type="text" v-model="categoryName" placeholder="类别名称">
          </div>
          <div class="sre-field">
            <label>类别描述</label>
            <input type="text" v-model="categoryDesc" placeholder="类别描述">
          </div>
          <div class="sre-field">
            <label>置信度</label>
            <span class="sre-score">{{ (detail.suggestion_data?.score != null) ? (detail.suggestion_data.score * 100).toFixed(1) + '%' : '-' }}</span>
          </div>
        </div>
      </div>

      <!-- Section 2: Ontology List -->
      <div class="sre-section">
        <h3 class="sre-section-title">本体列表</h3>
        <div class="sre-ont-list">
          <div v-for="(ont, oi) in ontologies" :key="oi" class="sre-ont-card">
            <div class="sre-ont-head">
              <span class="sre-ont-index">#{{ oi + 1 }}</span>
              <span class="sre-spacer"></span>
              <button class="icon-btn sm rm-btn sm" @click="removeOntology(oi)" title="删除本体">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <div class="sre-form">
              <div class="sre-field">
                <label>本体名称</label>
                <input type="text" v-model="ont.name" placeholder="本体名称">
              </div>
              <div class="sre-field">
                <label>描述</label>
                <input type="text" v-model="ont.description" placeholder="本体描述">
              </div>
            </div>
            <div class="sre-attrs">
              <div class="sre-attrs-header">属性</div>
              <table v-if="ont.attributes.length" class="sre-attr-table">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>编码</th>
                    <th>数据类型</th>
                    <th>必填</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(attr, ai) in ont.attributes" :key="ai">
                    <td><input type="text" v-model="attr.name" placeholder="属性名"></td>
                    <td><input type="text" v-model="attr.code" placeholder="编码"></td>
                    <td>
                      <select v-model="attr.data_type">
                        <option v-for="dt in DATA_TYPE_OPTIONS" :key="dt" :value="dt">{{ dt }}</option>
                      </select>
                    </td>
                    <td class="sre-center"><input type="checkbox" v-model="attr.is_required"></td>
                    <td class="sre-center">
                      <button class="icon-btn sm rm-btn sm" @click="removeAttribute(oi, ai)" title="删除属性">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
              <div v-else class="sre-empty-hint">暂无属性</div>
              <button class="btn sm" @click="addAttribute(oi)">添加属性</button>
            </div>
          </div>
        </div>
        <button class="btn sm" @click="addOntology">添加本体</button>
      </div>

      <!-- Section 3: Relation Dictionary -->
      <div class="sre-section">
        <h3 class="sre-section-title">关系字典</h3>
        <div class="sre-rel-list">
          <div v-for="(rel, ri) in relations" :key="ri" class="sre-rel-row">
            <div class="sre-rel-fields">
              <input type="text" v-model="rel.name" placeholder="关系名称">
              <input type="text" v-model="rel.code" placeholder="关系编码">
              <input type="text" v-model="rel.description" placeholder="关系描述">
            </div>
            <button class="icon-btn sm rm-btn sm" @click="removeRelation(ri)" title="删除关系">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          <div v-if="!relations.length" class="sre-empty-hint">暂无关系</div>
        </div>
        <button class="btn sm" @click="addRelation">添加关系</button>
      </div>

      <!-- Section 4: Triple Constraints -->
      <div class="sre-section">
        <h3 class="sre-section-title">三元组约束</h3>
        <div class="sre-con-list">
          <div v-for="(con, ci) in constraints" :key="ci" class="sre-con-row">
            <div class="sre-con-fields">
              <input type="text" v-model="con.source" placeholder="源">
              <input type="text" v-model="con.relation" placeholder="关系">
              <input type="text" v-model="con.target" placeholder="目标">
            </div>
            <button class="icon-btn sm rm-btn sm" @click="removeConstraint(ci)" title="删除约束">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          <div v-if="!constraints.length" class="sre-empty-hint">暂无三元组约束</div>
        </div>
        <button class="btn sm" @click="addConstraint">添加三元组</button>
      </div>

      <!-- Action Buttons -->
      <div class="sre-actions">
        <button class="btn" @click="reject" :disabled="saving">拒绝</button>
        <button class="btn primary" @click="approve" :disabled="saving">
          <span v-if="saving" class="spinner"></span>
          审核通过并入库
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sre-root {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 900px;
  font-family: var(--font);
}

.sre-loading,
.sre-error {
  padding: 32px;
  text-align: center;
  font-size: 14px;
  color: var(--c-secondary);
}
.sre-error {
  color: var(--c-danger);
}

.sre-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.sre-section-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--c-fg);
  margin: 0;
}

.sre-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.sre-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sre-field label {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-secondary);
}
.sre-field input[type="text"],
.sre-field input[type="number"] {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-panel);
  color: var(--c-fg);
  font-size: 13px;
  font-family: var(--font);
  outline: none;
  box-sizing: border-box;
}
.sre-field input:focus {
  border-color: var(--c-primary);
}
.sre-score {
  font-size: 14px;
  font-weight: 600;
  color: var(--c-primary);
}

/* Ontology cards */
.sre-ont-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sre-ont-card {
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-panel);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.sre-ont-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sre-ont-index {
  font-size: 12px;
  font-weight: 700;
  color: var(--c-secondary);
  font-family: ui-monospace, Consolas, monospace;
}
.sre-spacer {
  flex: 1;
}

/* Attribute table */
.sre-attrs {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sre-attrs-header {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-secondary);
}
.sre-attr-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.sre-attr-table th {
  text-align: left;
  padding: 4px 6px;
  font-weight: 600;
  color: var(--c-secondary);
  font-size: 11px;
  border-bottom: 1px solid var(--c-border);
}
.sre-attr-table td {
  padding: 3px 4px;
  vertical-align: middle;
}
.sre-attr-table input[type="text"],
.sre-attr-table select {
  width: 100%;
  padding: 4px 6px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-bg);
  color: var(--c-fg);
  font-size: 12px;
  font-family: var(--font);
  outline: none;
  box-sizing: border-box;
}
.sre-attr-table input:focus,
.sre-attr-table select:focus {
  border-color: var(--c-primary);
}
.sre-attr-table select {
  min-width: 80px;
}
.sre-center {
  text-align: center;
}
.sre-center input[type="checkbox"] {
  cursor: pointer;
}
.sre-empty-hint {
  font-size: 12px;
  color: var(--c-secondary);
  font-style: italic;
  padding: 4px 0;
}

/* Relation rows */
.sre-rel-list,
.sre-con-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sre-rel-row,
.sre-con-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sre-rel-fields,
.sre-con-fields {
  display: flex;
  gap: 8px;
  flex: 1;
}
.sre-rel-fields input,
.sre-con-fields input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-panel);
  color: var(--c-fg);
  font-size: 13px;
  font-family: var(--font);
  outline: none;
  box-sizing: border-box;
}
.sre-rel-fields input:focus,
.sre-con-fields input:focus {
  border-color: var(--c-primary);
}

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 14px;
  font-size: 13px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-panel);
  color: var(--c-fg);
  cursor: pointer;
  font-family: var(--font);
  white-space: nowrap;
}
.btn:hover {
  background: var(--c-muted);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn.primary {
  background: var(--c-primary);
  color: #fff;
  border-color: var(--c-primary);
}
.btn.primary:hover {
  opacity: 0.9;
}
.btn.sm {
  padding: 4px 10px;
  font-size: 12px;
}

.icon-btn.sm {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--c-secondary);
  cursor: pointer;
  padding: 0;
  flex-shrink: 0;
}
.rm-btn.sm:hover {
  background: rgba(220, 38, 38, 0.1);
  color: var(--c-danger);
}

/* Actions bar */
.sre-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--c-border);
}

.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: sre-spin 0.6s linear infinite;
}
@keyframes sre-spin {
  to { transform: rotate(360deg); }
}
</style>
