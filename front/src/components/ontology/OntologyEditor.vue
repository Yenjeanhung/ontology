<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import {
  createOntology, updateOntology, deleteOntology,
  replaceOntologyAttributes,
  fetchAttributeTemplates,
  setOntologyTemplates,
  getMergedAttributes,
} from '../../api'
import AttributeEditor from '../common/AttributeEditor.vue'
import SearchableSelect from '../common/SearchableSelect.vue'

const props = defineProps({
  categoryId: { type: String, required: true },
  detail: { type: Object, required: true },
})
const emit = defineEmits(['changed'])

const COLOR_PRESETS = ['#A16207', '#2563EB', '#16A34A', '#DC2626', '#9333EA', '#0891B2', '#DB2777', '#475569']

const templates = ref([])
const expandedId = ref(null)
const savingOnt = ref('')   // 正在保存基础信息的 ontology id
const savingTpl = ref('')   // 正在保存模板绑定的 ontology id

// 新建本体
const showCreate = ref(false)
const newName = ref('')
const newDesc = ref('')
const newColor = ref(COLOR_PRESETS[0])
const creating = ref(false)

// 编辑基础信息
const editingOntId = ref('')
const editName = ref('')
const editDesc = ref('')
const editColor = ref('')

// 模板绑定工作副本：{ ontologyId: [templateId...] }
const tplBindings = ref({})
const tplBindingsDirty = ref({})

// 合并属性预览
const mergedPreview = ref({}) // { ontologyId: { attributes, conflicts, loading } }

const ontologies = computed(() => props.detail?.ontologies || [])

const templateOptions = computed(() =>
  templates.value.map(t => ({ value: t.id, label: t.name, meta: `${t.attribute_count} 个属性` }))
)

onMounted(async () => {
  try {
    templates.value = await fetchAttributeTemplates()
  } catch {}
  // 初始化每个本体的模板绑定
  for (const ont of ontologies.value) {
    tplBindings.value[ont.id] = [...(ont.template_ids || [])]
  }
})

// 当 detail 刷新（父组件 changed）后，同步模板绑定
watch(() => props.detail, () => {
  for (const ont of ontologies.value) {
    if (!tplBindings.value[ont.id] || !tplBindingsDirty.value[ont.id]) {
      tplBindings.value[ont.id] = [...(ont.template_ids || [])]
    }
  }
}, { deep: false })

function isExpanded(ont) {
  return expandedId.value === ont.id
}

function toggleExpand(ont) {
  expandedId.value = isExpanded(ont) ? null : ont.id
}

function openCreate() {
  newName.value = ''
  newDesc.value = ''
  newColor.value = COLOR_PRESETS[0]
  showCreate.value = true
}

async function submitCreate() {
  if (!newName.value.trim()) return
  creating.value = true
  try {
    await createOntology(props.categoryId, {
      name: newName.value.trim(),
      description: newDesc.value.trim(),
      color: newColor.value,
    })
    showCreate.value = false
    emit('changed')
  } catch (e) {
    alert('创建失败：' + e.message)
  } finally {
    creating.value = false
  }
}

function startEditInfo(ont) {
  editingOntId.value = ont.id
  editName.value = ont.name
  editDesc.value = ont.description || ''
  editColor.value = ont.color || COLOR_PRESETS[0]
}

async function saveInfo(ont) {
  if (!editName.value.trim()) return
  savingOnt.value = ont.id
  try {
    await updateOntology(props.categoryId, ont.id, {
      name: editName.value.trim(),
      description: editDesc.value.trim(),
      color: editColor.value,
    })
    editingOntId.value = ''
    emit('changed')
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    savingOnt.value = ''
  }
}

