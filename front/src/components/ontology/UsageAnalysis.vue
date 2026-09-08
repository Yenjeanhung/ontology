<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getOntologyCategoryDetail, fetchOntologyUsages } from '../../api'
import ModalDialog from '../common/ModalDialog.vue'

const props = defineProps({
  categoryId: { type: String, required: true },
})

const router = useRouter()

const ontologies = ref([])
const selectedId = ref('')
const usages = ref(null)
const loading = ref(false)

// 点击 chip 后弹窗的上下文：{ key, title, items, count, countOnly, hint }
const viewing = ref(null)

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
  viewing.value = null
  try {
    usages.value = await fetchOntologyUsages(props.categoryId, selectedId.value)
  } catch (e) {
    alert('影响分析失败：' + e.message)
  } finally {
    loading.value = false
  }
}

const hasUsage = (key) => usages.value && (usages.value[key]?.length || usages.value[key] > 0)

// 每个 chip 的展示元数据：title / items / count / hint
const chipMeta = computed(() => {
  const u = usages.value || {}
  const cat = props.categoryId
  const ontId = selectedId.value
  return {
    constraint_count: {
      title: '三元组约束',
      count: u.constraint_count || 0,
      items: [],
      countOnly: true,
      hint: '本体关系（约束）里以该本体为源/目标的规则数。',
      page: { name: 'ontology-constraints', label: '打开「本体关系」管理页' },
    },
    entity_count: {
      title: '实体',
      count: u.entity_count || 0,
      items: [],
      countOnly: true,
      hint: '当前本体下的实体实例数。',
      page: { name: 'entities', query: { ontology_id: ontId }, label: '打开「实体」列表（按该本体筛选）' },
    },
    interfaces: {
      title: '接口实现',
      count: u.interfaces?.length || 0,
      items: (u.interfaces || []).map(i => ({ id: i.interface_id, name: i.interface_name })),
      countOnly: false,
      hint: '本体挂到该接口上后会参与多态查询；删除前需先解除挂接。',
      page: { name: 'ontology-ontologies', query: { category: cat, tab: 'iface' }, label: '打开「接口」管理页' },
    },
    services: {
      title: '动作',
      count: u.services?.length || 0,
      items: (u.services || []).map(s => ({ id: s.id, name: s.name })),
      countOnly: false,
      hint: '本体级动作（服务）引用了该本体的属性 / 关系。点击某项可直接打开该动作的编辑器。',
      // 动作可直接打开服务编辑器
      itemRoute: (item) => ({ name: 'ontology-service-edit', params: { serviceId: item.id } }),
      page: { name: 'ontology-ontologies', query: { category: cat, tab: 'ont' }, label: '打开「本体定义」管理页' },
    },
    functions: {
      title: '函数',
      count: u.functions?.length || 0,
      items: (u.functions || []).map(f => ({ id: f.id, name: f.name })),
      countOnly: false,
      hint: '函数参数或代码里引用了该本体。',
      page: { name: 'ontology-functions', label: '打开「函数与派生属性」管理页' },
    },
    derived_properties: {
      title: '派生属性',
      count: u.derived_properties?.length || 0,
      items: (u.derived_properties || []).map(d => ({ id: d.id, name: d.name })),
      countOnly: false,
      hint: '派生属性以该本体为来源（计算 / 图指标）。',
      page: { name: 'ontology-functions', label: '打开「函数与派生属性」管理页' },
    },
    object_views: {
      title: '对象视图',
      count: u.object_views?.length || 0,
      items: (u.object_views || []).map(v => ({ id: v.id, name: v.name })),
      countOnly: false,
      hint: '实体详情页视图里引用了该本体。',
      page: { name: 'ontology-ontologies', query: { category: cat, tab: 'view' }, label: '打开「对象视图」管理页' },
    },
    kb_bindings: {
      title: 'KB 绑定',
      count: u.kb_bindings?.length || (typeof u.kb_bindings === 'number' ? u.kb_bindings : 0),
      items: Array.isArray(u.kb_bindings) ? u.kb_bindings.map(k => ({ id: k.id || k.kb_id || k.name, name: k.name || k.kb_name || String(k) })) : [],
      countOnly: !Array.isArray(u.kb_bindings) || (u.kb_bindings || []).length === 0,
      hint: '把它作为抽取约束的知识库；删除本体将影响这些知识库的抽取。',
      page: { name: 'kb', label: '打开「知识库」管理页' },
    },
  }
})

function openChip(key) {
  if (!usages.value) return
  viewing.value = { key, ...chipMeta.value[key] }
}

function closeView() {
  viewing.value = null
}

// 打开该类型对应的管理页（关闭弹窗后跳转）
function openPage(page) {
  if (!page) return
  closeView()
  router.push({ name: page.name, query: page.query || {} })
}

