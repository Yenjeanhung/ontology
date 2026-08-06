<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  // 初始属性列表 [{ id?, name, data_type, description, is_required, default_value, enum_values, sort_order }]
  attributes: {
    type: Array,
    default: () => [],
  },
  // 保存函数：async ({ attributes }) => result；不传则不持久化（由父组件处理）
  saveFn: {
    type: Function,
    default: null,
  },
  editable: {
    type: Boolean,
    default: true,
  },
  // 标题文案
  title: {
    type: String,
    default: '属性',
  },
})

const emit = defineEmits(['saved', 'change'])

const DATA_TYPES = [
  { value: 'string', label: '文本 (string)' },
  { value: 'text', label: '长文本 (text)' },
  { value: 'number', label: '数字 (number)' },
  { value: 'boolean', label: '布尔 (boolean)' },
  { value: 'date', label: '日期 (date)' },
  { value: 'datetime', label: '日期时间 (datetime)' },
]

// 工作副本
const list = ref([])
const expandedId = ref(null)
const saving = ref(false)
const saveError = ref('')

function syncFromProps() {
  list.value = (props.attributes || []).map(a => ({
    id: a.id || null,
    name: a.name || '',
    code: a.code || '',
    data_type: a.data_type || 'string',
    description: a.description || '',
    is_required: !!a.is_required,
    default_value: a.default_value || '',
    sort_order: a.sort_order || 0,
    _dirty: false,
    _isNew: false,
  }))
  expandedId.value = null
}

watch(() => props.attributes, syncFromProps, { immediate: true, deep: false })

const dirty = computed(() => list.value.some(a => a._dirty || a._isNew))

function markDirty(idx) {
  list.value[idx]._dirty = true
  emit('change')
}

function toggleExpand(idx) {
  const item = list.value[idx]
  const key = item.id || `new-${idx}`
  expandedId.value = expandedId.value === key ? null : key
}

function isExpanded(idx) {
  const item = list.value[idx]
  const key = item.id || `new-${idx}`
  return expandedId.value === key
}

function addAttribute() {
  const newAttr = {
    id: null,
    name: '',
    code: '',
    data_type: 'string',
    description: '',
    is_required: false,
    default_value: '',
    sort_order: list.value.length,
    _dirty: true,
    _isNew: true,
  }
  list.value.push(newAttr)
  expandedId.value = `new-${list.value.length - 1}`
  emit('change')
}

function removeAttribute(idx) {
  if (!confirm(`确认删除属性「${list.value[idx].name || '未命名'}」？`)) return
  list.value.splice(idx, 1)
  // 重新排序
  list.value.forEach((a, i) => { a.sort_order = i; a._dirty = true })
  emit('change')
}

function moveAttr(idx, dir) {
  const target = idx + dir
  if (target < 0 || target >= list.value.length) return
  const tmp = list.value[idx]
  list.value[idx] = list.value[target]
  list.value[target] = tmp
  list.value.forEach((a, i) => { a.sort_order = i; a._dirty = true })
  emit('change')
}

async function saveAll() {
  if (!props.saveFn) {
    emit('saved', [...list.value])
    return
  }
  // 校验
  for (const a of list.value) {
    if (!a.name.trim()) {
      saveError.value = '存在未填写名称的属性'
      return
    }
  }
  // 校验编码唯一性
  const codes = list.value.map(a => a.code?.trim()).filter(Boolean)
  const dupes = codes.filter((c, i) => codes.indexOf(c) !== i)
  if (dupes.length) {
    saveError.value = `编码重复：${[...new Set(dupes)].join('、')}`
    return
  }
  saveError.value = ''
  saving.value = true
  try {
    const payload = {
      attributes: list.value.map((a, i) => ({
        name: a.name.trim(),
        code: a.code?.trim() || null,
        data_type: a.data_type,
        description: a.description.trim(),
        is_required: a.is_required,
        default_value: a.default_value || null,
        sort_order: i,
      })),
    }
    const result = await props.saveFn(payload)
    emit('saved', result)
    // 重置 dirty 标记
    list.value.forEach(a => { a._dirty = false; a._isNew = false })
  } catch (e) {
    saveError.value = '保存失败：' + e.message
  } finally {
    saving.value = false
  }
}

function typeLabel(t) {
  const found = DATA_TYPES.find(d => d.value === t)
  return found ? found.label.split(' ')[0] : t
}
</script>

