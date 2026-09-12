<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  // 初始属性列表 [{ id?, name, data_type, description, is_required, default_value, enum_values, sort_order }]
  attributes: {
    type: Array,
    default: () => [],
  },
  // 固有属性（锁定展示，不可编辑/删除/排序，不参与保存）：[{ code, name, data_type, is_required, description }]
  builtins: {
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
  // 可绑定的共享属性列表（可选）：[{ id, name, code, data_type }]
  sharedProperties: {
    type: Array,
    default: () => [],
  },
  // 继承自属性模板的属性（只读展示，带「继承」标记，不参与保存）：
  // [{ code, name, data_type, source: 'template:<id>' }]
  inheritedAttributes: {
    type: Array,
    default: () => [],
  },
  // 模板 id -> 名称 映射，用于在「继承」标签上提示来源模板
  templateNameMap: {
    type: Object,
    default: () => ({}),
  },
  // 派生属性（只读展示，带「派生」标记，不参与保存）：
  // [{ code, name, data_type, sourceLabel, enabled }]
  derivedAttributes: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['saved', 'change'])

const pendingDeleteIdx = ref(-1)

const DATA_TYPES = [
  { value: 'string', label: '文本 (string)' },
  { value: 'text', label: '长文本 (text)' },
  { value: 'number', label: '数字 (number)' },
  { value: 'boolean', label: '布尔 (boolean)' },
  { value: 'date', label: '日期 (date)' },
  { value: 'datetime', label: '日期时间 (datetime)' },
]

const RENDER_HINTS = [
  { value: '', label: '默认' },
  { value: 'text', label: '单行文本' },
  { value: 'textarea', label: '多行文本' },
  { value: 'tag', label: '标签' },
  { value: 'link', label: '链接' },
  { value: 'image', label: '图片' },
  { value: 'badge', label: '徽标' },
]

// 常用正则模板：选中即填入，可再手改
const PATTERN_TEMPLATES = [
  { label: '自定义 / 不使用模板', value: '' },
  { label: '手机号', value: '^1[3-9]\\d{9}$' },
  { label: '邮箱', value: '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$' },
  { label: '身份证号', value: '^\\d{17}[\\dXx]$' },
  { label: '日期 YYYY-MM-DD', value: '^\\d{4}-\\d{2}-\\d{2}$' },
  { label: '纯数字', value: '^\\d+$' },
  { label: '金额（两位小数）', value: '^\\d+(\\.\\d{1,2})?$' },
  { label: '统一社会信用代码', value: '^[0-9A-HJ-NPQRTUWXY]{18}$' },
]

// 违规处置策略
const ON_VIOLATIONS = [
  { value: 'drop_attribute', label: '丢弃该属性值（默认）' },
  { value: 'review', label: '进入人工复核' },
  { value: 'drop_entity', label: '丢弃整个实体' },
]

// 抽取规则展开状态（按索引）
const rulesOpen = ref(new Set())

// 工作副本
const list = ref([])
const expandedId = ref(null)
const saving = ref(false)
const saveError = ref('')

const builtinCodes = computed(() => new Set((props.builtins || []).map(b => b.code).filter(Boolean)))