// 点击某个明细项：能直达单项编辑页的就直达（如动作 → 服务编辑器），否则打开管理页
function openItem(item) {
  const meta = viewing.value
  if (!meta) return
  if (meta.itemRoute) {
    closeView()
    router.push(meta.itemRoute(item))
    return
  }
  openPage(meta.page)
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
        <button class="ua-chip" :class="{ hot: usages.constraint_count }" data-key="constraint_count" @click="openChip('constraint_count')" title="查看三元组约束明细">三元组约束 {{ usages.constraint_count }}</button>
        <button class="ua-chip" :class="{ hot: usages.entity_count }" data-key="entity_count" @click="openChip('entity_count')" title="查看实体数量明细">实体 {{ usages.entity_count }}</button>
        <button class="ua-chip" :class="{ hot: usages.interfaces?.length }" data-key="interfaces" @click="openChip('interfaces')" title="查看接口实现明细">接口实现 {{ usages.interfaces?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.services?.length }" data-key="services" @click="openChip('services')" title="查看动作明细">动作 {{ usages.services?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.functions?.length }" data-key="functions" @click="openChip('functions')" title="查看函数明细">函数 {{ usages.functions?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.derived_properties?.length }" data-key="derived_properties" @click="openChip('derived_properties')" title="查看派生属性明细">派生属性 {{ usages.derived_properties?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.object_views?.length }" data-key="object_views" @click="openChip('object_views')" title="查看对象视图明细">对象视图 {{ usages.object_views?.length || 0 }}</button>
        <button class="ua-chip" :class="{ hot: usages.kb_bindings?.length }" data-key="kb_bindings" @click="openChip('kb_bindings')" title="查看知识库绑定明细">KB 绑定 {{ usages.kb_bindings?.length || 0 }}</button>
      </div>

      <div v-if="!usages.constraint_count && !usages.entity_count && !usages.interfaces?.length && !usages.services?.length && !usages.functions?.length && !usages.derived_properties?.length && !usages.object_views?.length && !usages.kb_bindings?.length" class="ua-none">
        该本体暂无其他对象依赖，可安全删除 / 改名。
      </div>
    </div>

    <!-- 明细弹窗：所有 chip 点击都弹窗，不再跳转 -->
    <ModalDialog
      :model-value="!!viewing"
      :title="viewing ? `${viewing.title}（${viewing.count}）` : ''"
      size="sm"
      @update:model-value="closeView"
    >
      <div v-if="viewing" class="ua-modal-body">
        <p class="ua-modal-hint">{{ viewing.hint }}</p>
        <div v-if="!viewing.countOnly && viewing.items.length" class="ua-modal-list">
          <button v-for="(it, i) in viewing.items" :key="it.id || i" class="ua-modal-item" @click="openItem(it)" :title="viewing.itemRoute ? '打开该项编辑页' : '打开管理页'">
            <span class="ua-modal-idx">{{ i + 1 }}</span>
            <span class="ua-modal-name">{{ it.name }}</span>
            <span class="ua-modal-go">›</span>
          </button>
        </div>
        <div v-else-if="viewing.countOnly" class="ua-modal-count">
          <div class="ua-modal-count-num">{{ viewing.count }}</div>
          <div class="ua-modal-count-label">返回数量</div>
        </div>
        <div v-else class="ua-modal-empty">暂无明细</div>
        <div class="ua-modal-foot">
          <button class="btn sm primary" @click="openPage(viewing.page)" v-if="viewing.page">
            {{ viewing.page.label }}
          </button>
          <button class="btn sm" @click="closeView">关闭</button>
        </div>
      </div>
    </ModalDialog>
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

.ua-modal-body { display: flex; flex-direction: column; gap: 10px; min-width: 320px; max-height: 60vh; }
.ua-modal-hint { margin: 0; font-size: 12px; color: var(--c-secondary); line-height: 1.5; }
.ua-modal-list { display: flex; flex-direction: column; gap: 4px; overflow-y: auto; max-height: 44vh; padding-right: 4px; }
.ua-modal-item { display: flex; align-items: center; gap: 8px; padding: 7px 10px; background: var(--c-muted); border-radius: var(--radius-sm); font-size: 13px; border: 1px solid transparent; cursor: pointer; width: 100%; text-align: left; font-family: var(--font); color: var(--c-fg); transition: background 120ms, border-color 120ms; }
.ua-modal-item:hover { background: var(--c-border); border-color: var(--c-accent); }
.ua-modal-go { flex: 0 0 auto; color: var(--c-secondary); font-size: 16px; line-height: 1; }
.ua-modal-foot { display: flex; justify-content: flex-end; gap: 8px; border-top: 1px solid var(--c-border); padding-top: 10px; }
.ua-modal-idx { flex: 0 0 22px; text-align: center; font-size: 11px; color: var(--c-secondary); font-family: ui-monospace, Consolas, monospace; }
.ua-modal-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-fg); }
.ua-modal-count { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 16px 0; }
.ua-modal-count-num { font-size: 32px; font-weight: 700; color: var(--c-danger); }
.ua-modal-count-label { font-size: 12px; color: var(--c-secondary); }
.ua-modal-empty { padding: 16px; text-align: center; color: var(--c-secondary); font-size: 13px; }
</style>