async function removeOntology(ont) {
  if (!confirm(`确认删除本体「${ont.name}」？\n其下属性、三元组引用、模板绑定将一并删除。`)) return
  try {
    await deleteOntology(props.categoryId, ont.id)
    if (expandedId.value === ont.id) expandedId.value = null
    emit('changed')
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

// 模板绑定
function onTplChange(ont, ids) {
  tplBindings.value[ont.id] = ids
  tplBindingsDirty.value[ont.id] = true
}

async function saveTplBindings(ont) {
  savingTpl.value = ont.id
  try {
    await setOntologyTemplates(props.categoryId, ont.id, {
      template_ids: tplBindings.value[ont.id] || [],
    })
    tplBindingsDirty.value[ont.id] = false
    // 刷新合并预览（如果已展开）
    if (mergedPreview.value[ont.id]) {
      await loadMerged(ont)
    }
    emit('changed')
  } catch (e) {
    alert('保存模板绑定失败：' + e.message)
  } finally {
    savingTpl.value = ''
  }
}

// 属性保存
async function saveAttributes(ont, payload) {
  const result = await replaceOntologyAttributes(props.categoryId, ont.id, payload)
  // 刷新合并预览
  if (mergedPreview.value[ont.id]) {
    await loadMerged(ont)
  }
  emit('changed')
  return result
}

// 合并属性预览
async function loadMerged(ont) {
  mergedPreview.value[ont.id] = { loading: true }
  try {
    const data = await getMergedAttributes(props.categoryId, ont.id)
    mergedPreview.value[ont.id] = { ...data, loading: false }
  } catch (e) {
    mergedPreview.value[ont.id] = { loading: false, error: e.message }
  }
}

function toggleMerged(ont) {
  if (mergedPreview.value[ont.id]) {
    delete mergedPreview.value[ont.id]
  } else {
    loadMerged(ont)
  }
}

function attrSourceLabel(source) {
  if (source === 'own') return '自有'
  if (source && source.startsWith('template:')) return '模板'
  return source || ''
}
</script>

<template>
  <div class="oe-root">
    <div class="oe-head">
      <div class="oe-tip">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
        每个本体代表一类实体（如「人物」「组织」），可定义属性并引用全局属性模板。
      </div>
      <button class="btn primary sm" @click="openCreate">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        新建本体
      </button>
    </div>

    <div v-if="!ontologies.length" class="oe-empty">
      暂无本体，点击「新建本体」开始定义实体类型
    </div>

    <div class="oe-list">
      <div
        v-for="ont in ontologies"
        :key="ont.id"
        class="oe-card"
        :class="{ expanded: isExpanded(ont) }"
      >
        <div class="oe-card-head" @click="toggleExpand(ont)">
          <span class="oe-color-dot" :style="{ background: ont.color || '#A16207' }"></span>
          <span class="oe-name">{{ ont.name }}</span>
          <span class="oe-count-tag">{{ ont.attributes?.length || 0 }} 属性</span>
          <span v-if="ont.template_ids?.length" class="oe-count-tag tpl">{{ ont.template_ids.length }} 模板</span>
          <span class="oe-spacer"></span>
          <button class="rm-btn sm" @click.stop="removeOntology(ont)" title="删除">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
          <svg class="oe-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </div>

        <div v-if="isExpanded(ont)" class="oe-card-body">
          <!-- 基础信息 -->
          <div class="oe-section">
            <div class="oe-section-head">
              <span class="oe-section-title">基础信息</span>
              <button v-if="editingOntId !== ont.id" class="btn sm" @click="startEditInfo(ont)">编辑</button>
            </div>
            <template v-if="editingOntId === ont.id">
              <div class="oe-info-form">
                <div class="oe-field">
                  <label>名称</label>
                  <input type="text" v-model="editName" placeholder="本体名称">
                </div>
                <div class="oe-field">
                  <label>描述</label>
                  <input type="text" v-model="editDesc" placeholder="该本体代表的实体类型说明">
                </div>
                <div class="oe-field">
                  <label>颜色标识</label>
                  <div class="oe-color-palette">
                    <button
                      v-for="c in COLOR_PRESETS"
                      :key="c"
                      class="oe-color-swatch"
                      :class="{ active: editColor === c }"
                      :style="{ background: c }"
                      @click="editColor = c"
                    ></button>
                  </div>
                </div>
              </div>
              <div class="oe-info-actions">
                <button class="btn sm" @click="editingOntId = ''">取消</button>
                <button class="btn primary sm" @click="saveInfo(ont)" :disabled="savingOnt === ont.id || !editName.trim()">
                  <span v-if="savingOnt === ont.id" class="spinner"></span> 保存
                </button>
              </div>
            </template>
            <template v-else>
              <div class="oe-info-view">
                <span class="oe-info-desc" v-if="ont.description">{{ ont.description }}</span>
                <span class="oe-info-desc placeholder" v-else>暂无描述</span>
              </div>
            </template>
          </div>

          <!-- 属性模板引用 -->
          <div class="oe-section">
            <div class="oe-section-head">
              <span class="oe-section-title">引用属性模板</span>
              <button
                v-if="tplBindingsDirty[ont.id]"
                class="btn primary sm"
                @click="saveTplBindings(ont)"
                :disabled="savingTpl === ont.id"
              >
                <span v-if="savingTpl === ont.id" class="spinner"></span> 保存绑定
              </button>
            </div>
            <div class="oe-tpl-pick">
              <SearchableSelect
                :model-value="tplBindings[ont.id] || []"
                :options="templateOptions"
                multiple
                placeholder="选择要引用的属性模板（可多选）..."
                @change="(ids) => onTplChange(ont, ids)"
              />
            </div>
          </div>

          <!-- 本体自有属性 -->
          <div class="oe-section">
            <div class="oe-section-head">
              <span class="oe-section-title">本体属性</span>
            </div>
            <AttributeEditor
              :attributes="ont.attributes"
              :save-fn="(payload) => saveAttributes(ont, payload)"
              @saved="() => {}"
            />
          </div>

          <!-- 合并属性预览 -->
          <div class="oe-section">
            <div class="oe-section-head">
              <span class="oe-section-title">合并属性预览</span>
              <button class="btn sm" @click="toggleMerged(ont)">
                {{ mergedPreview[ont.id] ? '收起' : '查看' }}
              </button>
            </div>
            <div v-if="mergedPreview[ont.id]" class="oe-merged">
              <div v-if="mergedPreview[ont.id].loading" class="oe-merged-loading">
                <span class="spinner"></span> 加载中...
              </div>
              <div v-else-if="mergedPreview[ont.id].error" class="oe-merged-error">
                {{ mergedPreview[ont.id].error }}
              </div>
              <div v-else>
                <div v-if="mergedPreview[ont.id].conflicts?.length" class="oe-conflict-note">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                  存在 {{ mergedPreview[ont.id].conflicts.length }} 个同名冲突，已以自有属性覆盖：{{ mergedPreview[ont.id].conflicts.join('、') }}
                </div>
                <div class="oe-merged-list">
                  <div
                    v-for="a in mergedPreview[ont.id].attributes"
                    :key="a.name"
                    class="oe-merged-attr"
                    :class="{ conflict: mergedPreview[ont.id].conflicts?.includes(a.name) }"
                  >
                    <span v-if="a.code" class="oe-merged-code">{{ a.code }}</span>
                    <span class="oe-merged-name">{{ a.name }}</span>
                    <span class="oe-merged-type">{{ a.data_type }}</span>
                    <span class="oe-merged-src" :class="{ own: a.source === 'own' }">{{ attrSourceLabel(a.source) }}</span>
                  </div>
                </div>
                <div v-if="!mergedPreview[ont.id].attributes?.length" class="oe-merged-empty">
                  暂无合并属性（无自有属性且未引用模板）
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建本体弹窗 -->
    <div v-if="showCreate" class="oe-modal-mask" @click.self="showCreate = false">
      <div class="oe-modal">
        <h3>新建本体</h3>
        <div class="oe-field">
          <label>名称</label>
          <input type="text" v-model="newName" placeholder="如：人物、组织、产品" @keydown.enter="submitCreate">
        </div>
        <div class="oe-field">
          <label>描述（可选）</label>
          <input type="text" v-model="newDesc" placeholder="该本体代表的实体类型说明">
        </div>
        <div class="oe-field">
          <label>颜色标识</label>
          <div class="oe-color-palette">
            <button
              v-for="c in COLOR_PRESETS"
              :key="c"
              class="oe-color-swatch"
              :class="{ active: newColor === c }"
              :style="{ background: c }"
              @click="newColor = c"
            ></button>
          </div>
        </div>
        <div class="oe-modal-actions">
          <button class="btn" @click="showCreate = false">取消</button>
          <button class="btn primary" @click="submitCreate" :disabled="creating || !newName.trim()">
            <span v-if="creating" class="spinner"></span> 创建
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.oe-root { display: flex; flex-direction: column; gap: 12px; max-width: 880px; }
.oe-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.oe-tip { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-secondary); }
.btn.sm { padding: 5px 11px; font-size: 12px; }
.oe-empty { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }

.oe-list { display: flex; flex-direction: column; gap: 8px; }
.oe-card { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); overflow: hidden; transition: border-color 150ms; }
.oe-card.expanded { border-color: var(--c-fg); }
.oe-card-head { display: flex; align-items: center; gap: 10px; padding: 12px 16px; cursor: pointer; user-select: none; }
.oe-card-head:hover { background: var(--c-muted); }
.oe-color-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
.oe-name { font-size: 14px; font-weight: 600; color: var(--c-fg); }
.oe-count-tag { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); }
.oe-count-tag.tpl { background: rgba(161, 98, 7, 0.12); color: var(--c-accent); }
.oe-spacer { flex: 1; }
.rm-btn.sm { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; }
.rm-btn.sm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.oe-caret { color: var(--c-secondary); transition: transform 180ms ease; }
.oe-card.expanded .oe-caret { transform: rotate(180deg); }

