<script setup>
import { ref, onMounted, watch } from 'vue'
import {
  fetchObjectViews, createObjectView, updateObjectView, deleteObjectView, setDefaultObjectView,
} from '../../api'

const props = defineProps({
  categoryId: { type: String, required: true },
})

const views = ref([])
const loading = ref(false)
const showEditor = ref(false)
const editingId = ref('')
const saving = ref(false)
const editorMode = ref('visual') // 'visual' | 'json'

const form = ref(emptyForm())
const layoutModel = ref({ tabs: [] })

const drag = ref({ type: '', from: null, to: null })

function emptyForm() {
  return {
    name: '',
    ontology_id: '',
    interface_code: '',
    layout_text: JSON.stringify(emptyLayout(), null, 2),
    is_default: false,
    version: null,
  }
}

function emptyLayout() {
  return {
    tabs: [
      {
        name: '概览',
        sections: [
          { title: '基本信息', widgets: [{ kind: 'properties', title: '属性', config: {} }] },
          { title: '关联', widgets: [{ kind: 'relations', title: '关系', config: {} }] },
        ],
      },
      {
        name: '操作',
        sections: [
          { title: '动作', widgets: [{ kind: 'actions', title: '可用动作', config: {} }] },
        ],
      },
    ],
  }
}

function parseLayout(text) {
  try {
    const parsed = JSON.parse(text || '{}')
    if (!parsed || !Array.isArray(parsed.tabs)) return { tabs: [] }
    return parsed
  } catch {
    return { tabs: [] }
  }
}

function syncLayoutText() {
  form.value.layout_text = JSON.stringify(layoutModel.value, null, 2)
}

function switchMode(mode) {
  editorMode.value = mode
  if (mode === 'visual') {
    layoutModel.value = parseLayout(form.value.layout_text)
  }
}

const WIDGET_KINDS = [
  { value: 'properties', label: 'properties 属性' },
  { value: 'relations', label: 'relations 关系' },
  { value: 'actions', label: 'actions 动作' },
  { value: 'derived', label: 'derived 派生属性' },
  { value: 'timeline', label: 'timeline 时间线' },
  { value: 'chart', label: 'chart 图表' },
]

function activeTabIndex() {
  return layoutModel.value.tabs.findIndex((t) => t._active) || 0
}

function ensureActiveTab() {
  const tabs = layoutModel.value.tabs
  if (!tabs.length) return
  const hasActive = tabs.some((t) => t._active)
  if (!hasActive) tabs[0]._active = true
}

function setActiveTab(idx) {
  layoutModel.value.tabs.forEach((t, i) => (t._active = i === idx))
}

function addTab() {
  const tabs = layoutModel.value.tabs
  const name = `标签页 ${tabs.length + 1}`
  tabs.forEach((t) => (t._active = false))
  tabs.push({ name, _active: true, sections: [] })
  syncLayoutText()
}

function removeTab(idx) {
  layoutModel.value.tabs.splice(idx, 1)
  ensureActiveTab()
  syncLayoutText()
}

function addSection() {
  const tab = layoutModel.value.tabs.find((t) => t._active)
  if (!tab) return
  tab.sections.push({ title: `分区 ${tab.sections.length + 1}`, widgets: [] })
  syncLayoutText()
}

function removeSection(tabIdx, secIdx) {
  layoutModel.value.tabs[tabIdx].sections.splice(secIdx, 1)
  syncLayoutText()
}

function addWidget(tabIdx, secIdx) {
  const section = layoutModel.value.tabs[tabIdx].sections[secIdx]
  section.widgets.push({ kind: 'properties', title: '属性', config: {} })
  syncLayoutText()
}

function removeWidget(tabIdx, secIdx, widIdx) {
  layoutModel.value.tabs[tabIdx].sections[secIdx].widgets.splice(widIdx, 1)
  syncLayoutText()
}

function onWidgetKindChange(widget, kind) {
  widget.kind = kind
  const found = WIDGET_KINDS.find((k) => k.value === kind)
  widget.title = found ? found.label.split(' ')[1] || found.label : widget.title
  syncLayoutText()
}

