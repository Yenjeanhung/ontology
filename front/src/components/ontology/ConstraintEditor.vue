<script>
// 模块级持久化视图模式 —— 即使组件被 v-if 销毁重建也不会丢失
import { ref } from 'vue'
const _persistedView = ref('list')
</script>

<script setup>
import { ref, computed, watch } from 'vue'
import { createConstraint, deleteConstraint } from '../../api'
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

const hasOntologies = computed(() => props.ontologies.length > 0)
const hasRelations = computed(() => props.relations.length > 0)

async function submit() {
  if (!canCreate.value) return
  error.value = ''
  creating.value = true
  try {
    await createConstraint(props.categoryId, {
      source_ontology_id: sourceId.value,
      relation_id: relationId.value,
      target_ontology_id: targetId.value,
    })
    sourceId.value = null
    relationId.value = null
    targetId.value = null
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
              @change="relationId = null; targetId = null"
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
            />
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
        <div v-if="error" class="ce-error">{{ error }}</div>
      </div>

      <!-- 现有约束列表 -->
      <div class="ce-list" v-if="pagedConstraints.length">
        <div v-for="c in pagedConstraints" :key="c.id" class="ce-item">
          <div class="ce-tri-display">
            <span class="ce-node">{{ c.source_ontology_name }}</span>
            <span class="ce-rel">—{{ c.relation_name }}→</span>
            <span class="ce-node">{{ c.target_ontology_name }}</span>
          </div>
          <button class="rm-btn sm" @click="remove(c)" title="删除">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
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
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 14px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel);
}
.ce-tri-display { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
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