.oe-card-body { padding: 16px 18px; border-top: 1px solid var(--c-border); display: flex; flex-direction: column; gap: 18px; }
.oe-section { display: flex; flex-direction: column; gap: 8px; }
.oe-section-head { display: flex; align-items: center; justify-content: space-between; }
.oe-section-title { font-size: 13px; font-weight: 700; color: var(--c-fg); }

.oe-info-view { font-size: 13px; }
.oe-info-desc { color: var(--c-fg); }
.oe-info-desc.placeholder { color: var(--c-secondary); font-style: italic; }

.oe-info-form { display: flex; flex-direction: column; gap: 10px; }
.oe-field { display: flex; flex-direction: column; gap: 4px; }
.oe-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.oe-field input { width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; font-family: var(--font); outline: none; }
.oe-field input:focus { border-color: var(--c-fg); }
.oe-color-palette { display: flex; gap: 8px; flex-wrap: wrap; }
.oe-color-swatch { width: 24px; height: 24px; border-radius: 50%; border: 2px solid transparent; cursor: pointer; padding: 0; transition: transform 120ms; }
.oe-color-swatch:hover { transform: scale(1.15); }
.oe-color-swatch.active { border-color: var(--c-fg); box-shadow: 0 0 0 2px var(--c-bg), 0 0 0 4px var(--c-fg); }
.oe-info-actions { display: flex; justify-content: flex-end; gap: 8px; }