function onDragStart(type, path, event) {
  drag.value = { type, from: path, to: path }
  if (event?.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', JSON.stringify({ type, path }))
  }
}

function onDragOver(type, path) {
  if (drag.value.type !== type) return
  drag.value.to = path
}

function onDrop(type) {
  if (drag.value.type !== type) return
  const { from, to } = drag.value
  if (!from || !to || JSON.stringify(from) === JSON.stringify(to)) {
    drag.value = { type: '', from: null, to: null }
    return
  }
  if (type === 'tab') moveTab(from[0], to[0])
  else if (type === 'section') moveSection(from[0], from[1], to[1])
  else if (type === 'widget') moveWidget(from[0], from[1], from[2], to[2])
  drag.value = { type: '', from: null, to: null }
}

function isDragTarget(type, path) {
  return drag.value.type === type && JSON.stringify(drag.value.to) === JSON.stringify(path)
}

function moveItem(list, from, to) {
  if (from === to) return
  const [item] = list.splice(from, 1)
  list.splice(to, 0, item)
}

function moveTab(from, to) {
  moveItem(layoutModel.value.tabs, from, to)
  setActiveTab(to)
  syncLayoutText()
}

function moveSection(tabIdx, from, to) {
  moveItem(layoutModel.value.tabs[tabIdx].sections, from, to)
  syncLayoutText()
}

function moveWidget(tabIdx, secIdx, from, to) {
  moveItem(layoutModel.value.tabs[tabIdx].sections[secIdx].widgets, from, to)
  syncLayoutText()
}

function moveTabUp(idx) { if (idx > 0) moveTab(idx, idx - 1) }
function moveTabDown(idx) { if (idx < layoutModel.value.tabs.length - 1) moveTab(idx, idx + 1) }
function moveSectionUp(tabIdx, secIdx) { if (secIdx > 0) moveSection(tabIdx, secIdx, secIdx - 1) }
function moveSectionDown(tabIdx, secIdx) { if (secIdx < layoutModel.value.tabs[tabIdx].sections.length - 1) moveSection(tabIdx, secIdx, secIdx + 1) }
function moveWidgetUp(tabIdx, secIdx, widIdx) { if (widIdx > 0) moveWidget(tabIdx, secIdx, widIdx, widIdx - 1) }
function moveWidgetDown(tabIdx, secIdx, widIdx) { if (widIdx < layoutModel.value.tabs[tabIdx].sections[secIdx].widgets.length - 1) moveWidget(tabIdx, secIdx, widIdx, widIdx + 1) }

async function load() {
  if (!props.categoryId) return
  loading.value = true
  try {
    views.value = await fetchObjectViews(props.categoryId)
  } catch {
    views.value = []
  } finally {
    loading.value = false
  }
}

function openNew() {
  form.value = emptyForm()
  layoutModel.value = parseLayout(form.value.layout_text)
  ensureActiveTab()
  editorMode.value = 'visual'
  editingId.value = ''
  showEditor.value = true
}

function openEdit(v) {
  form.value = {
    name: v.name,
    ontology_id: v.ontology_id || '',
    interface_code: v.interface_code || '',
    layout_text: JSON.stringify(v.layout || {}, null, 2),
    is_default: !!v.is_default,
    version: v.version,
  }
  layoutModel.value = parseLayout(form.value.layout_text)
  ensureActiveTab()
  editorMode.value = 'visual'
  editingId.value = v.id
  showEditor.value = true
}

function stripInternal(layout) {
  const clone = JSON.parse(JSON.stringify(layout))
  if (Array.isArray(clone.tabs)) {
    clone.tabs.forEach((tab) => {
      delete tab._active
      if (Array.isArray(tab.sections)) {
        tab.sections.forEach((section) => {
          delete section._active
          if (Array.isArray(section.widgets)) {
            section.widgets.forEach((widget) => delete widget._active)
          }
        })
      }
    })
  }
  return clone
}

