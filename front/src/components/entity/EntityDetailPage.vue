<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getEntityDetail, updateEntity, deleteEntity, fetchFileContent, getFilePreviewUrl } from '../../api'
import { marked } from 'marked'

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

const parsedProperties = computed(() => {
  if (!entity.value) return {}
  let p = entity.value.properties
  if (typeof p === 'string') {
    try { p = JSON.parse(p) } catch { return {} }
  }
  return p || {}
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
  if (!confirm(`确认删除实体「${entity.value.name}」？\n关联关系将一并删除，Kùzu 图谱同步更新。`)) return
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
    }
  } catch (e) {
    loadError.value = '加载失败：' + e.message
    entity.value = null
  } finally {
    loading.value = false
  }
}

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
        <div class="detail-section">
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
          <div v-else class="props-view">
            <div v-for="(v, k) in parsedProperties" :key="k" class="prop-view-row">
              <span class="prop-view-key">{{ k }}</span>
              <span class="prop-view-val">{{ v }}</span>
            </div>
            <div v-if="!Object.keys(parsedProperties).length" class="props-empty">无属性</div>
          </div>
        </div>
      </div>

      <!-- 关联关系 -->
      <div class="detail-card">
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
.prop-view-key { flex: 0 0 160px; font-size: 13px; font-weight: 600; color: var(--c-secondary); }
.prop-view-val { flex: 1; font-size: 13px; color: var(--c-fg); word-break: break-word; }
.props-empty { padding: 16px; text-align: center; color: var(--c-secondary); font-size: 13px; }

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
