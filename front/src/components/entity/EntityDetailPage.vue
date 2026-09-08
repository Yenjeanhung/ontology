<script setup>
import { ref, computed, onMounted, watch, nextTick, onActivated } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getEntityDetail, updateEntity, deleteEntity, fetchFileContent, getFilePreviewUrl, fetchEntityServices, copyServiceToEntity, deleteOntologyService, resolveObjectView, fetchEntities, getMergedAttributes } from '../../api'
import { marked } from 'marked'
import ServiceInvokeDialog from './ServiceInvokeDialog.vue'

const props = defineProps({
  entityId: { type: String, required: true },
})
const router = useRouter()
const route = useRoute()
const entity = ref(null)
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)

const previewAsset = ref(null)
const previewText = ref('')
const previewLoading = ref(false)
const previewMode = ref('preview')
const previewDraft = ref('')
const previewSaving = ref(false)
const previewContentRef = ref(null)
const currentHighlightIndex = ref(0)

const matchCount = computed(() => {
  if (!entity.value?.name || !previewText.value) return 0
  const name = entity.value.name.trim()
  if (!name) return 0
  return [...previewText.value.matchAll(new RegExp(escapeRegExp(name), 'g'))].length
})

// 编辑状态
const editing = ref(false)
const editName = ref('')
const editDesc = ref('')
const editProps = ref([]) // [{ key, value }]

// ===== 服务（动作）=====
const services = ref([])
const servicesLoading = ref(false)
const showInvoke = ref(false)
const invokeTarget = ref(null)

const inheritedServices = computed(() =>
  services.value.filter(s => s.source === 'ontology')
)
const customServices = computed(() =>
  services.value.filter(s => s.owner_type === 'entity')
)

// ===== S6：对象视图（可配置详情页布局）=====
const objectView = ref(null)
const ovTab = ref(0)
const ovLoading = ref(false)
const WIDGET_LABELS = { properties: '属性', relations: '关系', actions: '动作', derived: '派生属性', timeline: '时间线', chart: '图表', stats: '指标卡', table: '关联表格', note: '说明' }

// 图表数据源缓存：关联实体详情 / 同类实体列表
const relatedEntityMap = ref({}) // id -> entity detail
const peerEntities = ref(null) // 同类实体数组
const chartSourcesLoading = ref(false)

function currentViewTab() {
  const tabs = objectView.value?.layout?.tabs
  if (!tabs || !tabs.length) return { name: '', sections: [] }
  const idx = Math.min(ovTab.value, tabs.length - 1)
  return tabs[idx] || tabs[0]
}
function widgetLabel(k) { return WIDGET_LABELS[k] || k }

async function loadObjectView() {
  const cat = entity.value?.category_id
  const ont = entity.value?.ontology_id
  if (!cat || !ont) { objectView.value = null; return }
  ovLoading.value = true
  try {
    objectView.value = await resolveObjectView(cat, ont)
    ovTab.value = 0
    loadChartSources()
  } catch {
    objectView.value = null
  } finally {
    ovLoading.value = false
  }
}

// 扫描布局：按需拉取关联实体详情与同类实体，供图表/表格使用
async function loadChartSources() {
  const layout = objectView.value?.layout
  if (!layout?.tabs) return
  const needsRelated = layout.tabs.some((t) => (t.sections || []).some((s) => (s.widgets || []).some((w) => w.kind === 'chart' || w.kind === 'table')))
  const needsPeers = layout.tabs.some((t) => (t.sections || []).some((s) => (s.widgets || []).some((w) => w.kind === 'chart' && (w.config?.source === 'peers'))))
  if (!needsRelated && !needsPeers) return
  chartSourcesLoading.value = true
  try {
    if (needsRelated && Array.isArray(entity.value?.relations)) {
      const ids = entity.value.relations
        .map((r) => (r.role === 'source' ? r.target_entity_id : r.source_entity_id))
        .filter((id) => id && !relatedEntityMap.value[id])
        .slice(0, 50)
      const details = await Promise.all(ids.map((id) => getEntityDetail(id).catch(() => null)))
      const map = { ...relatedEntityMap.value }
      ids.forEach((id, i) => { if (details[i]) map[id] = details[i] })
      relatedEntityMap.value = map
    }
    if (needsPeers && peerEntities.value === null && entity.value?.category_id) {
      const res = await fetchEntities({ category_id: entity.value.category_id, page: 1, page_size: 50 })
      peerEntities.value = Array.isArray(res) ? res : (res?.items || [])
    }
  } catch {
    // 数据源加载失败时图表显示空态
  } finally {
    chartSourcesLoading.value = false
  }
}

function entityProps(e) {
  if (!e) return {}
  let p = e.properties
  if (typeof p === 'string') {
    try { p = JSON.parse(p) } catch { return {} }
  }
  return p || {}
}

function toNumber(v) {
  if (typeof v === 'number') return v
  if (typeof v !== 'string') return NaN
  return Number(v.trim())
}

// 图表数据：{ points: [{ label, value }], empty: reason }
function chartData(w) {
  const cfg = w.config || {}
  const source = cfg.source || 'self'
  if (source === 'self') {
    const points = Object.entries(parsedProperties.value)
      .map(([k, v]) => ({ label: k, value: toNumber(v) }))
      .filter((d) => !isNaN(d.value) && isFinite(d.value))
    return { points, empty: points.length ? '' : '本实体无可绘图的数值属性，可切换数据源为「关联实体」或「同类实体」' }
  }
  let list = []
  if (source === 'relations') {
    list = (entity.value?.relations || [])
      .map((r) => relatedEntityMap.value[r.role === 'source' ? r.target_entity_id : r.source_entity_id])
      .filter(Boolean)
  } else {
    list = peerEntities.value || []
  }
  const labelField = (cfg.labelField || '').trim()
  const valueField = (cfg.valueField || '').trim()
  const points = []
  for (const e of list) {
    const p = entityProps(e)
    const label = String(labelField ? (p[labelField] ?? '') : (e.name || '')) || '—'
    let value
    if (valueField) value = toNumber(p[valueField])
    else {
      const firstNum = Object.entries(p).find(([, v]) => !isNaN(toNumber(v)) && isFinite(toNumber(v)))
      value = firstNum ? toNumber(firstNum[1]) : NaN
    }
    if (!isNaN(value) && isFinite(value)) points.push({ label, value })
  }
  const emptyReason = chartSourcesLoading.value ? '图表数据加载中...'
    : (source === 'relations' && !(entity.value?.relations || []).length ? '暂无关联实体'
      : points.length ? '' : '所选数据源暂无匹配的数值数据，请检查标签/数值字段配置')
  return { points, empty: emptyReason }
}

const PIE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#f97316', '#84cc16', '#ec4899', '#64748b']