<template>
  <div class="ae-root">
    <div class="ae-head" v-if="editable">
      <button class="btn sm" @click="addAttribute" :disabled="!editable">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        添加属性
      </button>
      <button
        v-if="saveFn"
        class="btn primary sm"
        @click="saveAll"
        :disabled="!dirty || saving"
      >
        <span v-if="saving" class="spinner"></span>
        {{ saving ? '保存中' : (dirty ? '保存全部' : '已保存') }}
      </button>
    </div>
    <div v-if="saveError" class="ae-error">{{ saveError }}</div>

    <div v-if="list.length === 0" class="ae-empty">
      暂无{{ title }}，点击「添加属性」开始定义
    </div>

    <div class="ae-list">
      <div
        v-for="(attr, idx) in list"
        :key="(attr.id || 'new') + '-' + idx"
        class="ae-card"
        :class="{ expanded: isExpanded(idx), 'is-new': attr._isNew }"
      >
        <div class="ae-card-head" @click="editable && toggleExpand(idx)">
          <span class="ae-drag" v-if="editable">
            <button class="drag-btn" @click.stop="moveAttr(idx, -1)" title="上移">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
            </button>
            <button class="drag-btn" @click.stop="moveAttr(idx, 1)" title="下移">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
            </button>
          </span>
          <span v-if="attr.code" class="ae-code-tag">{{ attr.code }}</span>
          <span class="ae-name" :class="{ placeholder: !attr.name }">{{ attr.name || '未命名属性' }}</span>
          <span class="ae-type-tag">{{ typeLabel(attr.data_type) }}</span>
          <span v-if="attr.is_required" class="ae-req-tag">必填</span>
          <span v-if="attr._dirty || attr._isNew" class="ae-dirty-dot" title="未保存"></span>
          <span class="ae-spacer"></span>
          <span v-if="editable" class="ae-actions">
            <button class="rm-btn" @click.stop="removeAttribute(idx)" title="删除">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </span>
          <svg class="ae-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </div>

        <div v-if="isExpanded(idx)" class="ae-card-body">
          <div class="ae-field-row">
            <div class="ae-field">
              <label>属性编码</label>
              <input type="text" v-model="attr.code" @input="markDirty(idx)" placeholder="如：found_date（本体内唯一）">
            </div>
            <div class="ae-field">
              <label>属性名称</label>
              <input type="text" v-model="attr.name" @input="markDirty(idx)" placeholder="如：成立时间">
            </div>
          </div>
          <div class="ae-field-row">
            <div class="ae-field">
              <label>数据类型</label>
              <select v-model="attr.data_type" @change="markDirty(idx)">
                <option v-for="d in DATA_TYPES" :key="d.value" :value="d.value">{{ d.label }}</option>
              </select>
            </div>
            <div class="ae-field">
              <label>默认值</label>
              <input type="text" v-model="attr.default_value" @input="markDirty(idx)" placeholder="（可选）">
            </div>
          </div>
          <div class="ae-field">
            <label>描述</label>
            <input type="text" v-model="attr.description" @input="markDirty(idx)" placeholder="该属性的含义说明">
          </div>
          <div class="ae-field-row">
            <div class="ae-field-check">
              <label>是否必填</label>
              <label class="switch">
                <input type="checkbox" v-model="attr.is_required" @change="markDirty(idx)">
                <span class="switch-slider"></span>
                <span class="switch-label">{{ attr.is_required ? '必填' : '可选' }}</span>
              </label>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ae-root { display: flex; flex-direction: column; gap: 10px; }
.ae-head { display: flex; align-items: center; gap: 8px; }
.btn.sm { padding: 5px 11px; font-size: 12px; }
.ae-error { color: var(--c-danger); font-size: 12px; }
.ae-empty { padding: 20px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }

.ae-list { display: flex; flex-direction: column; gap: 6px; }
.ae-card {
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); overflow: hidden;
  transition: border-color 150ms;
}
.ae-card.expanded { border-color: var(--c-fg); }
.ae-card.is-new { border-style: dashed; }

.ae-card-head {
  display: flex; align-items: center; gap: 8px; padding: 9px 12px;
  cursor: pointer; user-select: none;
}
.ae-card-head:hover { background: var(--c-muted); }
.ae-drag { display: inline-flex; flex-direction: column; gap: 1px; }
.drag-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 11px; border: 0; background: transparent;
  color: var(--c-secondary); cursor: pointer; padding: 0;
}
.drag-btn:hover { color: var(--c-fg); }
.ae-name { font-size: 13px; font-weight: 600; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 240px; }
.ae-name.placeholder { color: var(--c-secondary); font-weight: 500; font-style: italic; }
.ae-type-tag, .ae-req-tag, .ae-code-tag {
  font-size: 11px; padding: 1px 7px; border-radius: 10px;
  background: var(--c-muted); color: var(--c-secondary); flex-shrink: 0;
}
.ae-code-tag {
  font-family: ui-monospace, Consolas, monospace;
  background: rgba(14, 116, 144, 0.12);
  color: var(--c-accent);
}
.ae-req-tag { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.ae-dirty-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--c-accent); flex-shrink: 0; }
.ae-spacer { flex: 1; }
.ae-actions { display: inline-flex; gap: 4px; }
.rm-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border: 0; border-radius: var(--radius-sm);
  background: transparent; color: var(--c-secondary); cursor: pointer;
}
.rm-btn:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.ae-caret { color: var(--c-secondary); transition: transform 180ms ease; flex-shrink: 0; }
.ae-card.expanded .ae-caret { transform: rotate(180deg); }

.ae-card-body { padding: 12px 14px 14px; border-top: 1px solid var(--c-border); display: flex; flex-direction: column; gap: 10px; }
.ae-field-row { display: flex; gap: 12px; }
.ae-field { flex: 1; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.ae-field-check { flex: 0 0 160px; }
.ae-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.ae-field input, .ae-field select {
  width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 13px; font-family: var(--font); outline: none;
}
.ae-field input:focus, .ae-field select:focus { border-color: var(--c-fg); }
.ae-field input::placeholder { color: var(--c-secondary); opacity: 0.6; }

.switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; padding-top: 4px; }
.switch input { display: none; }
.switch-slider {
  width: 34px; height: 18px; border-radius: 10px; background: var(--c-border);
  position: relative; transition: background 180ms;
}
.switch-slider::after {
  content: ''; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px;
  border-radius: 50%; background: #fff; transition: transform 180ms;
  box-shadow: 0 1px 2px rgba(0,0,0,0.2);
}
.switch input:checked + .switch-slider { background: var(--c-success); }
.switch input:checked + .switch-slider::after { transform: translateX(16px); }
.switch-label { font-size: 12px; color: var(--c-secondary); }
</style>
