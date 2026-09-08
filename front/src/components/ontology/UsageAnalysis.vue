<script setup>
import { ref, onMounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { getOntologyCategoryDetail, fetchOntologyUsages } from '../../api'

const props = defineProps({
  categoryId: { type: String, required: true },
})

const router = useRouter()
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

// 跳转到对应管理页（外部路由）；若当前已在本体管理内可附带 categoryId
function jumpTo(routeName, extraQuery = {}) {
  const q = { ...extraQuery }
  if (props.categoryId) q.from_category = props.categoryId
  router.push({ name: routeName, query: q })
}

// 点击 chip：若下方有对应明细则滚动定位；否则跳转到外部管理页
async function onChip(key) {
  const u = usages.value
  if (!u || !hasUsage(key)) return
  await nextTick()
  const el = document.querySelector(`.ua-block[data-key="${key}"]`)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    el.classList.add('flash')
    setTimeout(() => el.classList.remove('flash'), 1200)
    return
  }
  // 没有下方明细的 chip：跳到外部页
  const map = {
    constraint_count: ['ontology-constraints'],
    entity_count: ['entities', { ontology_id: selectedId.value }],
    interfaces: null,        // 明细在本页
    services: null,           // 明细在本页
    functions: ['ontology-functions'],
    derived_properties: ['ontology-functions'],
    object_views: null,       // 明细在本页
    kb_bindings: ['kb'],
  }
  const target = map[key]
  if (target) jumpTo(target[0], target[1] || {})
}

watch(() => props.categoryId, async () => { await loadOntologies(); await analyze() })
onMounted(async () => { await loadOntologies(); await analyze() })
</script>

<template>
  <div class="ua-root">
    <div class="ua-head">
      <span class="ua-tip">影响分析（Usages）：用于<strong>删除 / 改名本体前</strong>预览影响面——查看该本体被哪些约束、接口实现、动作、函数、派生属性、对象视图、实体、知识库（KB）引用，数字越高代表波及范围越大；显示「暂无其他对象依赖」即可安全删除/改名。</span>
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
        <button class="ua-chip" :class="{ hot: usages.constraint_count }" data-key="constraint_count" @click="onChip('constraint_count')" title="跳到本体关系约束管理">三元组约束 {{ usages.constraint_count }}</button>
        <button class="ua-chip" :class="{ hot: usages.entity_count }" data-key="entity_count" @click="onChip('entity_count')" title="跳到该本体的实体列表">实体 {{ usages.entity_count }}</button>
        <button class="ua-chip" :class="{ hot: usages.interfaces?.length }" data-key="interfaces" @click="onChip('interfaces')" title="查看接口实现明细">接口实现 {{ usages.interfaces?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.services?.length }" data-key="services" @click="onChip('services')" title="查看动作明细">动作 {{ usages.services?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.functions?.length }" data-key="functions" @click="onChip('functions')" title="跳到函数管理">函数 {{ usages.functions?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.derived_properties?.length }" data-key="derived_properties" @click="onChip('derived_properties')" title="跳到函数与派生属性">派生属性 {{ usages.derived_properties?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.object_views?.length }" data-key="object_views" @click="onChip('object_views')" title="查看对象视图明细">对象视图 {{ usages.object_views?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.kb_bindings?.length }" data-key="kb_bindings" @click="onChip('kb_bindings')" title="跳到知识库列表">KB 绑定 {{ usages.kb_bindings?.length || 0 }}</button>
      </div>

      <div v-if="usages.interfaces?.length" class="ua-block" data-key="interfaces">
        <div class="ua-block-title">接口实现</div>
        <div v-for="i in usages.interfaces" :key="i.interface_id" class="ua-item">接口：{{ i.interface_name }}</div>
      </div>
      <div v-if="usages.services?.length" class="ua-block" data-key="services">
        <div class="ua-block-title">动作</div>
        <div v-for="s in usages.services" :key="s.id" class="ua-item">{{ s.name }}</div>
      </div>
      <div v-if="usages.functions?.length" class="ua-block" data-key="functions">
        <div class="ua-block-title">函数</div>
        <div v-for="f in usages.functions" :key="f.id" class="ua-item">{{ f.name }}</div>
      </div>
      <div v-if="usages.derived_properties?.length" class="ua-block" data-key="derived_properties">
        <div class="ua-block-title">派生属性</div>
        <div v-for="d in usages.derived_properties" :key="d.id" class="ua-item">{{ d.name }}</div>
      </div>
      <div v-if="usages.object_views?.length" class="ua-block" data-key="object_views">
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
.ua-chip { font-size: 12px; padding: 3px 10px; border-radius: 11px; background: var(--c-muted); color: var(--c-secondary); border: 1px solid transparent; cursor: pointer; font-family: var(--font); transition: background 120ms, border-color 120ms, transform 120ms; }
.ua-chip:hover { background: var(--c-border); }
.ua-chip:active { transform: scale(0.97); }
.ua-chip:disabled { cursor: not-allowed; opacity: 0.6; }
.ua-chip[disabled] { cursor: not-allowed; }
.ua-chip.hot { color: var(--c-danger); border-color: rgba(220, 38, 38, 0.4); }
.ua-chip.hot:hover { background: rgba(220, 38, 38, 0.12); border-color: var(--c-danger); }
.ua-block { border-top: 1px solid var(--c-border); padding-top: 8px; transition: background 600ms; }
.ua-block.flash { background: rgba(14, 116, 144, 0.12); }
.ua-block-title { font-size: 12px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.ua-item { font-size: 12px; color: var(--c-fg); padding: 2px 0; }
.ua-none { padding: 12px; font-size: 12px; color: #16a34a; }
</style>
