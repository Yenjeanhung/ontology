<script setup>
import { ref, computed, watch } from 'vue'
import { createConstraint, deleteConstraint } from '../../api'
import SearchableSelect from '../common/SearchableSelect.vue'

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
      <div class="ce-list-head" v-if="constraints.length">
        已定义 {{ constraints.length }} 个三元组约束
      </div>
      <div class="ce-list" v-if="constraints.length">
        <div v-for="c in constraints" :key="c.id" class="ce-item">
          <div class="ce-tri-display">
            <span class="ce-node">{{ c.source_ontology_name }}</span>
            <span class="ce-rel">—{{ c.relation_name }}→</span>
            <span class="ce-node">{{ c.target_ontology_name }}</span>
          </div>
          <button class="rm-btn sm" @click="remove(c)" title="删除">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
      </div>

      <div v-else class="ce-empty">
        暂无三元组约束，使用上方表单添加
      </div>
    </template>
  </div>
</template>

<style scoped>
.ce-root { display: flex; flex-direction: column; gap: 16px; max-width: 880px; }

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
  background: var(--c-panel); padding: 16px 18px;
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
.ce-list { display: flex; flex-direction: column; gap: 6px; }
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
.ce-empty { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }
</style>