.oe-tpl-pick { max-width: 520px; }

.oe-merged { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 12px; background: var(--c-muted); }
.oe-merged-loading, .oe-merged-error { padding: 12px; text-align: center; font-size: 13px; color: var(--c-secondary); }
.oe-merged-error { color: var(--c-danger); }
.oe-conflict-note { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-accent); margin-bottom: 8px; padding: 6px 10px; background: rgba(161, 98, 7, 0.08); border-radius: var(--radius-sm); }
.oe-merged-list { display: flex; flex-direction: column; gap: 4px; }
.oe-merged-attr { display: flex; align-items: center; gap: 10px; padding: 6px 10px; border-radius: var(--radius-sm); background: var(--c-panel); font-size: 12px; }
.oe-merged-attr.conflict { outline: 1px solid var(--c-accent); }
.oe-merged-name { font-weight: 600; color: var(--c-fg); flex: 1; }
.oe-merged-type { color: var(--c-secondary); font-family: ui-monospace, Consolas, monospace; font-size: 11px; }
.oe-merged-code { color: var(--c-accent); font-family: ui-monospace, Consolas, monospace; font-size: 11px; padding: 0 6px; border-radius: 8px; background: var(--c-muted); }
.oe-merged-src { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: var(--c-muted); color: var(--c-secondary); }
.oe-merged-src.own { background: rgba(161, 98, 7, 0.15); color: var(--c-accent); }
.oe-merged-empty { padding: 8px; text-align: center; color: var(--c-secondary); font-size: 12px; }

.oe-modal-mask { position: fixed; inset: 0; background: var(--c-overlay); display: flex; align-items: center; justify-content: center; z-index: 100; padding: 20px; }
.oe-modal { background: var(--c-panel); border-radius: var(--radius); padding: 22px; width: 100%; max-width: 460px; box-shadow: 0 8px 30px rgba(0,0,0,0.18); }
.oe-modal h3 { font-size: 15px; font-weight: 700; margin-bottom: 16px; color: var(--c-fg); }
.oe-modal .oe-field { margin-bottom: 12px; }
.oe-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
