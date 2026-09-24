<script>
// 模块级持久化视图模式 —— 即使组件被 v-if 销毁重建也不会丢失
import { ref } from 'vue'
const _persistedView = ref('list')
</script>

<script setup>
import { ref, computed, watch } from 'vue'
import { createConstraint, updateConstraint, deleteConstraint } from '../../api'
import SearchableSelect from '../common/SearchableSelect.vue'
import RelationGraph from './RelationGraph.vue'
import Pagination from '../common/Pagination.vue'

const props = defineProps({
  categoryId: { type: String, required: true },
  ontologies: { type: Array, default: () => [] },
  relations: { type: Array, default: () => [] },
  constraints: { type: Array, default: () => [] },
})
const emit = defineEmits(['changed'])

const sourceId = ref(null)
const relationId = ref(null)
const targetId = ref(null)
const creating = ref(false)
const error = ref('')

// 直接用模块级 ref，不需要 computed 包装
const viewMode = _persistedView

// 搜索
const searchQuery = ref('')

const filteredConstraints = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return props.constraints
  return props.constraints.filter(c =>
    (c.source_ontology_name || '').toLowerCase().includes(q) ||
    (c.target_ontology_name || '').toLowerCase().includes(q) ||
    (c.relation_name || '').toLowerCase().includes(q)
  )
})

const page = ref(1)
const pageSize = ref(10)
const pagedConstraints = computed(() =>
  filteredConstraints.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value)
)
watch(searchQuery, () => { page.value = 1 })
watch(() => props.constraints, () => { page.value = 1 }, { deep: true })

const ontologyOptions = computed(() =>
  props.ontologies.map(o => ({ value: o.id, label: o.name, meta: o.description || '' }))
)

const relationOptions = computed(() =>
  props.relations.map(r => ({ value: r.id, label: r.name, meta: r.description || '' }))
)

const canCreate = computed(() => sourceId.value && relationId.value && targetId.value)

// ── 基数（source→target 视角）：真源在约束层（映射 source_max/target_max，NL2SQL 翻倍防护按此判读）──
const CARD_OPTIONS = [
  { value: 'ONE_TO_ONE', label: '一对一 (1:1)' },
  { value: 'ONE_TO_MANY', label: '一对多 (1:N)' },
  { value: 'MANY_TO_ONE', label: '多对一 (N:1)' },
  { value: 'MANY_TO_MANY', label: '多对多 (N:N)' },
]
function cardLabel(c) { return (CARD_OPTIONS.find(o => o.value === c) || {}).label || '' }
const newCard = ref('MANY_TO_MANY')
const editCard = ref('MANY_TO_MANY')

// ── 关联字段映射（join_condition）：NL2SQL 生成 ON 条件的来源，支持多字段对 ──
function attrOptions(ontologyId) {
  const o = props.ontologies.find(x => x.id === ontologyId)
  return (o?.attributes || []).map(a => ({
    value: a.code, label: a.code === a.name ? a.code : `${a.code}（${a.name}）`,
  }))
}
const validJoin = rows => (rows || []).filter(r => r.left && r.right).map(r => ({ left: r.left, right: r.right }))
const newJoin = ref([{ left: '', right: '' }])
const editJoin = ref([])
function joinText(c) {
  return (c.join_condition || []).map(j => `${j.left} = ${j.right}`).join(' AND ')
}

const hasOntologies = computed(() => props.ontologies.length > 0)
const hasRelations = computed(() => props.relations.length > 0)

async function submit() {
  if (!canCreate.value) return
  error.value = ''
  // 与后端同规则的预校验：每对 source-target 本体只允许一条约束（不限关系与方向）
  const dup = props.constraints.find(
    c => c.source_ontology_id === sourceId.value && c.target_ontology_id === targetId.value
  )
  if (dup) {
    error.value = `「${dup.source_ontology_name}」与「${dup.target_ontology_name}」之间已存在关系约束（每对本体只能建立一个关系）；如需调整请使用该行的编辑功能`
    return
  }
  creating.value = true
  try {
    await createConstraint(props.categoryId, {
      source_ontology_id: sourceId.value,
      relation_id: relationId.value,
      target_ontology_id: targetId.value,
      join_condition: validJoin(newJoin.value),
      cardinality: newCard.value,
    })
    sourceId.value = null
    relationId.value = null
    targetId.value = null
    newJoin.value = [{ left: '', right: '' }]
    emit('changed')
  } catch (e) {
    error.value = '创建失败：' + e.message
  } finally {
    creating.value = false
  }
}