function pieGeom(points) {
  const total = points.reduce((s, d) => s + Math.abs(d.value), 0) || 1
  let angle = -Math.PI / 2
  return points.map((d, i) => {
    const frac = Math.abs(d.value) / total
    const a2 = angle + frac * Math.PI * 2
    const cx = 110, cy = 110, r = 90
    const large = frac > 0.5 ? 1 : 0
    const x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle)
    const x2 = cx + r * Math.cos(a2), y2 = cy + r * Math.sin(a2)
    angle = a2
    return { d: `M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`, color: PIE_COLORS[i % PIE_COLORS.length], frac }
  })
}

function linePath(points, yMax) {
  const n = points.length
  if (!n) return ''
  const max = yMax || niceMax(Math.max(...points.map((d) => d.value), 0))
  const step = 530 / Math.max(n - 1, 1)
  return points
    .map((d, i) => `${i === 0 ? 'M' : 'L'} ${(50 + i * step).toFixed(1)} ${(190 - (d.value / max) * 160).toFixed(1)}`)
    .join(' ')
}

function linePointY(points, i, yMax) {
  const max = yMax || niceMax(Math.max(...points.map((d) => d.value), 0))
  return 190 - (points[i].value / max) * 160
}

// 图表坐标轴与刻度（柱/折线共用）
function niceMax(v) {
  if (v <= 0) return 1
  const exp = Math.floor(Math.log10(v))
  const base = Math.pow(10, exp)
  const m = v / base
  let n
  if (m <= 1) n = 1
  else if (m <= 2) n = 2
  else if (m <= 5) n = 5
  else n = 10
  return n * base
}
function chartLayout(w, data) {
  const cfg = w.config || {}
  const source = cfg.source || 'self'
  const pts = data?.points || []
  const max = niceMax(pts.reduce((s, d) => Math.max(s, d.value), 0))
  const yTicks = [0, max * 0.25, max * 0.5, max * 0.75, max]
  const xTitle = cfg.titleX || (source === 'self' ? '属性' : (cfg.labelField ? attrName(cfg.labelField) : '类别'))
  const yTitle = cfg.titleY || (source === 'self' ? '属性值' : (cfg.valueField ? attrName(cfg.valueField) : '数值'))
  return { xTitle, yTitle, yTicks, yMax: max }
}

// self 模式语义提示：跨字段拼图无意义
function selfHint(w, data) {
  if ((w.config?.source || 'self') !== 'self') return ''
  const pts = data?.points || []
  if (pts.length === 0) return ''
  if (pts.length === 1) return '提示：仅 1 个数值属性，柱状/折线图意义不大，建议改用「指标卡」微件。'
  if (pts.length >= 3) return '提示：当前将本实体各属性拼到 X 轴上，量纲可能不同。推荐改用数据源「关联实体」或「同类实体对比」作真正的对比图。'
  return ''
}

// stats 指标卡数据
function statItems(w) {
  const items = Array.isArray(w.config?.items) ? w.config.items.filter((it) => it.key) : []
  if (items.length) {
    return items.map((it) => {
      const label = it.label || attrLabel(it.key, w)
      return {
        label,
        code: it.key,
        hasAttrName: isFriendlyLabel(it.key, label, w) || !!it.label,
        value: parsedProperties.value[it.key] ?? '—',
        unit: it.unit || '',
      }
    })
  }
  return Object.entries(parsedProperties.value)
    .filter(([, v]) => !isNaN(toNumber(v)) && isFinite(toNumber(v)))
    .slice(0, 6)
    .map(([k, v]) => {
      const label = attrLabel(k, w)
      return { label, code: k, hasAttrName: isFriendlyLabel(k, label, w), value: v, unit: '' }
    })
}

// table 关联表格数据
function tableData(w) {
  const rels = entity.value?.relations || []
  const rows = rels
    .map((r) => {
      const other = relatedEntityMap.value[r.role === 'source' ? r.target_entity_id : r.source_entity_id]
      return other ? { rel: r, ent: other, props: entityProps(other) } : null
    })
    .filter(Boolean)
  const cols = Array.isArray(w.config?.columns) ? w.config.columns.filter(Boolean) : []
  return { rows, cols }
}

function secStyle(sec) {
  const cols = Math.max(1, Math.min(3, sec.columns || 1))
  return { 'grid-template-columns': `repeat(${cols}, minmax(0, 1fr))` }
}
function spanStyle(sec, w) {
  const cols = Math.max(1, Math.min(3, sec.columns || 1))
  const span = Math.max(1, Math.min(cols, w.span || 1))
  return { 'grid-column': `span ${span}` }
}

function renderNote(text) {
  try { return marked.parse(String(text || '')) } catch { return String(text || '') }
}

async function loadServices() {
  servicesLoading.value = true
  try {
    services.value = await fetchEntityServices(props.entityId)
  } catch {
    services.value = []
  } finally {
    servicesLoading.value = false
  }
}

function openInvoke(svc) {
  invokeTarget.value = svc
  showInvoke.value = true
}

function openSvcCreate() {
  router.push({ name: 'entity-service-new', query: { entityId: props.entityId, entityName: entity.value?.name || '' } })
}

function openSvcEdit(svc) {
  router.push({ name: 'entity-service-edit', params: { serviceId: svc.id } })
}

async function onSvcSaved() {
  await loadServices()
}

async function copyToCustom(svc) {
  try {
    await copyServiceToEntity(props.entityId, svc.id)
    await loadServices()
  } catch (e) {
    alert('复制失败：' + e.message)
  }
}