async function save() {
  let layout
  try {
    layout = JSON.parse(form.value.layout_text)
  } catch {
    alert('布局 JSON 格式不正确')
    return
  }
  layout = stripInternal(layout)
  if (!form.value.name.trim()) { alert('请填写视图名称'); return }
  saving.value = true
  const payload = {
    name: form.value.name.trim(),
    ontology_id: form.value.ontology_id || '',
    interface_code: form.value.interface_code || '',
    layout,
    is_default: !!form.value.is_default,
    set_default: !!form.value.is_default,
  }
  try {
    if (editingId.value) {
      payload.version = form.value.version
      await updateObjectView(editingId.value, payload)
    } else {
      await createObjectView(props.categoryId, payload)
    }
    showEditor.value = false
    await load()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

async function remove(v) {
  if (!confirm(`确认删除对象视图「${v.name}」？`)) return
  try {
    await deleteObjectView(v.id)
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

async function setDefault(v) {
  try {
    await setDefaultObjectView(v.id)
    await load()
  } catch (e) {
    alert('设置失败：' + e.message)
  }
}

watch(() => props.categoryId, load)
onMounted(load)
</script>

<template>
  <div class="ovm-root">
    <div class="ovm-head">
      <span class="ovm-tip">对象视图：可配置实体详情页布局（tabs → sections → widgets）。无视图时回落到默认详情页。</span>
      <button class="btn primary sm" @click="openNew" :disabled="!categoryId">+ 新建对象视图</button>
    </div>

    <div v-if="loading" class="ovm-hint">加载中...</div>
    <div v-else-if="!views.length" class="ovm-empty">暂无对象视图。新建后可绑定到本体或作为类别缺省视图。</div>

    <div v-else class="ovm-list">
      <div v-for="v in views" :key="v.id" class="ovm-card">
        <div class="ovm-card-main">
          <div class="ovm-title">{{ v.name }}</div>
          <div class="ovm-badges">
            <span class="ovm-badge" v-if="v.is_default">默认</span>
            <span class="ovm-badge" v-if="v.ontology_id">本体级</span>
            <span class="ovm-badge" v-else-if="v.interface_code">接口级</span>
            <span class="ovm-badge" v-else>类别缺省</span>
            <span class="ovm-badge mono">v{{ v.version }}</span>
            <span class="ovm-badge" v-if="v.ontology_id || v.interface_code">{{ v.ontology_id || v.interface_code }}</span>
          </div>
        </div>
        <div class="ovm-actions">
          <button v-if="!v.is_default" class="btn sm" @click="setDefault(v)" title="设为默认">设为默认</button>
          <button class="btn sm" @click="openEdit(v)">编辑</button>
          <button class="btn sm danger" @click="remove(v)">删除</button>
        </div>
      </div>
    </div>

    <div v-if="showEditor" class="ovm-mask" @click.self="showEditor = false">
      <div class="ovm-modal">
        <div class="ovm-modal-head">
          <h3>{{ editingId ? '编辑' : '新建' }}对象视图</h3>
          <button class="ovm-close" @click="showEditor = false">×</button>
        </div>
        <div class="ovm-modal-body">
          <div class="ovm-grid2">
            <div class="ovm-field">
              <label>视图名称</label>
              <input type="text" v-model="form.name" placeholder="如：人物标准视图">
            </div>
            <div class="ovm-field">
              <label>说明</label>
              <span class="ovm-hint">绑定本体 / 接口可留空表示类别缺省</span>
            </div>
          </div>
          <div class="ovm-grid2">
            <div class="ovm-field">
              <label>绑定本体 ID（可选）</label>
              <input type="text" v-model="form.ontology_id" placeholder="留空=类别缺省">
            </div>
            <div class="ovm-field">
              <label>或绑定接口 code（可选）</label>
              <input type="text" v-model="form.interface_code" placeholder="留空=类别缺省">
            </div>
          </div>

          <div class="ovm-field">
            <div class="ovm-mode-bar">
              <label>布局</label>
              <div class="ovm-mode-switch">
                <button :class="['ovm-mode-btn', { active: editorMode === 'visual' }]" @click="switchMode('visual')">可视化</button>
                <button :class="['ovm-mode-btn', { active: editorMode === 'json' }]" @click="switchMode('json')">JSON</button>
              </div>
            </div>

            <!-- JSON 模式 -->
            <textarea v-if="editorMode === 'json'" v-model="form.layout_text" rows="16" spellcheck="false" class="ovm-json"></textarea>

            <!-- 可视化模式 -->
            <div v-else class="ovm-builder">
              <div class="ovm-tabs-bar">
                <div
                  v-for="(tab, tIdx) in layoutModel.tabs"
                  :key="`tab-${tIdx}`"
                  :class="['ovm-tab-item', { active: tab._active, 'drag-target': isDragTarget('tab', [tIdx]) }]"
                  draggable="true"
                  @dragstart="onDragStart('tab', [tIdx], $event)"
                  @dragover.prevent="onDragOver('tab', [tIdx])"
                  @drop.prevent="onDrop('tab')"
                >
                  <span class="ovm-drag-handle" title="拖拽排序">⋮⋮</span>
                  <input v-model="tab.name" @input="syncLayoutText" class="ovm-tab-name" placeholder="标签页名称">
                  <button class="ovm-icon-btn ovm-sort-btn" :disabled="tIdx === 0" @click="moveTabUp(tIdx)" title="左移">‹</button>
                  <button class="ovm-icon-btn ovm-sort-btn" :disabled="tIdx === layoutModel.tabs.length - 1" @click="moveTabDown(tIdx)" title="右移">›</button>
                  <button class="ovm-icon-btn" @click="removeTab(tIdx)" title="删除标签页">×</button>
                </div>
                <button class="ovm-add-tab" @click="addTab">+ 标签页</button>
              </div>

              <div v-if="layoutModel.tabs.length" class="ovm-tab-content">
                <div v-for="(tab, tIdx) in layoutModel.tabs" v-show="tab._active" :key="`content-${tIdx}`" class="ovm-tab-pane">
                  <div
                    v-for="(section, sIdx) in tab.sections"
                    :key="`sec-${tIdx}-${sIdx}`"
                    :class="['ovm-section', { 'drag-target': isDragTarget('section', [tIdx, sIdx]) }]"
                    draggable="true"
                    @dragstart="onDragStart('section', [tIdx, sIdx], $event)"
                    @dragover.prevent="onDragOver('section', [tIdx, sIdx])"
                    @drop.prevent="onDrop('section')"
                  >
                    <div class="ovm-section-head">
                      <span class="ovm-drag-handle" title="拖拽排序">⋮⋮</span>
                      <input v-model="section.title" @input="syncLayoutText" class="ovm-section-title" placeholder="分区标题">
                      <button class="ovm-icon-btn ovm-sort-btn" :disabled="sIdx === 0" @click="moveSectionUp(tIdx, sIdx)" title="上移">↑</button>
                      <button class="ovm-icon-btn ovm-sort-btn" :disabled="sIdx === tab.sections.length - 1" @click="moveSectionDown(tIdx, sIdx)" title="下移">↓</button>
                      <button class="ovm-icon-btn" @click="removeSection(tIdx, sIdx)" title="删除分区">×</button>
                    </div>
                    <div class="ovm-widgets">
                      <div
                        v-for="(widget, wIdx) in section.widgets"
                        :key="`wid-${tIdx}-${sIdx}-${wIdx}`"
                        :class="['ovm-widget-row', { 'drag-target': isDragTarget('widget', [tIdx, sIdx, wIdx]) }]"
                        draggable="true"
                        @dragstart="onDragStart('widget', [tIdx, sIdx, wIdx], $event)"
                        @dragover.prevent="onDragOver('widget', [tIdx, sIdx, wIdx])"
                        @drop.prevent="onDrop('widget')"
                      >
                        <span class="ovm-drag-handle" title="拖拽排序">⋮⋮</span>
                        <select :value="widget.kind" @change="onWidgetKindChange(widget, $event.target.value)">
                          <option v-for="k in WIDGET_KINDS" :key="k.value" :value="k.value">{{ k.label }}</option>
                        </select>
                        <input v-model="widget.title" @input="syncLayoutText" class="ovm-widget-title" placeholder="微件标题">
                        <button class="ovm-icon-btn ovm-sort-btn" :disabled="wIdx === 0" @click="moveWidgetUp(tIdx, sIdx, wIdx)" title="上移">↑</button>
                        <button class="ovm-icon-btn ovm-sort-btn" :disabled="wIdx === section.widgets.length - 1" @click="moveWidgetDown(tIdx, sIdx, wIdx)" title="下移">↓</button>
                        <button class="ovm-icon-btn" @click="removeWidget(tIdx, sIdx, wIdx)" title="删除微件">×</button>
                      </div>
                      <button class="ovm-add-widget" @click="addWidget(tIdx, sIdx)">+ 添加微件</button>
                    </div>
                  </div>
                  <button class="ovm-add-section" @click="addSection">+ 添加分区</button>
                </div>
              </div>
              <div v-else class="ovm-empty-builder">
                暂无标签页，点击「+ 标签页」开始配置。
              </div>
            </div>

            <div class="ovm-hint">
              微件 kind：<code>properties</code> 属性 · <code>relations</code> 关系 · <code>actions</code> 动作 · <code>derived</code> 派生属性 · <code>timeline</code> 时间线 · <code>chart</code> 图表
            </div>
          </div>
          <label class="ovm-check"><input type="checkbox" v-model="form.is_default"> 设为该本体/类别的默认视图</label>
        </div>
        <div class="ovm-modal-foot">
          <button class="btn sm" @click="showEditor = false">取消</button>
          <button class="btn sm primary" :disabled="saving" @click="save">{{ saving ? '保存中...' : '保存' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ovm-root { display: flex; flex-direction: column; gap: 12px; }
.ovm-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.ovm-tip { font-size: 12px; color: var(--c-secondary); }
.btn.sm { padding: 5px 11px; font-size: 12px; }
.btn.danger { border-color: rgba(220,38,38,0.4); color: var(--c-danger); }
.btn.danger:hover { background: rgba(220,38,38,0.08); }

.ovm-hint { font-size: 12px; color: var(--c-secondary); }
.ovm-hint code { font-family: ui-monospace, Consolas, monospace; background: var(--c-muted); padding: 0 4px; border-radius: 4px; }
.ovm-empty { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }

.ovm-list { display: flex; flex-direction: column; gap: 6px; }
.ovm-card { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); }
.ovm-card-main { flex: 1; min-width: 0; }
.ovm-title { font-size: 14px; font-weight: 600; color: var(--c-fg); }
.ovm-badges { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
.ovm-badge { font-size: 11px; padding: 1px 7px; border-radius: 9px; background: var(--c-muted); color: var(--c-secondary); }
.ovm-badge.mono { font-family: ui-monospace, Consolas, monospace; }
.ovm-actions { display: flex; gap: 6px; flex-shrink: 0; }

.ovm-mask { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 300; }
.ovm-modal { width: 720px; max-width: 94vw; max-height: 90vh; overflow-y: auto; background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius); display: flex; flex-direction: column; }
.ovm-modal-head { display: flex; align-items: center; justify-content: space-between; padding: 13px 16px; border-bottom: 1px solid var(--c-border); }
.ovm-modal-head h3 { margin: 0; font-size: 14px; font-weight: 700; color: var(--c-fg); }
.ovm-close { width: 26px; height: 26px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); font-size: 16px; cursor: pointer; }
.ovm-close:hover { background: var(--c-muted); color: var(--c-fg); }
.ovm-modal-body { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.ovm-field { display: flex; flex-direction: column; gap: 4px; }
.ovm-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.ovm-field input { width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; box-sizing: border-box; }
.ovm-field input:focus { border-color: var(--c-fg); }
.ovm-json { width: 100%; padding: 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12px; font-family: ui-monospace, Consolas, monospace; outline: none; resize: vertical; }
.ovm-json:focus { border-color: var(--c-fg); }
.ovm-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.ovm-check { display: flex; align-items: center; gap: 5px; font-size: 13px; color: var(--c-fg); }
.ovm-modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--c-border); }

.ovm-mode-bar { display: flex; align-items: center; justify-content: space-between; }
.ovm-mode-switch { display: flex; gap: 0; border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; }
.ovm-mode-btn { padding: 4px 12px; font-size: 12px; border: 0; background: var(--c-panel); color: var(--c-secondary); cursor: pointer; }
.ovm-mode-btn.active { background: var(--c-muted); color: var(--c-fg); font-weight: 600; }
.ovm-mode-btn:not(.active):hover { background: var(--c-muted); color: var(--c-fg); }

.ovm-builder { border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); display: flex; flex-direction: column; min-height: 220px; }
.ovm-tabs-bar { display: flex; gap: 4px; padding: 8px; border-bottom: 1px solid var(--c-border); overflow-x: auto; }
.ovm-tab-item { display: flex; align-items: center; gap: 4px; padding: 5px 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-muted); cursor: grab; }
.ovm-tab-item.active { border-color: var(--c-fg); background: var(--c-panel); }
.ovm-tab-name { width: 90px; border: 0; background: transparent; padding: 2px 4px; font-size: 13px; color: var(--c-fg); }
.ovm-tab-name:focus { outline: 1px solid var(--c-fg); border-radius: 3px; }
.ovm-add-tab { flex-shrink: 0; padding: 5px 10px; font-size: 12px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; }
.ovm-add-tab:hover { border-color: var(--c-fg); color: var(--c-fg); }

.ovm-tab-content { padding: 12px; }
.ovm-tab-pane { display: flex; flex-direction: column; gap: 10px; }
.ovm-section { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 10px; background: var(--c-panel); cursor: grab; }
.ovm-section-head { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.ovm-section-title { flex: 1; border: 0; background: transparent; padding: 3px 6px; font-size: 13px; font-weight: 600; color: var(--c-fg); }
.ovm-section-title:focus { outline: 1px solid var(--c-fg); border-radius: 3px; }

.ovm-widgets { display: flex; flex-direction: column; gap: 6px; }
.ovm-widget-row { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); cursor: grab; }
.ovm-widget-row select { padding: 4px 6px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12px; }
.ovm-widget-title { flex: 1; border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 4px 6px; background: var(--c-panel); color: var(--c-fg); font-size: 12px; }

.ovm-add-widget, .ovm-add-section { padding: 5px 10px; font-size: 12px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; }
.ovm-add-widget { align-self: flex-start; margin-top: 2px; }
.ovm-add-section { margin-top: 4px; }
.ovm-add-widget:hover, .ovm-add-section:hover { border-color: var(--c-fg); color: var(--c-fg); }

.ovm-empty-builder { padding: 32px; text-align: center; color: var(--c-secondary); font-size: 13px; }

.ovm-drag-handle { color: var(--c-secondary); font-size: 11px; cursor: grab; user-select: none; }
.ovm-icon-btn { width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; font-size: 14px; line-height: 1; }
.ovm-icon-btn:hover { background: rgba(220,38,38,0.1); color: var(--c-danger); }

.ovm-sort-btn { font-size: 12px !important; }
.ovm-sort-btn:hover { background: var(--c-muted) !important; color: var(--c-fg) !important; }
.ovm-sort-btn:disabled { opacity: 0.3; cursor: default; }
.ovm-sort-btn:disabled:hover { background: transparent !important; color: var(--c-secondary) !important; }

.drag-target { border-color: var(--c-accent) !important; box-shadow: 0 0 0 2px rgba(59,130,246,0.25); }
.ovm-tab-item:active, .ovm-section:active, .ovm-widget-row:active { cursor: grabbing; }
</style>