async function remove(c) {
  const label = `${c.source_ontology_name} —${c.relation_name}→ ${c.target_ontology_name}`
  if (!confirm(`确认删除三元组约束「${label}」？`)) return
  try {
    await deleteConstraint(props.categoryId, c.id)
    emit('changed')
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

function resetForm() {
  sourceId.value = null
  relationId.value = null
  targetId.value = null
  error.value = ''
}

// ── 弹窗编辑：改起点/关系/终点与关联字段（PUT /constraints/{id} 支持三端与描述）──
const editingId = ref('')
const editSourceId = ref(null)
const editRelationId = ref(null)
const editTargetId = ref(null)
const saving = ref(false)

function openEdit(c) {
  editingId.value = c.id
  editSourceId.value = c.source_ontology_id || null
  editRelationId.value = c.relation_id || null
  editTargetId.value = c.target_ontology_id || null
  editCard.value = c.cardinality || 'MANY_TO_MANY'
  editJoin.value = (c.join_condition && c.join_condition.length)
    ? c.join_condition.map(j => ({ left: j.left, right: j.right }))
    : [{ left: '', right: '' }]
  error.value = ''
}

function cancelEdit() {
  editingId.value = ''
  error.value = ''
}

async function saveEdit() {
  if (!editSourceId.value || !editRelationId.value || !editTargetId.value) {
    error.value = '请完整选择起点、关系与终点'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await updateConstraint(props.categoryId, editingId.value, {
      source_ontology_id: editSourceId.value,
      relation_id: editRelationId.value,
      target_ontology_id: editTargetId.value,
      join_condition: validJoin(editJoin.value),
      cardinality: editCard.value,
    })
    editingId.value = ''
    emit('changed')
  } catch (e) {
    error.value = '保存失败：' + e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="ce-root">
    <!-- 前置检查 -->
    <div v-if="!hasOntologies || !hasRelations" class="ce-prereq">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      <div>
        <div class="ce-prereq-title">需要先定义本体与关系</div>
        <div class="ce-prereq-desc">
          <span v-if="!hasOntologies">尚无本体，请到「本体」Tab 添加本体。</span>
          <span v-if="!hasOntologies && !hasRelations"> </span>
          <span v-if="!hasRelations">尚无关系，请到「关系字典」Tab 添加关系。</span>
        </div>
      </div>
    </div>

    <template v-else>
      <!-- 视图切换 + 搜索 -->
      <div class="ce-bar">
        <span class="ce-bar-count">已定义 {{ constraints.length }} 个三元组约束<span v-if="searchQuery">，筛选出 {{ filteredConstraints.length }} 个</span></span>
        <div class="ce-bar-right">
          <div class="ce-search">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="ce-search-icon"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input
              v-model="searchQuery"
              type="text"
              class="ce-search-input"
              placeholder="搜索本体名或关系名..."
            />
            <button v-if="searchQuery" class="ce-search-clear" @click="searchQuery = ''" title="清除">✕</button>
          </div>
          <div class="ce-view-toggle">
            <button class="ce-vbtn" :class="{ on: viewMode === 'list' }" @click="viewMode = 'list'">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="3.5" cy="6" r="1"/><circle cx="3.5" cy="12" r="1"/><circle cx="3.5" cy="18" r="1"/></svg>
              列表视图
            </button>
            <button class="ce-vbtn" :class="{ on: viewMode === 'graph' }" @click="viewMode = 'graph'">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><line x1="8" y1="7" x2="16" y2="7"/><line x1="7" y1="8.5" x2="11" y2="16"/><line x1="17" y1="8.5" x2="13" y2="16"/></svg>
              图谱视图
            </button>
          </div>
        </div>
      </div>

      <!-- 列表视图 -->
      <template v-if="viewMode === 'list'">
      <!-- 新建三元组 -->
      <div class="ce-builder">
        <div class="ce-builder-head">
          <span class="ce-builder-title">添加三元组约束</span>
          <span class="ce-builder-tip">选择 起点 → 关系 → 终点，约束抽取时仅生成符合的三元组</span>
        </div>
        <div class="ce-triplet">
          <div class="ce-pick">
            <label class="ce-pick-label">起点本体</label>
            <SearchableSelect
              v-model="sourceId"
              :options="ontologyOptions"
              placeholder="选择起点本体..."
              @change="relationId = null; targetId = null; newJoin = [{ left: '', right: '' }]"
            />
          </div>
          <div class="ce-arrow">→</div>
          <div class="ce-pick">
            <label class="ce-pick-label">关系</label>
            <SearchableSelect
              v-model="relationId"
              :options="relationOptions"
              placeholder="选择关系..."
            />
          </div>
          <div class="ce-arrow">→</div>
          <div class="ce-pick">
            <label class="ce-pick-label">终点本体</label>
            <SearchableSelect
              v-model="targetId"
              :options="ontologyOptions"
              placeholder="选择终点本体..."
              @change="newJoin = [{ left: '', right: '' }]"
            />
          </div>
          <div class="ce-pick ce-card-pick">
            <label class="ce-pick-label">基数</label>
            <select v-model="newCard" class="ce-card-select" title="起点→终点视角的基数，用于 NL2SQL join 翻倍防护">
              <option v-for="o in CARD_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </div>
          <button
            class="btn primary ce-add-btn"
            @click="submit"
            :disabled="!canCreate || creating"
          >
            <span v-if="creating" class="spinner"></span>
            添加
          </button>
        </div>
        <div v-if="sourceId && targetId" class="ce-join-box">
          <div class="ce-join-head">
            <span class="ce-join-title">关联字段（生成 SQL 的 ON 条件，可配多个）</span>
            <button class="ce-join-add" @click="newJoin.push({ left: '', right: '' })">＋ 字段对</button>
          </div>
          <div v-for="(j, i) in newJoin" :key="'nj' + i" class="ce-join-row">
            <SearchableSelect v-model="j.left" :options="attrOptions(sourceId)" placeholder="起点字段" />
            <span class="ce-join-eq">=</span>
            <SearchableSelect v-model="j.right" :options="attrOptions(targetId)" placeholder="终点字段" />
            <button v-if="newJoin.length > 1" class="rm-btn sm" @click="newJoin.splice(i, 1)" title="移除">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
        </div>
        <div v-if="error" class="ce-error">{{ error }}</div>
      </div>

      <!-- 现有约束列表 -->
      <div class="ce-list" v-if="pagedConstraints.length">
        <div v-for="c in pagedConstraints" :key="c.id" class="ce-item">
          <div>
            <div class="ce-tri-display">
              <span class="ce-node">{{ c.source_ontology_name }}</span>
              <span class="ce-rel">—{{ c.relation_name }}→</span>
              <span class="ce-node">{{ c.target_ontology_name }}</span>
              <span v-if="c.cardinality" class="ce-card-pill">{{ cardLabel(c.cardinality) }}</span>
            </div>
            <div v-if="c.join_condition && c.join_condition.length" class="ce-join-line">
              关联：{{ c.source_ontology_name }}.{{ (c.join_condition[0] || {}).left }} = {{ c.target_ontology_name }}.{{ (c.join_condition[0] || {}).right }}<template v-if="c.join_condition.length > 1"> 等 {{ c.join_condition.length }} 对字段</template>
            </div>
          </div>
          <div class="ce-item-ops">
            <button class="rm-btn sm" @click="openEdit(c)" title="编辑">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
            </button>
            <button class="rm-btn sm" @click="remove(c)" title="删除">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </div>
        <Pagination v-if="filteredConstraints.length > pageSize" v-model:page="page" v-model:page-size="pageSize" :total="filteredConstraints.length" />
      </div>

      <div v-else class="ce-empty">
        {{ searchQuery ? '未找到匹配的三元组约束' : '暂无三元组约束，使用上方表单添加' }}
      </div>
      </template>

      <!-- 图谱视图（v-show 保持挂载，避免数据刷新时跳回列表） -->
      <RelationGraph
        v-show="viewMode === 'graph'"
        :constraints="filteredConstraints"
        :all-constraints="constraints"
        :categoryId="categoryId"
        :ontologies="ontologies"
        :relations="relations"
        :search-query="searchQuery"
        @changed="emit('changed')"
      />
    </template>

    <!-- 编辑三元组约束弹窗 -->
    <Teleport to="body">
      <div v-if="editingId" class="ce-modal-mask" @click.self="cancelEdit">
        <div class="ce-modal">
          <div class="ce-modal-head">
            <h3>编辑三元组约束</h3>
            <button class="ce-modal-close" @click="cancelEdit">✕</button>
          </div>
          <div class="ce-modal-body">
            <div class="ce-tri-grid">
              <div class="ce-pick">
                <label class="ce-pick-label">起点本体</label>
                <SearchableSelect v-model="editSourceId" :options="ontologyOptions" placeholder="起点本体" @change="editJoin = [{ left: '', right: '' }]" />
              </div>
              <div class="ce-pick">
                <label class="ce-pick-label">关系</label>
                <SearchableSelect v-model="editRelationId" :options="relationOptions" placeholder="关系" />
              </div>
              <div class="ce-pick">
                <label class="ce-pick-label">终点本体</label>
                <SearchableSelect v-model="editTargetId" :options="ontologyOptions" placeholder="终点本体" @change="editJoin = [{ left: '', right: '' }]" />
              </div>
            </div>
            <div class="ce-modal-card-row">
              <label class="ce-pick-label">基数（起点 → 终点）</label>
              <select v-model="editCard" class="ce-card-select">
                <option v-for="o in CARD_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
            <div v-if="editSourceId && editTargetId" class="ce-join-box">
              <div class="ce-join-head">
                <span class="ce-join-title">关联字段（生成 SQL 的 ON 条件，可配多个）</span>
                <button class="ce-join-add" @click="editJoin.push({ left: '', right: '' })">＋ 字段对</button>
              </div>
              <div v-for="(j, i) in editJoin" :key="'ej' + i" class="ce-join-row">
                <SearchableSelect v-model="j.left" :options="attrOptions(editSourceId)" placeholder="起点字段" />
                <span class="ce-join-eq">=</span>
                <SearchableSelect v-model="j.right" :options="attrOptions(editTargetId)" placeholder="终点字段" />
                <button v-if="editJoin.length > 1" class="rm-btn sm" @click="editJoin.splice(i, 1)" title="移除">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
            </div>
            <div v-if="error" class="ce-error">{{ error }}</div>
          </div>
          <div class="ce-modal-foot">
            <button class="btn" @click="cancelEdit">取消</button>
            <button class="btn primary" :disabled="saving || !editSourceId || !editRelationId || !editTargetId" @click="saveEdit">
              <span v-if="saving" class="spinner"></span>
              保存
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.ce-root { display: flex; flex-direction: column; gap: 16px; }

.ce-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.ce-bar-count { font-size: 13px; font-weight: 600; color: var(--c-secondary); flex-shrink: 0; }
.ce-bar-right { display: flex; align-items: center; gap: 10px; }
.ce-search { position: relative; display: flex; align-items: center; }
.ce-search-icon { position: absolute; left: 10px; color: var(--c-secondary); pointer-events: none; }
.ce-search-input {
  width: 200px; height: 33px; padding: 0 30px 0 30px;
  border: 1px solid var(--c-border); border-radius: 10px;
  background: var(--c-panel); color: var(--c-fg); font-size: 12px; outline: none;
  transition: border-color 150ms, width 200ms;
}
.ce-search-input:focus { border-color: #8bb5f5; width: 240px; }
.ce-search-input::placeholder { color: var(--c-secondary); font-size: 11px; }
.ce-search-clear {
  position: absolute; right: 4px; width: 22px; height: 22px; display: inline-flex;
  align-items: center; justify-content: center; border: 0; border-radius: 6px;
  background: transparent; color: var(--c-secondary); font-size: 11px; cursor: pointer;
}
.ce-search-clear:hover { background: var(--c-muted); color: var(--c-fg); }
.ce-view-toggle { display: inline-flex; border: 1px solid var(--c-border); border-radius: 10px; overflow: hidden; flex-shrink: 0; }
.ce-vbtn {
  height: 34px; padding: 0 14px; border: 0; background: transparent;
  color: var(--c-secondary); font-weight: 600; font-size: 12px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px; transition: background 150ms, color 150ms;
}
.ce-vbtn:hover { color: var(--c-fg); }
.ce-vbtn.on { background: var(--c-fg); color: var(--c-bg); }

.ce-prereq {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 18px 20px; border: 1px solid var(--c-accent); border-radius: var(--radius);
  background: rgba(161, 98, 7, 0.06); color: var(--c-accent);
}
.ce-prereq svg { flex-shrink: 0; margin-top: 2px; }
.ce-prereq-title { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
.ce-prereq-desc { font-size: 12px; opacity: 0.85; }

.ce-builder {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); padding: 16px 18px; max-width: 880px;
}
.ce-builder-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 14px; }
.ce-builder-title { font-size: 14px; font-weight: 700; color: var(--c-fg); }
.ce-builder-tip { font-size: 12px; color: var(--c-secondary); }

.ce-triplet { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; }
.ce-pick { flex: 1; min-width: 160px; display: flex; flex-direction: column; gap: 4px; }
.ce-pick-label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.ce-arrow { font-size: 18px; color: var(--c-secondary); padding-bottom: 8px; flex-shrink: 0; }
.ce-add-btn { flex-shrink: 0; }
.ce-error { color: var(--c-danger); font-size: 12px; margin-top: 8px; }

.ce-list-head { font-size: 13px; font-weight: 600; color: var(--c-secondary); }
.ce-list { display: flex; flex-direction: column; gap: 6px; max-width: 880px; }
.ce-item {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  padding: 10px 14px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel);
}
.ce-join-box { width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }
.ce-join-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.ce-join-title { font-size: 11px; color: var(--c-secondary); }
.ce-join-add { font-size: 11px; padding: 1px 8px; border: 1px solid var(--c-border); border-radius: 999px;
  background: transparent; color: var(--c-secondary); cursor: pointer; }
.ce-join-add:hover { color: var(--c-accent); border-color: var(--c-accent); }
.ce-join-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.ce-join-row:last-child { margin-bottom: 0; }
.ce-join-row > *:first-child, .ce-join-row > *:nth-child(3) { flex: 1; min-width: 110px; }
.ce-join-eq { flex-shrink: 0; font-size: 12px; font-weight: 700; color: var(--c-accent); }
.ce-join-line { margin-top: 5px; font-size: 11px; color: var(--c-secondary);
  font-family: var(--font-mono, ui-monospace, Consolas, monospace); }
.ce-tri-display { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ce-card-pill { font-size: 11px; padding: 1px 8px; border-radius: 9px; background: rgba(59, 130, 246, 0.14); color: #60a5fa; flex-shrink: 0; }
.ce-card-select {
  height: 33px; padding: 0 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 12px; outline: none; width: 100%;
  box-sizing: border-box;
}
.ce-card-select:focus { border-color: var(--c-fg); }
.ce-card-pick { flex: 0 0 140px; }
.ce-modal-card-row { display: flex; flex-direction: column; gap: 5px; }
.ce-modal-card-row select { height: 34px; }

/* 编辑弹窗 */
.ce-modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.55); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 20px; }
.ce-modal { width: 640px; max-width: 92vw; max-height: 86vh; overflow-y: auto; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius); box-shadow: 0 20px 60px rgba(0,0,0,0.35); }
.ce-modal-head { display: flex; align-items: center; justify-content: space-between; padding: 13px 16px; border-bottom: 1px solid var(--c-border); }
.ce-modal-head h3 { margin: 0; font-size: 14px; font-weight: 700; color: var(--c-fg); }
.ce-modal-close { border: 0; background: transparent; color: var(--c-secondary); font-size: 16px; cursor: pointer; padding: 2px 6px; border-radius: 6px; }
.ce-modal-close:hover { background: var(--c-muted); color: var(--c-fg); }
.ce-modal-body { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.ce-modal-foot { display: flex; justify-content: flex-end; gap: 10px; padding: 13px 16px; border-top: 1px solid var(--c-border); }
.ce-tri-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
.ce-tri-grid .ce-pick { min-width: 0; }
@media (max-width: 640px) { .ce-tri-grid { grid-template-columns: 1fr; } }
.ce-item-ops { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.ce-item > .ce-error { width: 100%; }
.ce-node {
  font-size: 13px; font-weight: 600; color: var(--c-fg);
  padding: 3px 10px; border-radius: 12px; background: var(--c-muted);
}
.ce-rel { font-size: 12px; color: var(--c-accent); font-weight: 600; }
.rm-btn.sm {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm);
  background: transparent; color: var(--c-secondary); cursor: pointer; flex-shrink: 0;
}
.rm-btn.sm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.ce-empty { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); max-width: 880px; }
</style>