function syncFromProps() {
  const codes = builtinCodes.value
  // 过滤掉与固有属性编码重复的条目（固有属性由 builtins 单独锁定展示，不进编辑副本、不参与保存）
  list.value = (props.attributes || [])
    .filter(a => !a.code || !codes.has(a.code))
    .map(a => ({
      id: a.id || null,
      name: a.name || '',
      code: a.code || '',
      data_type: a.data_type || 'string',
      description: a.description || '',
      is_required: !!a.is_required,
      default_value: a.default_value || '',
      sort_order: a.sort_order || 0,
      is_edit_only: !!a.is_edit_only,
      render_hint: a.render_hint || '',
      unit: a.unit || '',
      format: a.format || '',
      shared_property_id: a.shared_property_id || '',
      // 抽取规则（属性级）：不配置即不启用，与接入前行为一致
      enum_values: Array.isArray(a.enum_values) ? [...a.enum_values] : [],
      value_pattern: a.value_pattern || '',
      min_value: a.min_value || '',
      max_value: a.max_value || '',
      min_length: a.min_length || 0,
      max_length: a.max_length || 0,
      confidence_threshold: (a.confidence_threshold === null || a.confidence_threshold === undefined) ? null : a.confidence_threshold,
      on_violation: a.on_violation || 'drop_attribute',
      extraction_hint: a.extraction_hint || '',
      extraction_examples: Array.isArray(a.extraction_examples) ? [...a.extraction_examples] : [],
      negative_examples: Array.isArray(a.negative_examples) ? [...a.negative_examples] : [],
      // 来源：'own' 本体自有；'template:xxx' 继承自模板（不可删除；改动只作为自有覆盖）
      _source: a.source || 'own',
      _templateId: (a.source && a.source.startsWith('template:')) ? a.source.slice('template:'.length) : '',
      _templateName: (a.source && a.source.startsWith('template:')) ? (props.templateNameMap[a.source.slice('template:'.length)] || '') : '',
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
    is_edit_only: false,
    render_hint: '',
    unit: '',
    format: '',
    shared_property_id: '',
    _dirty: true,
    _isNew: true,
  }
  list.value.push(newAttr)
  expandedId.value = `new-${list.value.length - 1}`
  emit('change')
}

function askRemoveAttribute(idx) {
  pendingDeleteIdx.value = idx
}

function cancelRemoveAttribute() {
  pendingDeleteIdx.value = -1
}

function confirmRemoveAttribute() {
  const idx = pendingDeleteIdx.value
  if (idx < 0 || idx >= list.value.length) return
  list.value.splice(idx, 1)
  // 重新排序
  list.value.forEach((a, i) => { a.sort_order = i; a._dirty = true })
  pendingDeleteIdx.value = -1
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
    if (a.code?.trim() && builtinCodes.value.has(a.code.trim())) {
      saveError.value = `编码「${a.code.trim()}」为固有属性保留编码，不可使用`
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
  // 校验抽取规则正则合法性（前端预校验，避免脏数据入库）
  for (const a of list.value) {
    const err = patternError(a)
    if (err) {
      saveError.value = `属性「${a.name.trim() || '(未命名)'}」${err}`
      return
    }
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
        is_edit_only: !!a.is_edit_only,
        render_hint: a.render_hint || '',
        unit: (a.unit || '').trim(),
        format: (a.format || '').trim(),
        shared_property_id: a.shared_property_id || '',
        // 抽取规则（属性级）
        enum_values: (a.enum_values || []).length ? a.enum_values : null,
        value_pattern: (a.value_pattern || '').trim(),
        min_value: (a.min_value || '').trim(),
        max_value: (a.max_value || '').trim(),
        min_length: Number(a.min_length) || 0,
        max_length: Number(a.max_length) || 0,
        confidence_threshold: normThreshold(a.confidence_threshold),
        on_violation: a.on_violation || 'drop_attribute',
        extraction_hint: (a.extraction_hint || '').trim(),
        extraction_examples: (a.extraction_examples || []).length ? a.extraction_examples : null,
        negative_examples: (a.negative_examples || []).length ? a.negative_examples : null,
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

// 绑定共享属性：名称/类型跟随共享定义（锁定）
function onSharedPropChange(idx) {
  const attr = list.value[idx]
  if (attr.shared_property_id) {
    const sp = (props.sharedProperties || []).find(p => p.id === attr.shared_property_id)
    if (sp) {
      if (!attr.name.trim()) attr.name = sp.name
      if (!attr.code?.trim() && sp.code) attr.code = sp.code
      if (sp.data_type) attr.data_type = sp.data_type
      if (sp.unit && !attr.unit) attr.unit = sp.unit
      if (sp.format && !attr.format) attr.format = sp.format
    }
  }
  markDirty(idx)
}

function sharedPropOf(attr) {
  return (props.sharedProperties || []).find(p => p.id === attr.shared_property_id) || null
}

// ===== 抽取规则辅助 =====

function toggleRules(idx) {
  const next = new Set(rulesOpen.value)
  if (next.has(idx)) next.delete(idx)
  else next.add(idx)
  rulesOpen.value = next
}

function isRulesOpen(idx) {
  return rulesOpen.value.has(idx)
}

function ruleCount(attr) {
  let n = 0
  if ((attr.enum_values || []).length) n += 1
  if ((attr.value_pattern || '').trim()) n += 1
  if ((attr.min_value || '').trim() || (attr.max_value || '').trim()) n += 1
  if (attr.min_length || attr.max_length) n += 1
  if (attr.confidence_threshold) n += 1
  if ((attr.extraction_hint || '').trim()) n += 1
  if ((attr.extraction_examples || []).length) n += 1
  if ((attr.negative_examples || []).length) n += 1
  return n
}

/** 正则合法性校验：返回空串表示合法 */
function patternError(attr) {
  const p = (attr.value_pattern || '').trim()
  if (!p) return ''
  try {
    new RegExp(p)
    return ''
  } catch (e) {
    return `正则无效：${e.message}`
  }
}

/** 顿号/逗号分隔的字符串 → 数组 */
function onListInput(idx, field, event) {
  const raw = event.target.value || ''
  list.value[idx][field] = raw.split(/[、,，]/).map(s => s.trim()).filter(Boolean)
  markDirty(idx)
}

/** 置信度门槛归一化：空值一律转 null（不限） */
function normThreshold(v) {
  if (v === '' || v === null || v === undefined) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function patternTemplateOf(attr) {
  const p = (attr.value_pattern || '').trim()
  return PATTERN_TEMPLATES.find(t => t.value && t.value === p)?.value || ''
}

function onPatternTemplate(idx, event) {
  list.value[idx].value_pattern = event.target.value || ''
  markDirty(idx)
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

    <div v-if="list.length === 0 && !builtins.length" class="ae-empty">
      暂无{{ title }}，点击「添加属性」开始定义
    </div>

    <div class="ae-list">
      <!-- 固有属性：锁定展示，不可编辑/删除/排序 -->
      <div
        v-for="b in builtins"
        :key="'builtin-' + b.code"
        class="ae-card builtin"
      >
        <div class="ae-card-head">
          <span class="ae-lock" title="固有属性，不可修改或删除">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
          </span>
          <span v-if="b.code" class="ae-code-tag">{{ b.code }}</span>
          <span class="ae-name">{{ b.name }}</span>
          <span class="ae-type-tag">{{ typeLabel(b.data_type) }}</span>
          <span v-if="b.is_required" class="ae-req-tag">必填</span>
          <span class="ae-builtin-tag">固有</span>
          <span class="ae-spacer"></span>
          <span v-if="b.description" class="ae-builtin-desc">{{ b.description }}</span>
        </div>
      </div>
      <!-- 继承自属性模板的属性：只读展示，带「继承」标记，不参与保存 -->
      <div
        v-for="t in inheritedAttributes"
        :key="'tpl-' + (t.code || t.name)"
        class="ae-card inherited"
      >
        <div class="ae-card-head">
          <span class="ae-lock" title="继承自属性模板，需到「属性模板」里修改">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
          </span>
          <span v-if="t.code" class="ae-code-tag">{{ t.code }}</span>
          <span class="ae-name">{{ t.name || '未命名属性' }}</span>
          <span class="ae-type-tag">{{ typeLabel(t.data_type) }}</span>
          <span v-if="t.is_required" class="ae-req-tag">必填</span>
          <span class="ae-tpl-tag" title="继承自属性模板，本体自有同名属性会覆盖它">继承</span>
          <span v-if="t._templateName" class="ae-tpl-src" :title="'继承自属性模板「' + t._templateName + '」'">来自 {{ t._templateName }}</span>
          <span class="ae-spacer"></span>
          <span v-if="t.description" class="ae-builtin-desc">{{ t.description }}</span>
        </div>
      </div>
      <!-- 派生属性：只读展示，运行时由函数/图指标计算，无存储值，不参与保存 -->
      <div
        v-for="d in derivedAttributes"
        :key="'dp-' + (d.code || d.name)"
        class="ae-card derived"
      >
        <div class="ae-card-head">
          <span class="ae-lock" title="派生属性：运行时由函数/图指标计算，无存储值；到「函数与派生属性」页维护">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
          </span>
          <span v-if="d.code" class="ae-code-tag">{{ d.code }}</span>
          <span class="ae-name">{{ d.name || '未命名派生属性' }}</span>
          <span class="ae-type-tag">{{ typeLabel(d.data_type) }}</span>
          <span class="ae-derived-tag" title="派生属性，在实体详情页按需实时计算">派生</span>
          <span class="ae-derived-src" :title="d.sourceLabel">{{ d.sourceLabel }}</span>
          <span v-if="d.enabled === false" class="ae-derived-tag" style="opacity: .55;">停用</span>
          <span class="ae-spacer"></span>
        </div>
      </div>
      <!-- 可编辑属性 -->
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
          <span v-if="attr.is_edit_only" class="ae-editonly-tag" title="仅人工编辑，不参与抽取">仅编辑</span>
          <span v-if="attr._source && attr._source.startsWith('template:')" class="ae-tpl-tag" :title="'继承自属性模板' + (attr._templateName ? '「' + attr._templateName + '」' : '') + '（在此编辑只作为自有覆盖）'">继承{{ attr._templateName ? '·' + attr._templateName : '' }}</span>
          <span v-if="attr.shared_property_id && sharedPropOf(attr)" class="ae-shared-tag" title="已绑定共享属性">共享</span>
          <span v-if="attr._dirty || attr._isNew" class="ae-dirty-dot" title="未保存"></span>
          <span class="ae-spacer"></span>
          <span v-if="editable" class="ae-actions">
            <template v-if="attr._source && attr._source.startsWith('template:')">
              <span class="ae-locked-tip" title="继承自模板的属性不能在此删除，请到「属性模板」里调整">不可删</span>
            </template>
            <template v-else-if="pendingDeleteIdx === idx">
              <span class="ae-del-ask">确认删除？</span>
              <button class="btn xs danger" @click.stop="confirmRemoveAttribute">删除</button>
              <button class="btn xs" @click.stop="cancelRemoveAttribute">取消</button>
            </template>
            <template v-else>
              <button class="rm-btn" @click.stop="askRemoveAttribute(idx)" title="删除">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </template>
          </span>
          <svg class="ae-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </div>

        <div v-if="isExpanded(idx)" class="ae-card-body">
          <div v-if="sharedProperties.length" class="ae-field-row">
            <div class="ae-field">
              <label>绑定共享属性</label>
              <select v-model="attr.shared_property_id" @change="onSharedPropChange(idx)">
                <option value="">不绑定</option>
                <option v-for="sp in sharedProperties" :key="sp.id" :value="sp.id">
                  {{ sp.name }}{{ sp.code ? ` (${sp.code})` : '' }}
                </option>
              </select>
            </div>
            <div class="ae-field">
              <label class="ae-hint-label" v-if="sharedPropOf(attr)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
                已绑定共享属性：类型/单位/格式以共享定义为准，改共享定义全局生效
              </label>
            </div>
          </div>
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
              <select v-model="attr.data_type" @change="markDirty(idx)" :disabled="!!sharedPropOf(attr)">
                <option v-for="d in DATA_TYPES" :key="d.value" :value="d.value">{{ d.label }}</option>
              </select>
            </div>
            <div class="ae-field">
              <label>默认值</label>
              <input type="text" v-model="attr.default_value" @input="markDirty(idx)" placeholder="（可选）">
            </div>
          </div>
          <div class="ae-field-row">
            <div class="ae-field">
              <label>渲染提示</label>
              <select v-model="attr.render_hint" @change="markDirty(idx)">
                <option v-for="r in RENDER_HINTS" :key="r.value" :value="r.value">{{ r.label }}</option>
              </select>
            </div>
            <div class="ae-field">
              <label>单位</label>
              <input type="text" v-model="attr.unit" @input="markDirty(idx)" placeholder="如：万元 / %">
            </div>
            <div class="ae-field">
              <label>格式化</label>
              <input type="text" v-model="attr.format" @input="markDirty(idx)" placeholder="如 #,##0.00 / YYYY-MM-DD">
            </div>
          </div>
          <div class="ae-field">
            <label>描述</label>
            <input type="text" v-model="attr.description" @input="markDirty(idx)" placeholder="该属性的含义说明">
          </div>
          <div class="ae-field-row options">
            <div class="ae-field-check">
              <label>是否必填</label>
              <label class="switch switch-required">
                <input type="checkbox" v-model="attr.is_required" @change="markDirty(idx)">
                <span class="switch-slider"></span>
                <span class="switch-label">{{ attr.is_required ? '必填' : '可选' }}</span>
              </label>
            </div>
            <div class="ae-field-check">
              <label>仅人工编辑</label>
              <label class="switch switch-editonly">
                <input type="checkbox" v-model="attr.is_edit_only" @change="markDirty(idx)">
                <span class="switch-slider"></span>
                <span class="switch-label">{{ attr.is_edit_only ? '不参与抽取' : '参与抽取' }}</span>
              </label>
            </div>
          </div>

          <!-- 抽取规则（可选）：不配置任何规则时，行为与既有抽取完全一致 -->
          <div class="ae-rule-block">
            <div class="ae-rule-head" @click="toggleRules(idx)">
              <svg class="ae-rule-caret" :class="{ open: isRulesOpen(idx) }" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
              <span class="ae-rule-title">抽取规则</span>
              <span v-if="ruleCount(attr)" class="ae-rule-badge">{{ ruleCount(attr) }} 项已配置</span>
              <span v-else class="ae-rule-none">未配置，不启用</span>
            </div>

            <div v-if="isRulesOpen(idx)" class="ae-rule-body" :class="{ disabled: attr.is_edit_only }">
              <div v-if="attr.is_edit_only" class="ae-rule-tip">
                该属性不参与抽取（仅人工编辑），下列规则不会生效
              </div>
              <div class="ae-field">
                <label>抽取提示（写入 Prompt，引导模型）</label>
                <input type="text" v-model="attr.extraction_hint" @input="markDirty(idx)" placeholder="如：仅抽取明确写明的资质限制条款">
              </div>
              <div class="ae-field-row">
                <div class="ae-field">
                  <label>正例</label>
                  <input type="text" :value="(attr.extraction_examples || []).join('、')" @input="onListInput(idx, 'extraction_examples', $event)" placeholder="用、分隔，如：在职、试用">
                </div>
                <div class="ae-field">
                  <label>反例</label>
                  <input type="text" :value="(attr.negative_examples || []).join('、')" @input="onListInput(idx, 'negative_examples', $event)" placeholder="用、分隔，如：未知、待定">
                </div>
              </div>
              <div class="ae-field-row">
                <div class="ae-field">
                  <label>枚举取值</label>
                  <input type="text" :value="(attr.enum_values || []).join('、')" @input="onListInput(idx, 'enum_values', $event)" placeholder="用、分隔，留空不校验">
                </div>
                <div class="ae-field">
                  <label>正则约束</label>
                  <select @change="onPatternTemplate(idx, $event)" :value="patternTemplateOf(attr)">
                    <option v-for="p in PATTERN_TEMPLATES" :key="p.label" :value="p.value">{{ p.label }}</option>
                  </select>
                  <input class="ae-rule-pattern" type="text" v-model="attr.value_pattern" @input="markDirty(idx)" placeholder="留空不校验">
                  <span v-if="patternError(attr)" class="ae-rule-err">{{ patternError(attr) }}</span>
                </div>
              </div>
              <div class="ae-field-row">
                <div class="ae-field">
                  <label>取值范围（数值 / 日期）</label>
                  <div class="ae-range">
                    <input type="text" v-model="attr.min_value" @input="markDirty(idx)" placeholder="最小">
                    <span class="ae-range-sep">~</span>
                    <input type="text" v-model="attr.max_value" @input="markDirty(idx)" placeholder="最大">
                  </div>
                </div>
                <div class="ae-field">
                  <label>长度范围（字符数）</label>
                  <div class="ae-range">
                    <input type="number" min="0" v-model="attr.min_length" @input="markDirty(idx)" placeholder="最少">
                    <span class="ae-range-sep">~</span>
                    <input type="number" min="0" v-model="attr.max_length" @input="markDirty(idx)" placeholder="最多">
                  </div>
                </div>
              </div>
              <div class="ae-field-row">
                <div class="ae-field">
                  <label>置信度门槛（0~1，留空继承对象类型）</label>
                  <input type="number" min="0" max="1" step="0.05" v-model="attr.confidence_threshold" @input="markDirty(idx)" placeholder="留空 = 不限">
                </div>
                <div class="ae-field">
                  <label>违背时处置</label>
                  <select v-model="attr.on_violation" @change="markDirty(idx)">
                    <option v-for="o in ON_VIOLATIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
                  </select>
                </div>
              </div>
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
.ae-card.builtin { border-style: dashed; background: var(--c-muted); }
.ae-card.builtin .ae-card-head { cursor: default; background: transparent; }
.ae-card.builtin .ae-card-head:hover { background: transparent; }
.ae-card.inherited { border-style: dashed; background: var(--c-muted); }
.ae-card.inherited .ae-card-head { cursor: default; background: transparent; }
.ae-card.inherited .ae-card-head:hover { background: transparent; }
.ae-card.derived { border-style: dashed; background: var(--c-muted); border-left: 2px solid rgba(139, 92, 246, 0.55); }
.ae-card.derived .ae-card-head { cursor: default; background: transparent; }
.ae-card.derived .ae-card-head:hover { background: transparent; }
.ae-card.derived .ae-lock { color: #A78BFA; }
.ae-lock { color: var(--c-secondary); flex-shrink: 0; display: inline-flex; align-items: center; }
.ae-builtin-tag { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: rgba(22, 163, 74, 0.12); color: var(--c-success); flex-shrink: 0; }
.ae-builtin-desc { font-size: 11px; color: var(--c-secondary); font-style: italic; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

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
.ae-type-tag, .ae-req-tag, .ae-code-tag, .ae-editonly-tag, .ae-shared-tag, .ae-tpl-tag, .ae-derived-tag {
  font-size: 11px; padding: 1px 7px; border-radius: 10px;
  background: var(--c-muted); color: var(--c-secondary); flex-shrink: 0;
}
.ae-code-tag {
  font-family: ui-monospace, Consolas, monospace;
  background: rgba(14, 116, 144, 0.12);
  color: var(--c-accent);
}
.ae-req-tag { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.ae-editonly-tag { background: rgba(147, 51, 234, 0.12); color: #9333EA; }
.ae-shared-tag { background: rgba(14, 116, 144, 0.12); color: var(--c-accent); }
.ae-tpl-tag { background: rgba(245, 158, 11, 0.14); color: #B45309; }
.ae-tpl-src {
  font-size: 11px; padding: 1px 7px; border-radius: 10px;
  background: rgba(245, 158, 11, 0.08); color: #B45309; flex-shrink: 0;
  max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ae-derived-tag { background: rgba(139, 92, 246, 0.16); color: #A78BFA; }
.ae-derived-src {
  font-size: 11px; padding: 1px 7px; border-radius: 10px;
  background: rgba(139, 92, 246, 0.1); color: #A78BFA; flex-shrink: 0;
  max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ae-locked-tip { font-size: 11px; color: var(--c-secondary); font-style: italic; }
.ae-hint-label {
  display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 500;
  color: var(--c-accent); padding-top: 4px; line-height: 1.4;
}
.ae-dirty-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--c-accent); flex-shrink: 0; }
.ae-spacer { flex: 1; }
.ae-actions { display: inline-flex; align-items: center; gap: 4px; }
.ae-del-ask { font-size: 11px; color: var(--c-danger, #ef4444); margin-right: 2px; }
.ae-actions .btn.xs { padding: 2px 8px; font-size: 11px; }
.ae-actions .btn.xs.danger { background: var(--c-danger, #ef4444); color: #fff; border-color: var(--c-danger, #ef4444); }
.rm-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border: 0; border-radius: var(--radius-sm);
  background: transparent; color: var(--c-secondary); cursor: pointer;
}
.rm-btn:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.ae-caret { color: var(--c-secondary); transition: transform 180ms ease; flex-shrink: 0; }
.ae-card.expanded .ae-caret { transform: rotate(180deg); }

.ae-card-body { padding: 14px 16px 16px; border-top: 1px solid var(--c-border); display: flex; flex-direction: column; gap: 12px; }
.ae-field-row { display: flex; gap: 12px; }
.ae-field-row.options {
  background: var(--c-muted);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  align-items: center;
}
.ae-field { flex: 1; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.ae-field-check {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}
.ae-field-check + .ae-field-check {
  border-left: 1px solid var(--c-border);
  padding-left: 12px;
}
.ae-field-check > label:first-child {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-fg);
}
.ae-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.ae-field input, .ae-field select {
  width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 13px; font-family: var(--font); outline: none;
}
.ae-field input:focus, .ae-field select:focus { border-color: var(--c-fg); }
.ae-field input::placeholder { color: var(--c-secondary); opacity: 0.6; }

.switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; }
.switch input { display: none; }
.switch-slider {
  width: 36px; height: 20px; border-radius: 12px; background: var(--c-border);
  position: relative; transition: background 180ms;
}
.switch-slider::after {
  content: ''; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px;
  border-radius: 50%; background: #fff; transition: transform 180ms;
  box-shadow: 0 1px 3px rgba(0,0,0,0.25);
}
.switch input:checked + .switch-slider { background: var(--c-success); }
.switch-required input:checked + .switch-slider { background: var(--c-danger); }
.switch-editonly input:checked + .switch-slider { background: #9333EA; }
.switch input:checked + .switch-slider::after { transform: translateX(16px); }
.switch input:checked + .switch-slider + .switch-label { color: var(--c-fg); }
.switch-label { font-size: 12px; font-weight: 500; color: var(--c-secondary); min-width: 64px; }
/* ===== 抽取规则区 ===== */
.ae-rule-block {
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-muted);
  overflow: hidden;
}
.ae-rule-head {
  display: flex; align-items: center; gap: 7px;
  padding: 8px 12px; cursor: pointer; user-select: none;
}
.ae-rule-head:hover { background: rgba(0, 0, 0, 0.03); }
.ae-rule-caret { color: var(--c-secondary); transition: transform 150ms; flex-shrink: 0; }
.ae-rule-caret.open { transform: rotate(90deg); }
.ae-rule-title { font-size: 12.5px; font-weight: 600; color: var(--c-fg); }
.ae-rule-badge {
  font-size: 11px; padding: 1px 8px; border-radius: 10px;
  background: rgba(139, 92, 246, 0.14); color: #7C3AED;
}
.ae-rule-none { font-size: 11px; color: var(--c-secondary); }
.ae-rule-body {
  padding: 12px; border-top: 1px solid var(--c-border);
  background: var(--c-panel);
  display: flex; flex-direction: column; gap: 10px;
}
.ae-rule-body.disabled { opacity: 0.55; }
.ae-rule-tip {
  font-size: 11.5px; color: var(--c-warning, #B45309);
  background: rgba(180, 83, 9, 0.08); padding: 5px 9px; border-radius: var(--radius-sm);
}
.ae-rule-pattern { margin-top: 5px; }
.ae-rule-err { display: block; margin-top: 4px; font-size: 11px; color: var(--c-danger); }
.ae-range { display: flex; align-items: center; gap: 6px; }
.ae-range input { flex: 1; min-width: 0; }
.ae-range-sep { color: var(--c-secondary); font-size: 12px; flex-shrink: 0; }
</style>