async function removeCustomService(svc) {
  if (!confirm(`确认删除自定义服务「${svc.name}」？`)) return
  try {
    await deleteOntologyService(svc.id)
    await loadServices()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

// 从服务编辑大页面返回时刷新服务列表
onActivated(() => { loadServices() })

const parsedProperties = computed(() => {
  if (!entity.value) return {}
  let p = entity.value.properties
  if (typeof p === 'string') {
    try { p = JSON.parse(p) } catch { return {} }
  }
  return p || {}
})

// 本体属性定义（code -> { code, name, type, ... }）
const attrDefs = ref({})
function humanize(code) {
  if (!code) return ''
  return String(code).replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/\b\w/g, (c) => c.toUpperCase())
}
function attrName(code) {
  const def = attrDefs.value[code]
  return def?.name || humanize(code)
}
function hasAttrName(code) { return !!attrDefs.value[code]?.name }
function attrType(code) { return attrDefs.value[code]?.type || '' }

// 视图层面覆盖：widget.config.labelOverrides = { code: 显示名 }，优先于本体名
function attrLabel(code, widget) {
  const ov = widget?.config?.labelOverrides
  if (ov && ov[code]) return ov[code]
  const def = attrDefs.value[code]
  if (def?.name) return def.name
  return humanize(code)
}
function isFriendlyLabel(code, label, widget) {
  if (widget?.config?.labelOverrides?.[code]) return true
  if (attrDefs.value[code]?.name) return true
  return label !== humanize(code)
}

const derivedEntries = computed(() => {
  if (!entity.value) return []
  const raw = entity.value.derived_properties || entity.value.derived_results
  if (!raw) return []
  if (typeof raw === 'string') {
    try { return Object.entries(JSON.parse(raw)) } catch { return [] }
  }
  return Object.entries(raw)
})

const numericChartData = computed(() => {
  const entries = Object.entries(parsedProperties.value)
    .map(([k, v]) => ({ key: k, value: Number(v) }))
    .filter((d) => !isNaN(d.value) && isFinite(d.value))
  const max = Math.max(...entries.map((d) => d.value), 0)
  return { entries, max }
})

function chartMax(data) {
  return Math.max(...(data?.points || []).map((d) => d.value), 0)
}

const timelineEvents = computed(() => {
  const events = []
  if (entity.value?.created_at) events.push({ time: entity.value.created_at, label: '实体创建' })
  if (entity.value?.updated_at && entity.value.updated_at !== entity.value.created_at) {
    events.push({ time: entity.value.updated_at, label: '最后更新' })
  }
  return events.sort((a, b) => new Date(a.time) - new Date(b.time))
})

function startEdit() {
  editName.value = entity.value.name || ''
  editDesc.value = entity.value.description || ''
  const props = parsedProperties.value
  editProps.value = Object.keys(props).map(k => ({ key: k, value: String(props[k] ?? '') }))
  editing.value = true
}

function cancelEdit() {
  editing.value = false
}

function addProp() {
  editProps.value.push({ key: '', value: '' })
}

function removeProp(idx) {
  editProps.value.splice(idx, 1)
}

async function save() {
  if (!editName.value.trim()) return
  saving.value = true
  // 构建 properties 对象
  const propsObj = {}
  for (const p of editProps.value) {
    const k = p.key.trim()
    if (k) propsObj[k] = p.value
  }
  try {
    const updated = await updateEntity(props.entityId, {
      name: editName.value.trim(),
      description: editDesc.value.trim(),
      properties: propsObj,
    })
    entity.value = updated
    editing.value = false
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!confirm(`确认删除实体「${entity.value.name}」？\n关联关系将一并删除，图谱同步更新。`)) return
  try {
    await deleteEntity(props.entityId)
    if (window.history.length > 1) {
      router.back()
    } else {
      router.push('/entities')
    }
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

function goBack() {
  if (window.history.length > 1) {
    router.back()
    return
  }
  const from = route.query.from
  if (typeof from === 'string' && from.trim()) {
    router.push(from)
    return
  }
  router.push('/entities')
}

async function load() {
  if (!props.entityId) return
  loading.value = true
  loadError.value = ''
  try {
    const data = await getEntityDetail(props.entityId)
    if (!data) {
      loadError.value = '未找到该实体'
      entity.value = null
    } else {
      entity.value = data
      loadServices()
      loadObjectView()
      loadAttrDefs()
    }
  } catch (e) {
    loadError.value = '加载失败：' + e.message
    entity.value = null
  } finally {
    loading.value = false
  }
}

async function loadAttrDefs() {
  const cat = entity.value?.category_id
  const ont = entity.value?.ontology_id
  if (!cat || !ont) { attrDefs.value = {}; return }
  try {
    const res = await getMergedAttributes(cat, ont)
    const arr = Array.isArray(res) ? res : (res?.attributes || [])
    const map = {}
    for (const a of arr) { if (a?.code) map[a.code] = a }
    attrDefs.value = map
  } catch {
    attrDefs.value = {}
  }
}

// 仅展示本体已定义中文名的属性（未定义则视为遗留数据/脏字段，不显示）
const displayedProperties = computed(() => {
  const out = {}
  for (const [k, v] of Object.entries(parsedProperties.value)) {
    if (attrDefs.value[k]?.name) out[k] = v
  }
  return out
})

function fmtTime(t) {
  if (!t) return '—'
  try { return new Date(t).toLocaleString('zh-CN') } catch { return t }
}

function relOtherName(rel) {
  return rel.role === 'source' ? rel.target_entity_name : rel.source_entity_name
}

function relOtherType(rel) {
  return rel.role === 'source'
    ? rel.target_entity_type || rel.relation_type || rel.relation_def_name
    : rel.source_entity_type || rel.relation_type || rel.relation_def_name
}

async function openSourcePreview() {
  if (!entity.value?.source_file_id) return
  previewAsset.value = {
    id: entity.value.source_file_id,
    name: entity.value.source_file_name || entity.value.source_file_id,
    ext: entity.value.source_file_name?.split('.').pop() || '',
    source_type: '来源文件',
    size: 0,
  }
  previewLoading.value = true
  previewMode.value = 'preview'
  previewDraft.value = ''
  try {
    previewText.value = await fetchFileContent(entity.value.source_file_id)
    previewDraft.value = previewText.value
  } catch (e) {
    previewText.value = '文件内容加载失败'
    previewDraft.value = ''
  } finally {
    previewLoading.value = false
  }
}

function closePreview() {
  previewAsset.value = null
  previewText.value = ''
  previewMode.value = 'preview'
  previewDraft.value = ''
  previewSaving.value = false
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function buildHighlightedHtml(raw) {
  if (!entity.value?.name || !raw) return escapeHtml(raw || '')
  const name = entity.value.name
  const escapedName = escapeHtml(name)
  const regex = new RegExp(escapeRegExp(name), 'g')
  let lastIndex = 0
  let result = ''
  let match
  let idx = 0
  while ((match = regex.exec(raw)) !== null) {
    result += escapeHtml(raw.slice(lastIndex, match.index))
    result += `<mark class="entity-highlight" data-highlight-index="${idx}">${escapedName}</mark>`
    lastIndex = match.index + match[0].length
    idx += 1
  }
  result += escapeHtml(raw.slice(lastIndex))
  return result
}

function highlightText(raw) {
  return buildHighlightedHtml(raw)
}

function renderHighlightedMarkdown(raw) {
  if (!raw) return ''
  if (!entity.value?.name) return marked.parse(raw)
  const name = entity.value.name
  let idx = 0
  const parsed = raw.replace(new RegExp(escapeRegExp(name), 'g'), () => {
    const replacement = `<mark class="entity-highlight" data-highlight-index="${idx}">${escapeHtml(name)}</mark>`
    idx += 1
    return replacement
  })
  return marked.parse(parsed)
}

const previewHtml = computed(() => {
  if (!previewText.value) return ''
  if (isMarkdownAsset(previewAsset.value)) {
    return renderHighlightedMarkdown(previewText.value)
  }
  return highlightText(previewText.value)
})

function updateHighlightClass() {
  const container = previewContentRef.value
  if (!container) return
  const marks = Array.from(container.querySelectorAll('.entity-highlight'))
  marks.forEach((el, index) => {
    el.classList.toggle('current-highlight', index === currentHighlightIndex.value)
  })
}

async function scrollToEntityHighlight() {
  await nextTick()
  const container = previewContentRef.value
  if (!container) return
  updateHighlightClass()
  const mark = container.querySelector(`.entity-highlight[data-highlight-index="${currentHighlightIndex.value}"]`)
  if (mark) {
    mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

function gotoHighlight(index) {
  if (!matchCount.value) return
  const next = ((index % matchCount.value) + matchCount.value) % matchCount.value
  currentHighlightIndex.value = next
}

function gotoNextHighlight() {
  gotoHighlight(currentHighlightIndex.value + 1)
}

function gotoPrevHighlight() {
  gotoHighlight(currentHighlightIndex.value - 1)
}

watch([previewText, () => entity.value?.name], async () => {
  currentHighlightIndex.value = 0
  await nextTick()
  scrollToEntityHighlight()
})

watch(currentHighlightIndex, () => {
  scrollToEntityHighlight()
})

function togglePreviewMode(mode) {
  previewMode.value = mode
}

function isEditableTextAsset(asset) {
  return ['txt', 'md', 'csv', 'json', 'html'].includes((asset?.ext || '').toLowerCase())
}

function isMarkdownAsset(asset) {
  return (asset?.ext || '').toLowerCase() === 'md'
}

function gotoSourceFile() {
  if (!entity.value?.source_file_id) return
  openSourcePreview()
}

watch(() => props.entityId, load)
onMounted(load)
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="title-area">
        <button class="back-btn" @click="goBack" title="返回">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
        </button>
        <div class="title-text">
          <h2 class="page-title">{{ entity?.name || '实体详情' }}</h2>
          <span class="page-subtitle" v-if="entity">{{ entity.entity_type || entity.ontology_name || '未分类' }}</span>
        </div>
      </div>
    </div>

    <div v-if="loading" class="loading-state"><span class="spinner"></span> 加载中...</div>
    <div v-else-if="loadError" class="error-state">{{ loadError }}</div>

    <div v-else-if="entity">
      <!-- 基本信息 + 属性 -->
      <div class="detail-card">
        <div class="detail-section">
          <div class="section-head">
            <span class="section-title">基本信息</span>
            <div class="section-actions" v-if="!editing">
              <button class="btn sm danger-outline" @click="remove">删除</button>
              <button class="btn sm primary" @click="startEdit">编辑</button>
            </div>
            <div class="section-actions" v-else>
              <button class="btn sm" @click="cancelEdit">取消</button>
              <button class="btn sm primary" @click="save" :disabled="saving || !editName.trim()">
                <span v-if="saving" class="spinner"></span> 保存
              </button>
            </div>
          </div>

          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">实体名称</span>
              <span class="info-value">
                <input v-if="editing" type="text" v-model="editName" class="info-input">
                <span v-else>{{ entity.name }}</span>
              </span>
            </div>
            <div class="info-item">
              <span class="info-label">本体类型</span>
              <span class="info-value">{{ entity.entity_type || entity.ontology_name || '—' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">描述</span>
              <span class="info-value">
                <input v-if="editing" type="text" v-model="editDesc" class="info-input" placeholder="（无）">
                <span v-else>{{ entity.description || '—' }}</span>
              </span>
            </div>
            <div class="info-item">
              <span class="info-label">来源文件</span>
              <span class="info-value mono clickable" @click="gotoSourceFile">
                {{ entity.source_file_name || entity.source_file_id || '—' }}
              </span>
            </div>
            <div class="info-item">
              <span class="info-label">创建时间</span>
              <span class="info-value">{{ fmtTime(entity.created_at) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">实体 ID</span>
              <span class="info-value mono">{{ entity.id }}</span>
            </div>
          </div>
        </div>

        <!-- 属性 -->
        <div v-if="!objectView" class="detail-section">
          <div class="section-head">
            <span class="section-title">属性</span>
            <button v-if="editing" class="btn sm" @click="addProp">添加属性</button>
          </div>
          <div v-if="editing" class="props-edit">
            <div v-for="(p, idx) in editProps" :key="idx" class="prop-edit-row">
              <input type="text" v-model="p.key" placeholder="属性名" class="prop-key">
              <input type="text" v-model="p.value" placeholder="属性值" class="prop-val">
              <button class="rm-btn sm" @click="removeProp(idx)">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <div v-if="!editProps.length" class="props-empty">无属性，点击「添加属性」</div>
          </div>
          <div v-else>
            <table v-if="Object.keys(displayedProperties).length" class="prop-table">
              <thead>
                <tr>
                  <th>属性编码</th>
                  <th>属性名称</th>
                  <th>属性值</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(v, k) in displayedProperties" :key="k">
                  <td class="prop-code">{{ k }}</td>
                  <td class="prop-name">{{ attrName(k) }}</td>
                  <td class="prop-value">{{ v }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="props-empty">无属性（或本体尚未定义）</div>
          </div>
        </div>
      </div>

      <!-- S6：对象视图（按配置布局渲染） -->
      <div v-if="objectView" class="detail-card">
        <div class="detail-section">
          <div class="section-head">
            <span class="section-title">对象视图：{{ objectView.name }}</span>
            <span class="ov-badge" v-if="objectView.ontology_id">本体级</span>
            <span class="ov-badge" v-else>类别缺省</span>
          </div>
          <div v-if="ovLoading" class="props-empty">加载视图中...</div>
          <template v-else>
            <div class="ov-tabs">
              <button
                v-for="(tab, ti) in objectView.layout.tabs"
                :key="ti"
                class="ov-tab"
                :class="{ on: ovTab === ti }"
                @click="ovTab = ti"
              >{{ tab.name }}</button>
            </div>
            <div v-for="(sec, si) in currentViewTab().sections" :key="si" class="ov-sec">
              <div class="ov-sec-title" v-if="sec.title">{{ sec.title }}</div>
              <div class="ov-grid" :style="secStyle(sec)">
                <div v-for="(w, wi) in sec.widgets" :key="wi" class="ov-widget" :style="spanStyle(sec, w)">
                  <div class="ov-widget-title">{{ w.title || widgetLabel(w.kind) }}</div>
                  <div v-if="w.kind === 'properties'">
                    <table v-if="Object.keys(displayedProperties).length" class="prop-table">
                      <thead>
                        <tr>
                          <th>属性编码</th>
                          <th>属性名称</th>
                          <th>属性值</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(v, k) in displayedProperties" :key="k">
                          <td class="prop-code">{{ k }}</td>
                          <td class="prop-name">{{ attrLabel(k, w) }}</td>
                          <td class="prop-value">{{ v }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else class="props-empty">无属性（或本体尚未定义）</div>
                  </div>
                  <div v-else-if="w.kind === 'relations'" class="rel-list">
                    <div v-for="rel in entity.relations" :key="rel.id" class="rel-item">
                      <span class="rel-current">{{ entity.name }}</span>
                      <span class="rel-arrow">{{ rel.role === 'source' ? '→' : '←' }}</span>
                      <span class="rel-type">{{ rel.relation_def_name || rel.relation_type }}</span>
                      <span class="rel-arrow">{{ rel.role === 'source' ? '→' : '←' }}</span>
                      <span class="rel-other">
                        <span class="rel-other-name">{{ relOtherName(rel) || '—' }}</span>
                        <span class="rel-other-type" v-if="relOtherType(rel)">{{ relOtherType(rel) }}</span>
                      </span>
                    </div>
                    <div v-if="!entity.relations?.length" class="props-empty">无关联关系</div>
                  </div>
                  <div v-else-if="w.kind === 'actions'" class="svc-list">
                    <div v-for="svc in inheritedServices" :key="svc.id" class="svc-row">
                      <span class="svc-status on"></span>
                      <span class="svc-name">{{ svc.name }}</span>
                      <span class="svc-desc" v-if="svc.description">{{ svc.description }}</span>
                      <span class="svc-spacer"></span>
                      <button class="btn sm primary" @click="openInvoke(svc)">执行</button>
                    </div>
                    <div v-if="!inheritedServices.length && !customServices.length" class="props-empty">无可用动作</div>
                  </div>
                  <div v-else-if="w.kind === 'derived'" class="props-view">
                    <div v-for="([k, v]) in derivedEntries" :key="k" class="prop-view-row">
                      <span class="prop-view-key">{{ k }}</span>
                      <span class="prop-view-val">{{ v }}</span>
                    </div>
                    <div v-if="!derivedEntries.length" class="props-empty">暂无派生属性计算结果</div>
                  </div>
                  <div v-else-if="w.kind === 'timeline'" class="timeline-list">
                    <div v-for="(ev, ei) in timelineEvents" :key="ei" class="timeline-item">
                      <div class="timeline-dot"></div>
                      <div class="timeline-meta">
                        <div class="timeline-label">{{ ev.label }}</div>
                        <div class="timeline-time">{{ fmtTime(ev.time) }}</div>
                      </div>
                    </div>
                    <div v-if="!timelineEvents.length" class="props-empty">无时间线数据</div>
                  </div>
                  <div v-else-if="w.kind === 'chart'" class="chart-wrap">
                    <div v-if="selfHint(w, chartData(w))" class="chart-hint">{{ selfHint(w, chartData(w)) }}</div>
                    <template v-if="chartData(w).points.length">
                      <!-- 柱状图 -->
                      <svg v-if="(w.config?.chartType || 'bar') === 'bar'" viewBox="0 0 600 240" preserveAspectRatio="xMidYMid meet" class="chart-svg">
                        <!-- Y 轴刻度 -->
                        <g class="axis-y">
                          <line v-for="(t, i) in chartLayout(w, chartData(w)).yTicks" :key="'y'+i" x1="50" :y1="190 - t / chartLayout(w, chartData(w)).yMax * 160" x2="580" :y2="190 - t / chartLayout(w, chartData(w)).yMax * 160" stroke="var(--c-border)" stroke-dasharray="2 3" />
                          <text v-for="(t, i) in chartLayout(w, chartData(w)).yTicks" :key="'yt'+i" x="46" :y="193 - t / chartLayout(w, chartData(w)).yMax * 160" text-anchor="end" font-size="10" fill="var(--c-secondary)">{{ Number.isInteger(t) ? t : t.toFixed(2) }}</text>
                          <line x1="50" y1="30" x2="50" y2="190" stroke="var(--c-secondary)" />
                          <line x1="50" y1="190" x2="580" y2="190" stroke="var(--c-secondary)" />
                          <text x="20" y="110" :transform="'rotate(-90 20 110)'" text-anchor="middle" font-size="11" fill="var(--c-secondary)">{{ chartLayout(w, chartData(w)).yTitle }}</text>
                        </g>
                        <!-- 柱与 X 标签 -->
                        <g v-for="(d, i) in chartData(w).points" :key="i">
                          <rect
                            :x="60 + i * ((520 - 60) / Math.max(chartData(w).points.length, 1)) + 4"
                            :y="190 - (d.value / Math.max(chartLayout(w, chartData(w)).yMax, 1)) * 160"
                            :width="((520 - 60) / Math.max(chartData(w).points.length, 1)) - 8"
                            :height="(d.value / Math.max(chartLayout(w, chartData(w)).yMax, 1)) * 160"
                            fill="var(--c-accent)"
                            rx="3"
                          />
                          <text
                            :x="60 + i * ((520 - 60) / Math.max(chartData(w).points.length, 1)) + ((520 - 60) / Math.max(chartData(w).points.length, 1)) / 2"
                            y="205"
                            text-anchor="middle"
                            font-size="10"
                            fill="var(--c-secondary)"
                          >{{ d.label }}</text>
                        </g>
                        <text :x="(50 + 580) / 2" y="232" text-anchor="middle" font-size="11" fill="var(--c-secondary)">{{ chartLayout(w, chartData(w)).xTitle }}</text>
                      </svg>
                      <!-- 折线图 -->
                      <svg v-else-if="(w.config?.chartType) === 'line'" viewBox="0 0 600 240" preserveAspectRatio="xMidYMid meet" class="chart-svg">
                        <g class="axis-y">
                          <line v-for="(t, i) in chartLayout(w, chartData(w)).yTicks" :key="'y'+i" x1="50" :y1="190 - t / chartLayout(w, chartData(w)).yMax * 160" x2="580" :y2="190 - t / chartLayout(w, chartData(w)).yMax * 160" stroke="var(--c-border)" stroke-dasharray="2 3" />
                          <text v-for="(t, i) in chartLayout(w, chartData(w)).yTicks" :key="'yt'+i" x="46" :y="193 - t / chartLayout(w, chartData(w)).yMax * 160" text-anchor="end" font-size="10" fill="var(--c-secondary)">{{ Number.isInteger(t) ? t : t.toFixed(2) }}</text>
                          <line x1="50" y1="30" x2="50" y2="190" stroke="var(--c-secondary)" />
                          <line x1="50" y1="190" x2="580" y2="190" stroke="var(--c-secondary)" />
                          <text x="20" y="110" :transform="'rotate(-90 20 110)'" text-anchor="middle" font-size="11" fill="var(--c-secondary)">{{ chartLayout(w, chartData(w)).yTitle }}</text>
                        </g>
                        <path :d="linePath(chartData(w).points, chartLayout(w, chartData(w)).yMax)" fill="none" stroke="var(--c-accent)" stroke-width="2" />
                        <g v-for="(d, i) in chartData(w).points" :key="i">
                          <circle :cx="50 + i * (530 / Math.max(chartData(w).points.length - 1, 1))" :cy="linePointY(chartData(w).points, i, chartLayout(w, chartData(w)).yMax)" r="3.5" fill="var(--c-accent)" />
                          <text :x="50 + i * (530 / Math.max(chartData(w).points.length - 1, 1))" y="205" text-anchor="middle" font-size="10" fill="var(--c-secondary)">{{ d.label }}</text>
                        </g>
                        <text :x="(50 + 580) / 2" y="232" text-anchor="middle" font-size="11" fill="var(--c-secondary)">{{ chartLayout(w, chartData(w)).xTitle }}</text>
                      </svg>
                      <!-- 饼图 -->
                      <div v-else class="pie-flex">
                        <svg viewBox="0 0 220 220" class="chart-svg pie-svg">
                          <path v-for="(g, i) in pieGeom(chartData(w).points)" :key="i" :d="g.d" :fill="g.color" stroke="var(--c-panel)" stroke-width="1.5" />
                        </svg>
                        <div class="pie-legend">
                          <div v-for="(d, i) in chartData(w).points" :key="i" class="pie-legend-item">
                            <span class="pie-dot" :style="{ background: PIE_COLORS[i % PIE_COLORS.length] }"></span>
                            <span class="pie-name">{{ d.label }}</span>
                            <span class="pie-val">{{ d.value }}</span>
                          </div>
                        </div>
                      </div>
                    </template>
                    <div v-else-if="chartData(w).empty" class="props-empty">{{ chartData(w).empty }}</div>
                    <div v-else class="props-empty">无数据</div>
                  </div>
                  <div v-else-if="w.kind === 'stats'" class="stats-grid">
                    <div v-for="(it, i) in statItems(w)" :key="i" class="stat-card">
                      <div class="stat-value">{{ it.value }}<span class="stat-unit" v-if="it.unit">{{ it.unit }}</span></div>
                      <div class="stat-label">
                        {{ it.label }}<span v-if="it.hasAttrName && it.label !== it.code" class="stat-code">({{ it.code }})</span>
                      </div>
                    </div>
                    <div v-if="!statItems(w).length" class="props-empty">无匹配指标，可在视图中配置指标字段</div>
                  </div>
                  <div v-else-if="w.kind === 'table'" class="ov-table-wrap">
                    <table v-if="tableData(w).rows.length" class="ov-table">
                      <thead>
                        <tr>
                          <th>名称</th>
                          <th v-for="c in tableData(w).cols" :key="c">{{ attrLabel(c, w) }}<span class="ov-table-th-code" v-if="isFriendlyLabel(c, attrLabel(c, w), w) && attrLabel(c, w) !== c">({{ c }})</span></th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="row in tableData(w).rows" :key="row.ent.id">
                          <td class="ov-table-name">
                            <span class="ov-table-rel" v-if="row.rel.relation_def_name">{{ row.rel.relation_def_name }}</span>
                            <span>{{ row.ent.name }}</span>
                          </td>
                          <td v-for="c in tableData(w).cols" :key="c">{{ row.props[c] ?? '—' }}</td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else class="props-empty">{{ chartSourcesLoading ? '关联数据加载中...' : '暂无关联实体' }}</div>
                  </div>
                  <div v-else-if="w.kind === 'note'" class="ov-note" v-html="renderNote(w.config?.text)"></div>
                  <div v-else class="ov-placeholder">微件类型 <code>{{ w.kind }}</code>（暂以默认详情页渲染）</div>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- 服务（动作）：本体继承 + 实体自定义 -->
      <div v-if="!objectView" class="detail-card">
        <div class="detail-section">
          <div class="section-head">
            <span class="section-title">服务（动作）</span>
            <button class="btn sm" @click="openSvcCreate">+ 新建自定义服务</button>
          </div>

          <div v-if="servicesLoading" class="props-empty">服务加载中...</div>
          <template v-else>
            <div v-if="!inheritedServices.length && !customServices.length" class="props-empty">
              暂无可用动作：可在本体上定义通用服务，所有实体自动继承
            </div>

            <template v-if="inheritedServices.length">
              <div class="svc-group-title">继承自本体 · {{ inheritedServices.length }}</div>
              <div class="svc-list">
                <div v-for="svc in inheritedServices" :key="svc.id" class="svc-row">
                  <span class="svc-status on" title="继承自本体"></span>
                  <span class="svc-name">{{ svc.name }}</span>
                  <span class="svc-code">{{ svc.code }}</span>
                  <span class="svc-desc" v-if="svc.description" :title="svc.description">{{ svc.description }}</span>
                  <span class="svc-spacer"></span>
                  <button class="btn sm primary" @click="openInvoke(svc)">执行</button>
                  <button class="btn sm" @click="copyToCustom(svc)" title="复制为自定义后可修改覆盖">复制为自定义</button>
                </div>
              </div>
            </template>

            <template v-if="customServices.length">
              <div class="svc-group-title custom">自定义 · {{ customServices.length }}</div>
              <div class="svc-list">
                <div v-for="svc in customServices" :key="svc.id" class="svc-row">
                  <span class="svc-status custom-dot" title="实体自定义"></span>
                  <span class="svc-name">{{ svc.name }}</span>
                  <span class="svc-code">{{ svc.code }}</span>
                  <span v-if="svc.source === 'entity_override'" class="svc-override-tag" title="同名实体服务已覆盖本体动作">已覆盖本体</span>
                  <span class="svc-desc" v-if="svc.description" :title="svc.description">{{ svc.description }}</span>
                  <span class="svc-spacer"></span>
                  <button class="btn sm primary" @click="openInvoke(svc)">执行</button>
                  <button class="btn sm" @click="openSvcEdit(svc)">编辑</button>
                  <button class="rm-btn sm" @click="removeCustomService(svc)" title="删除">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                  </button>
                </div>
              </div>
            </template>
          </template>
        </div>
      </div>

      <!-- 关联关系 -->
      <div v-if="!objectView" class="detail-card">
        <div class="detail-section">
          <div class="section-head">
            <span class="section-title">关联关系 · {{ entity.relations?.length || 0 }}</span>
          </div>
          <div v-if="entity.relations?.length" class="rel-list">
            <div v-for="rel in entity.relations" :key="rel.id" class="rel-item">
              <span class="rel-current">{{ entity.name }}</span>
              <span class="rel-arrow">{{ rel.role === 'source' ? '→' : '←' }}</span>
              <span class="rel-type">{{ rel.relation_def_name || rel.relation_type }}</span>
              <span class="rel-arrow">{{ rel.role === 'source' ? '→' : '←' }}</span>
              <span class="rel-other">
                <span class="rel-other-name">{{ relOtherName(rel) || '—' }}</span>
                <span class="rel-other-type" v-if="relOtherType(rel)">{{ relOtherType(rel) }}</span>
              </span>
            </div>
          </div>
          <div v-else class="props-empty">无关联关系</div>
        </div>
      </div>

      <div class="modal-mask" v-if="previewAsset" @click.self="closePreview">
        <div class="preview-modal" @click.stop>
          <div class="preview-head">
            <div>
              <div class="preview-title">{{ previewAsset.name }}</div>
              <div class="preview-sub">来源文件预览</div>
            </div>
            <button class="icon-btn" @click="closePreview">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
            </button>
          </div>
          <div class="preview-actions">
            <div v-if="matchCount > 0" class="preview-occurrence">
              <button class="small-btn" @click="gotoPrevHighlight" :disabled="matchCount <= 1">上一个</button>
              <span>{{ currentHighlightIndex + 1 }} / {{ matchCount }}</span>
              <button class="small-btn" @click="gotoNextHighlight" :disabled="matchCount <= 1">下一个</button>
            </div>
          </div>
          <div class="preview-body">
            <div v-if="previewLoading" class="empty-state">加载中...</div>
            <iframe v-else-if="previewAsset.ext === 'pdf'" :src="getFilePreviewUrl(previewAsset.id)" class="pdf-frame"></iframe>
            <textarea
              v-else-if="previewMode === 'edit' && isEditableTextAsset(previewAsset)"
              v-model="previewDraft"
              class="preview-editor"
              readonly
            ></textarea>
            <div
              v-else-if="isMarkdownAsset(previewAsset)"
              ref="previewContentRef"
              class="preview-markdown markdown-body"
              v-html="previewHtml"
            ></div>
            <pre
              v-else
              class="preview-text"
              ref="previewContentRef"
              v-html="previewHtml"
            ></pre>
          </div>
        </div>
      </div>

      <ServiceInvokeDialog v-model="showInvoke" :entity-id="entityId" :entity-name="entity?.name || ''" :service="invokeTarget" />
    </div>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.title-area { display: flex; align-items: center; gap: 12px; }
.back-btn { display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; flex-shrink: 0; transition: background 150ms, color 150ms; }
.back-btn:hover { background: var(--c-muted); color: var(--c-fg); }
.title-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }

.loading-state, .error-state { padding: 40px; text-align: center; color: var(--c-secondary); }
.error-state { color: var(--c-danger); }

.detail-card { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); overflow: hidden; }
.detail-section { padding: 16px 20px; border-bottom: 1px solid var(--c-border); }
.detail-section:last-child { border-bottom: 0; }
.section-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.section-title { font-size: 14px; font-weight: 700; color: var(--c-fg); }
.section-actions { display: flex; gap: 8px; }
.btn.sm { padding: 5px 11px; font-size: 12px; }
.btn.sm.danger-outline { color: var(--c-danger); border-color: var(--c-border); }
.btn.sm.danger-outline:hover { background: rgba(220, 38, 38, 0.1); border-color: var(--c-danger); }

.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 24px; }
.info-item { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.info-label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.info-value { font-size: 14px; color: var(--c-fg); word-break: break-word; }
.info-value.mono { font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: var(--c-secondary); }
  .info-value.clickable { cursor: pointer; color: var(--c-accent); transition: color 150ms ease, text-decoration 150ms ease; }
  .info-value.clickable:hover { color: var(--c-fg); text-decoration: underline; }

.props-edit { display: flex; flex-direction: column; gap: 8px; }
.prop-edit-row { display: flex; gap: 8px; align-items: center; }
.prop-key { flex: 0 0 180px; }
.prop-val { flex: 1; min-width: 0; }
.prop-edit-row input { padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; font-family: var(--font); outline: none; }
.prop-edit-row input:focus { border-color: var(--c-fg); }
.rm-btn.sm { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; flex-shrink: 0; }
.rm-btn.sm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }

.props-view { display: flex; flex-direction: column; gap: 6px; }
.prop-view-row { display: flex; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--c-border); }
.prop-view-row:last-child { border-bottom: 0; }
.prop-view-key { flex: 0 0 200px; font-size: 13px; font-weight: 600; color: var(--c-secondary); }
.prop-view-code { color: var(--c-secondary); font-weight: 400; font-size: 12px; opacity: 0.7; margin-left: 4px; }

.prop-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.prop-table th { text-align: left; padding: 6px 10px; color: var(--c-secondary); font-weight: 600; font-size: 12px; border-bottom: 1px solid var(--c-border); white-space: nowrap; background: var(--c-muted); }
.prop-table td { padding: 8px 10px; border-bottom: 1px solid var(--c-border); vertical-align: top; }
.prop-table tr:last-child td { border-bottom: 0; }
.prop-table .prop-code { font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: var(--c-secondary); white-space: nowrap; }
.prop-table .prop-name { color: var(--c-fg); font-weight: 500; }
.prop-table .prop-name-missing { color: var(--c-secondary); font-weight: 400; font-style: italic; opacity: 0.7; }
.prop-name-tip { display: inline-block; font-size: 10px; padding: 0 5px; margin-left: 6px; border-radius: 8px; background: rgba(245,158,11,0.15); color: #b45309; font-style: normal; font-weight: 400; }
.prop-table .prop-value { color: var(--c-fg); word-break: break-word; }
.prop-view-val { flex: 1; font-size: 13px; color: var(--c-fg); word-break: break-word; }
.props-empty { padding: 16px; text-align: center; color: var(--c-secondary); font-size: 13px; }

.svc-group-title { font-size: 12px; font-weight: 700; color: var(--c-secondary); margin: 4px 0 8px; letter-spacing: 0.02em; }
.svc-group-title.custom { color: #8b5cf6; }
.svc-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
.svc-row { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-muted); font-size: 13px; min-height: 40px; }
.svc-status { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; background: var(--c-secondary); }
.svc-status.on { background: var(--c-success); }
.svc-status.custom-dot { background: #8b5cf6; }
.svc-name { font-weight: 600; color: var(--c-fg); white-space: nowrap; }
.svc-code { font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: var(--c-accent); background: rgba(59, 130, 246, 0.1); padding: 1px 8px; border-radius: 4px; white-space: nowrap; }
.svc-desc { color: var(--c-secondary); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }
.svc-spacer { flex: 1; }
.svc-override-tag { font-size: 11px; color: #8b5cf6; border: 1px solid rgba(139, 92, 246, 0.4); border-radius: 4px; padding: 0 6px; white-space: nowrap; }

.rel-list { display: flex; flex-direction: column; gap: 6px; }
.rel-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-muted); font-size: 13px; }
.rel-role { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; font-size: 11px; font-weight: 700; flex-shrink: 0; }
.rel-role.source { background: rgba(22, 163, 74, 0.15); color: var(--c-success); }
.rel-role.target { background: rgba(37, 99, 235, 0.15); color: #2563EB; }
.rel-arrow { color: var(--c-secondary); }
.rel-type { font-weight: 600; color: var(--c-accent); padding: 0 4px; }
.rel-other { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.rel-other-name { font-weight: 600; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rel-other-type { font-size: 11px; color: var(--c-secondary); }

.ov-badge { font-size: 11px; padding: 1px 7px; border-radius: 9px; background: var(--c-muted); color: var(--c-secondary); }
.ov-tabs { display: flex; gap: 6px; border-bottom: 1px solid var(--c-border); padding-bottom: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.ov-tab { padding: 5px 13px; font-size: 12.5px; font-weight: 600; border: 1px solid transparent; border-radius: 999px; background: transparent; color: var(--c-secondary); cursor: pointer; }
.ov-tab:hover { background: var(--c-muted); color: var(--c-fg); }
.ov-tab.on { background: var(--c-fg); color: var(--c-panel); }
.ov-sec { margin-bottom: 12px; }
.ov-sec-title { font-size: 12px; font-weight: 700; color: var(--c-fg); margin: 8px 0 6px; }
.ov-grid { display: grid; gap: 8px; align-items: stretch; }
.ov-widget { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 10px 14px; background: var(--c-muted); overflow: hidden; min-width: 0; }
.ov-widget-title { font-size: 12px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; }
.ov-placeholder { font-size: 12px; color: var(--c-secondary); }
.ov-placeholder code { font-family: ui-monospace, Consolas, monospace; background: var(--c-panel); padding: 0 4px; border-radius: 4px; }

.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
.stat-card { border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); padding: 12px; text-align: center; }
.stat-value { font-size: 22px; font-weight: 700; color: var(--c-fg); line-height: 1.2; word-break: break-all; }
.stat-unit { font-size: 12px; font-weight: 500; color: var(--c-secondary); margin-left: 3px; }
.stat-label { font-size: 12px; color: var(--c-secondary); margin-top: 4px; }
.stat-code { opacity: 0.7; font-weight: 400; margin-left: 3px; }

.ov-table-wrap { overflow-x: auto; }
.ov-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.ov-table th { text-align: left; padding: 6px 10px; color: var(--c-secondary); font-weight: 600; border-bottom: 1px solid var(--c-border); white-space: nowrap; }
.ov-table-th-code { opacity: 0.6; font-weight: 400; margin-left: 3px; }
.ov-table td { padding: 6px 10px; border-bottom: 1px solid var(--c-border); color: var(--c-fg); }
.ov-table tr:last-child td { border-bottom: 0; }
.ov-table-name { display: flex; flex-direction: column; gap: 2px; white-space: nowrap; }
.ov-table-rel { font-size: 10.5px; color: var(--c-secondary); background: var(--c-muted); border-radius: 8px; padding: 0 6px; width: fit-content; }

.pie-flex { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.pie-svg { width: 180px; flex-shrink: 0; }
.pie-legend { display: flex; flex-direction: column; gap: 4px; min-width: 120px; }
.pie-legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.pie-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.pie-name { color: var(--c-fg); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pie-val { color: var(--c-secondary); font-variant-numeric: tabular-nums; }

.ov-note { font-size: 13px; color: var(--c-fg); line-height: 1.7; word-break: break-word; }
.ov-note :first-child { margin-top: 0; }
.ov-note :last-child { margin-bottom: 0; }

.timeline-list { display: flex; flex-direction: column; gap: 0; padding-left: 8px; }
.timeline-item { display: flex; align-items: flex-start; gap: 12px; padding: 10px 0; position: relative; }
.timeline-item:not(:last-child) { border-bottom: 1px solid var(--c-border); }
.timeline-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--c-accent); margin-top: 4px; flex-shrink: 0; }
.timeline-label { font-size: 13px; font-weight: 600; color: var(--c-fg); }
.timeline-time { font-size: 12px; color: var(--c-secondary); }

.chart-wrap { width: 100%; }
.chart-svg { width: 100%; height: auto; max-height: 280px; }
.chart-hint { background: rgba(245, 158, 11, 0.1); color: #b45309; border-left: 3px solid #f59e0b; padding: 6px 10px; border-radius: 4px; font-size: 12px; margin-bottom: 8px; line-height: 1.5; }

  .modal-mask { position: fixed; inset: 0; z-index: 999; display: flex; align-items: center; justify-content: center; background: rgba(0, 0, 0, 0.45); padding: 20px; }
  .preview-modal { width: min(940px, 100%); max-height: min(90vh, 800px); border-radius: 16px; background: var(--c-panel); overflow: hidden; box-shadow: 0 18px 60px rgba(0,0,0,0.22); display: flex; flex-direction: column; }
  .preview-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 20px; border-bottom: 1px solid var(--c-border); }
  .preview-title { font-size: 16px; font-weight: 700; color: var(--c-fg); }
  .preview-sub { font-size: 13px; color: var(--c-secondary); }
  .icon-btn { width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: 10px; background: transparent; color: var(--c-secondary); cursor: pointer; transition: background 150ms ease, color 150ms ease, border-color 150ms ease; }
  .icon-btn:hover { background: rgba(255, 255, 255, 0.08); color: var(--c-fg); border-color: var(--c-border); }
  .preview-body { padding: 16px; flex: 1; overflow: auto; min-height: 280px; }
  .pdf-frame { width: 100%; min-height: 420px; border: 0; }
  .preview-editor, .preview-text { width: 100%; min-height: 320px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-family: ui-monospace, Consolas, monospace; font-size: 13px; padding: 14px; white-space: pre-wrap; word-break: break-word; }
  .preview-editor { resize: vertical; }
  .preview-actions { display: flex; align-items: center; justify-content: flex-start; gap: 12px; padding: 0 20px 12px; }
  .entity-highlight { background: rgba(250, 196, 0, 0.22); color: inherit; border-radius: 4px; padding: 0 2px; transition: background 120ms ease, transform 120ms ease; cursor: default; }
  .entity-highlight:hover { background: rgba(250, 196, 0, 0.35); }
  .entity-highlight.current-highlight { background: rgba(250, 196, 0, 0.55); box-shadow: 0 0 0 2px rgba(250, 196, 0, 0.25); }
  .preview-occurrence { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--c-secondary); }
  .preview-occurrence .small-btn { padding: 6px 11px; border: 1px solid var(--c-border); border-radius: 999px; background: var(--c-panel); color: var(--c-fg); cursor: pointer; transition: background 150ms ease, border-color 150ms ease; }
  .preview-occurrence .small-btn:hover:not(:disabled) { background: rgba(255, 255, 255, 0.08); }
  .preview-occurrence .small-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
