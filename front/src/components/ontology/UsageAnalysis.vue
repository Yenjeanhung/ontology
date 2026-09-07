<script setup>
import { ref, onMounted, watch } from 'vue'
import { getOntologyCategoryDetail, fetchOntologyUsages } from '../../api'

const props = defineProps({
  categoryId: { type: String, required: true },
})

const ontologies = ref([])
const selectedId = ref('')
const usages = ref(null)
const loading = ref(false)

async function loadOntologies() {
  if (!props.categoryId) return
  try {
    const d = await getOntologyCategoryDetail(props.categoryId)
    ontologies.value = d?.ontologies || []
    if (ontologies.value.length && !selectedId.value) selectedId.value = ontologies.value[0].id
  } catch {
    ontologies.value = []
  }
}

async function analyze() {
  if (!selectedId.value) return
  loading.value = true
  usages.value = null
  try {
    usages.value = await fetchOntologyUsages(props.categoryId, selectedId.value)
  } catch (e) {
    alert('影响分析失败：' + e.message)
  } finally {
    loading.value = false
  }
}

const hasUsage = (key) => usages.value && (usages.value[key]?.length || usages.value[key] > 0)

watch(() => props.categoryId, async () => { await loadOntologies(); await analyze() })
onMounted(async () => { await loadOntologies(); await analyze() })
</script>

<template>
  <div class="ua-root">
    <div class="ua-head">
      <span class="ua-tip">影响分析（Usages）：删除 / 改名本体前，预览其约束、接口实现、动作、函数、派生属性、视图、实体与 KB 绑定影响面。</span>
    </div>
    <div class="ua-toolbar">
      <select v-model="selectedId" class="ua-select" @change="analyze">
        <option value="">选择本体…</option>
        <option v-for="o in ontologies" :key="o.id" :value="o.id">{{ o.name }}（{{ o.code || o.id }}）</option>
      </select>
      <button class="btn sm primary" :disabled="!selectedId || loading" @click="analyze">
        <span v-if="loading" class="spinner"></span> 分析
      </button>
    </div>

    <div v-if="!usages" class="ua-empty">选择本体后点击「分析」。</div>
    <div v-else class="ua-body">
      <div class="ua-summary">
        <div class="ua-chip" :class="{ hot: usages.constraint_count }">三元组约束 {{ usages.constraint_count }}</div>
        <div class="ua-chip" :class="{ hot: usages.entity_count }">实体 {{ usages.entity_count }}</div>
        <div class="ua-chip" :class="{ hot: usages.interfaces?.length }">接口实现 {{ usages.interfaces?.length || 0 }}</div>
        <div class="ua-chip" :class="{ hot: usages.services?.length }">动作 {{ usages.services?.length || 0 }}</div>
        <div class="ua-chip" :class="{ hot: usages.functions?.length }">函数 {{ usages.functions?.length || 0 }}</div>
        <div class="ua-chip" :class="{ hot: usages.derived_properties?.length }">派生属性 {{ usages.derived_properties?.length || 0 }}</div>
        <div class="ua-chip" :class="{ hot: usages.object_views?.length }">对象视图 {{ usages.object_views?.length || 0 }}</div>
        <div class="ua-chip" :class="{ hot: usages.kb_bindings?.length }">KB 绑定 {{ usages.kb_bindings?.length || 0 }}</div>
      </div>

      <div v-if="usages.interfaces?.length" class="ua-block">
        <div class="ua-block-title">接口实现</div>
        <div v-for="i in usages.interfaces" :key="i.interface_id" class="ua-item">接口：{{ i.interface_name }}</div>
      </div>
      <div v-if="usages.services?.length" class="ua-block">
        <div class="ua-block-title">动作</div>
        <div v-for="s in usages.services" :key="s.id" class="ua-item">{{ s.name }}</div>
      </div>
      <div v-if="usages.functions?.length" class="ua-block">
        <div class="ua-block-title">函数</div>
        <div v-for="f in usages.functions" :key="f.id" class="ua-item">{{ f.name }}</div>
      </div>
      <div v-if="usages.derived_properties?.length" class="ua-block">
        <div class="ua-block-title">派生属性</div>
        <div v-for="d in usages.derived_properties" :key="d.id" class="ua-item">{{ d.name }}</div>
      </div>
      <div v-if="usages.object_views?.length" class="ua-block">
        <div class="ua-block-title">对象视图</div>
        <div v-for="v in usages.object_views" :key="v.id" class="ua-item">{{ v.name }}</div>
      </div>
      <div v-if="!usages.constraint_count && !usages.entity_count && !usages.interfaces?.length && !usages.services?.length && !usages.functions?.length && !usages.derived_properties?.length && !usages.object_views?.length && !usages.kb_bindings?.length" class="ua-none">
        该本体暂无其他对象依赖，可安全删除 / 改名。
      </div>
    </div>
  </div>
</template>

<style scoped>
.ua-root { display: flex; flex-direction: column; gap: 12px; }
.ua-tip { font-size: 12px; color: var(--c-secondary); }
.ua-toolbar { display: flex; gap: 8px; }
.ua-select { width: 320px; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; }
.ua-select:focus { border-color: var(--c-fg); }
.btn.sm { padding: 5px 11px; font-size: 12px; }
.ua-empty { padding: 24px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }
.ua-summary { display: flex; gap: 8px; flex-wrap: wrap; }
.ua-chip { font-size: 12px; padding: 3px 10px; border-radius: 11px; background: var(--c-muted); color: var(--c-secondary); border: 1px solid transparent; }
.ua-chip.hot { color: var(--c-danger); border-color: rgba(220, 38, 38, 0.4); }
.ua-block { border-top: 1px solid var(--c-border); padding-top: 8px; }
.ua-block-title { font-size: 12px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.ua-item { font-size: 12px; color: var(--c-fg); padding: 2px 0; }
.ua-none { padding: 12px; font-size: 12px; color: #16a34a; }
</style>
