<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import {
  createOntology, updateOntology, deleteOntology,
  replaceOntologyAttributes,
  fetchAttributeTemplates,
  fetchOntologies,
  getOntologyDetail,
  setOntologyTemplates,
  getMergedAttributes,
  fetchOntologyServices,
  deleteOntologyService,
  updateOntologyService,
} from '../../api'
import AttributeEditor from '../common/AttributeEditor.vue'
import SearchableSelect from '../common/SearchableSelect.vue'
import ServiceEditorDialog from './ServiceEditorDialog.vue'

const props = defineProps({
  categoryId: { type: String, required: true },
})
const emit = defineEmits(['changed'])

const COLOR_PRESETS = ['#A16207', '#2563EB', '#16A34A', '#DC2626', '#9333EA', '#0891B2', '#DB2777', '#475569']

// 所有实体固有的属性：name 是实体表一等字段，用于列表/详情/图谱展示，
// 不作为普通本体属性存储，因此在编辑器中以"固有"锁定呈现，不可增删改。
const BUILTIN_ATTRS = [
  {
    code: 'name',
    name: '名称',
    data_type: 'string',
    is_required: true,
    description: '实体固有标识，用于列表/详情/图谱展示',
  },
]

// ── 列表（轻量，仅计数） ──
const list = ref([])
const listLoading = ref(false)

// ── 详情抽屉（点击时按需加载） ──
const currentId = ref(null)
const drawerOpen = ref(false)
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')

// 基础信息编辑（抽屉内）
const editingInfo = ref(false)
const savingInfo = ref(false)
const editName = ref('')
const editDesc = ref('')
const editColor = ref('')

// 模板绑定（仅当前本体）
const tplBinding = ref([])
const tplDirty = ref(false)
const savingTpl = ref(false)
const templates = ref([])

// 合并属性预览（仅当前本体，按需加载）
const merged = ref(null)
const mergedVisible = ref(false)

// 本体服务（仅当前本体）
const services = ref([])
const svcLoading = ref(false)
const showSvcDialog = ref(false)
const svcEditing = ref(null)
const svcOwner = ref(null)

// 新建本体
const showCreate = ref(false)
const newName = ref('')
const newDesc = ref('')
const newColor = ref(COLOR_PRESETS[0])
const creating = ref(false)

const templateOptions = computed(() =>
  templates.value.map(t => ({ value: t.id, label: t.name, meta: `${t.attribute_count} 个属性` }))
)

const currentRow = computed(() => list.value.find(o => o.id === currentId.value) || null)

function fmtDate(v) {
  if (!v) return '—'
  const s = String(v)
  return s.length >= 10 ? s.slice(0, 10) : s
}

// ── 数据加载 ──

async function loadList() {
  listLoading.value = true
  try {
    list.value = await fetchOntologies(props.categoryId)
    // 若当前抽屉中的本体已被删除，关闭抽屉
    if (currentId.value && !list.value.some(o => o.id === currentId.value)) {
      closeDetail()
    }
  } catch {
    list.value = []
  } finally {
    listLoading.value = false
  }
}

async function loadTemplates() {
  try {
    templates.value = await fetchAttributeTemplates()
  } catch {}
}

async function loadDetail(ontId) {
  detailLoading.value = true
  detailError.value = ''
  try {
    const [d, svcs] = await Promise.all([
      getOntologyDetail(props.categoryId, ontId),
      fetchOntologyServices(props.categoryId, ontId).catch(() => []),
    ])
    detail.value = d
    services.value = svcs
    tplBinding.value = [...(d.template_ids || [])]
    tplDirty.value = false
    editingInfo.value = false
    mergedVisible.value = false
    merged.value = null
  } catch (e) {
    detailError.value = '加载失败：' + e.message
    detail.value = null
  } finally {
    detailLoading.value = false
  }
}

async function loadServicesOnly() {
  svcLoading.value = true
  try {
    services.value = await fetchOntologyServices(props.categoryId, currentId.value)
  } catch {
    services.value = []
  } finally {
    svcLoading.value = false
  }
}

// 保存后：刷新列表计数 + 抽屉详情（若打开）
async function refreshAfterChange() {
  emit('changed')
  await loadList()
  if (drawerOpen.value && currentId.value) {
    await loadDetail(currentId.value)
  }
}

watch(() => props.categoryId, () => {
  closeDetail()
  list.value = []
  loadList()
})

onMounted(() => {
  loadList()
  loadTemplates()
})

function openDetail(ont) {
  currentId.value = ont.id
  drawerOpen.value = true
  detail.value = null
  loadDetail(ont.id)
}

function closeDetail() {
  drawerOpen.value = false
  editingInfo.value = false
}

// ── 新建本体 ──

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
    const ont = await createOntology(props.categoryId, {
      name: newName.value.trim(),
      description: newDesc.value.trim(),
      color: newColor.value,
    })
    showCreate.value = false
    await refreshAfterChange()
    openDetail({ id: ont.id })
  } catch (e) {
    alert('创建失败：' + e.message)
  } finally {
    creating.value = false
  }
}

// ── 基础信息 ──

function startEditInfo() {
  const d = detail.value
  if (!d) return
  editName.value = d.name
  editDesc.value = d.description || ''
  editColor.value = d.color || COLOR_PRESETS[0]
  editingInfo.value = true
}

async function saveInfo() {
  if (!detail.value || !editName.value.trim()) return
  savingInfo.value = true
  try {
    await updateOntology(props.categoryId, detail.value.id, {
      name: editName.value.trim(),
      description: editDesc.value.trim(),
      color: editColor.value,
    })
    await refreshAfterChange()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    savingInfo.value = false
  }
}

async function removeOntology(ont) {
  if (!confirm(`确认删除本体「${ont.name}」？\n其下属性、三元组引用、模板绑定将一并删除。`)) return
  try {
    await deleteOntology(props.categoryId, ont.id)
    if (currentId.value === ont.id) closeDetail()
    await refreshAfterChange()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

// ── 模板绑定 ──

function onTplChange(ids) {
  tplBinding.value = ids
  tplDirty.value = true
}

async function saveTplBindings() {
  if (!detail.value) return
  savingTpl.value = true
  try {
    await setOntologyTemplates(props.categoryId, detail.value.id, {
      template_ids: tplBinding.value || [],
    })
    tplDirty.value = false
    await loadMerged()
    await refreshAfterChange()
  } catch (e) {
    alert('保存模板绑定失败：' + e.message)
  } finally {
    savingTpl.value = false
  }
}

// ── 属性 ──

async function saveAttributes(payload) {
  const result = await replaceOntologyAttributes(props.categoryId, detail.value.id, payload)
  if (merged.value) await loadMerged()
  await refreshAfterChange()
  return result
}

// ── 合并属性预览 ──

async function loadMerged() {
  merged.value = { loading: true }
  try {
    merged.value = await getMergedAttributes(props.categoryId, detail.value.id)
  } catch (e) {
    merged.value = { error: e.message }
  }
}

function toggleMerged() {
  if (mergedVisible.value) {
    mergedVisible.value = false
  } else {
    mergedVisible.value = true
    if (!merged.value) loadMerged()
  }
}

function attrSourceLabel(source) {
  if (source === 'own') return '自有'
  if (source && source.startsWith('template:')) return '模板'
  return source || ''
}

// ── 本体服务（动作） ──

function openSvcCreate() {
  const d = detail.value
  if (!d) return
  svcEditing.value = null
  svcOwner.value = { type: 'ontology', categoryId: props.categoryId, ontologyId: d.id, ontologyName: d.name }
  showSvcDialog.value = true
}

function openSvcEdit(svc) {
  const d = detail.value
  if (!d) return
  svcEditing.value = svc
  svcOwner.value = { type: 'ontology', categoryId: props.categoryId, ontologyId: d.id, ontologyName: d.name }
  showSvcDialog.value = true
}

async function onSvcSaved() {
  await loadServicesOnly()
  await loadList()
}

async function toggleSvcEnabled(svc) {
  try {
    await updateOntologyService(svc.id, {
      name: svc.name, code: svc.code, description: svc.description || '',
      params: svc.params || [], code_text: svc.code_text || '', language: svc.language || 'python',
      timeout_seconds: svc.timeout_seconds, is_enabled: !svc.is_enabled, sort_order: svc.sort_order || 0,
    })
    await onSvcSaved()
  } catch (e) {
    alert('操作失败：' + e.message)
  }
}

async function removeSvc(svc) {
  if (!confirm(`确认删除服务「${svc.name}」？其下实体的继承动作将一并失效。`)) return
  try {
    await deleteOntologyService(svc.id)
    await onSvcSaved()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

function paramSummary(svc) {
  const n = svc.params?.length || 0
  return n ? `${n} 参数` : '无参数'
}
</script>

<template>
  <div class="oe-root">
    <div class="oe-head">
      <div class="oe-tip">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
        每个本体代表一类实体（如「人物」「组织」），点击行查看与编辑详情。
      </div>
      <button class="btn primary sm" @click="openCreate">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        新建本体
      </button>
    </div>

    <div v-if="listLoading && !list.length" class="oe-loading"><span class="spinner"></span> 加载中...</div>

    <div v-else-if="!list.length" class="oe-empty">
      暂无本体，点击「新建本体」开始定义实体类型
    </div>

    <!-- 本体列表（表格） -->
    <div v-else class="oe-table-card">
      <table class="oe-table">
        <thead>
          <tr>
            <th>本体名称</th>
            <th class="num-col">实体</th>
            <th class="num-col">属性</th>
            <th class="num-col">模板</th>
            <th class="num-col">服务</th>
            <th class="date-col">创建时间</th>
            <th class="op-col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="ont in list"
            :key="ont.id"
            :class="{ active: ont.id === currentId }"
            @click="openDetail(ont)"
          >
            <td>
              <div class="oe-cell-name">
                <span class="oe-color-dot" :style="{ background: ont.color || '#A16207' }"></span>
                <div class="oe-cell-text">
                  <span class="oe-name">{{ ont.name }}</span>
                  <span class="oe-desc" :title="ont.description">{{ ont.description || '—' }}</span>
                </div>
              </div>
            </td>
            <td class="num-cell">{{ ont.entity_count ?? '—' }}</td>
            <td class="num-cell">{{ ont.attribute_count ?? '—' }}</td>
            <td class="num-cell">{{ ont.template_count || '—' }}</td>
            <td class="num-cell">{{ ont.service_count || '—' }}</td>
            <td class="date-cell">{{ fmtDate(ont.created_at) }}</td>
            <td class="op-cell" @click.stop>
              <button class="oe-link-btn" @click="openDetail(ont)">编辑</button>
              <button class="oe-link-btn danger" @click="removeOntology(ont)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 详情抽屉 -->
    <div v-if="drawerOpen" class="oe-drawer-mask" @click.self="closeDetail">
      <div class="oe-drawer">
        <div class="oe-drawer-head">
          <span class="oe-color-dot lg" :style="{ background: (detail || currentRow)?.color || '#A16207' }"></span>
          <div class="oe-drawer-title">
            <span class="oe-drawer-name">{{ (detail || currentRow)?.name }}</span>
            <span v-if="detail" class="oe-drawer-meta">
              {{ detail.entity_count }} 实体 · {{ detail.attribute_count }} 属性 · {{ detail.template_count }} 模板 · {{ detail.service_count }} 服务
            </span>
          </div>
          <button class="oe-close-btn" @click="closeDetail" aria-label="关闭">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        <div class="oe-drawer-body">
          <div v-if="detailLoading" class="oe-loading"><span class="spinner"></span> 加载详情...</div>
          <div v-else-if="detailError" class="oe-merged-error">{{ detailError }}</div>

          <template v-else-if="detail">
            <!-- 基础信息 -->
            <div class="oe-section">
              <div class="oe-section-head">
                <span class="oe-section-title">基础信息</span>
                <button v-if="!editingInfo" class="btn sm" @click="startEditInfo">编辑</button>
              </div>
              <template v-if="editingInfo">
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
                  <button class="btn sm" @click="editingInfo = false">取消</button>
                  <button class="btn primary sm" @click="saveInfo" :disabled="savingInfo || !editName.trim()">
                    <span v-if="savingInfo" class="spinner"></span> 保存
                  </button>
                </div>
              </template>
              <template v-else>
                <div class="oe-info-view">
                  <span class="oe-info-desc" v-if="detail.description">{{ detail.description }}</span>
                  <span class="oe-info-desc placeholder" v-else>暂无描述</span>
                </div>
              </template>
            </div>

            <!-- 属性模板引用 -->
            <div class="oe-section">
              <div class="oe-section-head">
                <span class="oe-section-title">引用属性模板</span>
                <button
                  v-if="tplDirty"
                  class="btn primary sm"
                  @click="saveTplBindings"
                  :disabled="savingTpl"
                >
                  <span v-if="savingTpl" class="spinner"></span> 保存绑定
                </button>
              </div>
              <SearchableSelect
                :model-value="tplBinding"
                :options="templateOptions"
                multiple
                placeholder="选择要引用的属性模板（可多选）..."
                @change="onTplChange"
              />
            </div>

            <!-- 本体自有属性 -->
            <div class="oe-section">
              <div class="oe-section-head">
                <span class="oe-section-title">本体属性</span>
              </div>
              <AttributeEditor
                :attributes="detail.attributes"
                :builtins="BUILTIN_ATTRS"
                :save-fn="saveAttributes"
                @saved="() => {}"
              />
            </div>

            <!-- 合并属性预览 -->
            <div class="oe-section">
              <div class="oe-section-head">
                <span class="oe-section-title">合并属性预览</span>
                <button class="btn sm" @click="toggleMerged">
                  {{ mergedVisible ? '收起' : '查看' }}
                </button>
              </div>
              <div v-if="mergedVisible && merged" class="oe-merged">
                <div v-if="merged.loading" class="oe-merged-loading">
                  <span class="spinner"></span> 加载中...
                </div>
                <div v-else-if="merged.error" class="oe-merged-error">{{ merged.error }}</div>
                <div v-else>
                  <div v-if="merged.conflicts?.length" class="oe-conflict-note">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                    存在 {{ merged.conflicts.length }} 个同名冲突，已以自有属性覆盖：{{ merged.conflicts.join('、') }}
                  </div>
                  <div class="oe-merged-list">
                    <div
                      v-for="a in merged.attributes"
                      :key="a.name"
                      class="oe-merged-attr"
                      :class="{ conflict: merged.conflicts?.includes(a.name) }"
                    >
                      <span v-if="a.code" class="oe-merged-code">{{ a.code }}</span>
                      <span class="oe-merged-name">{{ a.name }}</span>
                      <span class="oe-merged-type">{{ a.data_type }}</span>
                      <span class="oe-merged-src" :class="{ own: a.source === 'own' }">{{ attrSourceLabel(a.source) }}</span>
                    </div>
                  </div>
                  <div v-if="!merged.attributes?.length" class="oe-merged-empty">
                    暂无合并属性（无自有属性且未引用模板）
                  </div>
                </div>
              </div>
            </div>

            <!-- 本体服务（动作） -->
            <div class="oe-section">
              <div class="oe-section-head">
                <span class="oe-section-title">本体服务（动作）</span>
                <button class="btn sm" @click="openSvcCreate">+ 新建服务</button>
              </div>
              <div class="oe-svc-tip">
                定义该类实体的通用动作（如调用 API、处理数据），其下所有实体自动继承并可执行。
              </div>
              <div v-if="svcLoading" class="oe-merged-loading"><span class="spinner"></span> 加载中...</div>
              <template v-else>
                <div v-if="!services.length" class="oe-merged-empty">
                  暂无服务，点击「新建服务」为本体添加通用动作
                </div>
                <div v-else class="oe-svc-list">
                  <div v-for="svc in services" :key="svc.id" class="oe-svc-row" :class="{ disabled: !svc.is_enabled }">
                    <span class="oe-svc-status" :class="svc.is_enabled ? 'on' : 'off'" :title="svc.is_enabled ? '已启用' : '已停用'"></span>
                    <span class="oe-svc-name">{{ svc.name }}</span>
                    <span class="oe-svc-code">{{ svc.code }}</span>
                    <span class="oe-svc-meta">{{ paramSummary(svc) }}</span>
                    <span v-if="svc.description" class="oe-svc-desc" :title="svc.description">{{ svc.description }}</span>
                    <span class="oe-spacer"></span>
                    <button class="btn sm ghost" @click="toggleSvcEnabled(svc)" :title="svc.is_enabled ? '停用' : '启用'">
                      {{ svc.is_enabled ? '停用' : '启用' }}
                    </button>
                    <button class="btn sm" @click="openSvcEdit(svc)">编辑</button>
                    <button class="rm-btn sm" @click="removeSvc(svc)" title="删除">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                  </div>
                </div>
              </template>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 服务编辑弹窗 -->
    <ServiceEditorDialog
      v-model="showSvcDialog"
      :owner="svcOwner"
      :service="svcEditing"
      @saved="onSvcSaved"
    />

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
.oe-root { display: flex; flex-direction: column; gap: 12px; width: 100%; }
.oe-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.oe-tip { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-secondary); }
.btn.sm { padding: 5px 11px; font-size: 12px; }
.oe-loading { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; }
.oe-empty { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }

/* ─── 表格 ─── */
.oe-table-card { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); overflow: hidden; }
.oe-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.oe-table thead th {
  text-align: left; padding: 9px 14px;
  font-size: 12px; font-weight: 600; color: var(--c-secondary);
  background: var(--c-muted); border-bottom: 1px solid var(--c-border);
  white-space: nowrap;
}
.oe-table tbody td { padding: 10px 14px; border-bottom: 1px solid var(--c-border); vertical-align: middle; }
.oe-table tbody tr:last-child td { border-bottom: none; }
.oe-table tbody tr { cursor: pointer; transition: background 120ms; }
.oe-table tbody tr:hover { background: var(--c-muted); }
.oe-table tbody tr.active { background: var(--c-muted); }
.oe-table .num-col { width: 60px; text-align: right; }
.oe-table .num-cell { text-align: right; font-variant-numeric: tabular-nums; color: var(--c-fg); }
.oe-table .date-col { width: 100px; }
.oe-table .date-cell { font-size: 12px; color: var(--c-secondary); font-variant-numeric: tabular-nums; white-space: nowrap; }
.oe-table .op-col { width: 110px; text-align: right; }
.oe-table .op-cell { text-align: right; white-space: nowrap; }

.oe-cell-name { display: flex; align-items: center; gap: 10px; min-width: 0; }
.oe-color-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.oe-color-dot.lg { width: 14px; height: 14px; }
.oe-cell-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.oe-name { font-size: 13px; font-weight: 600; color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.oe-desc { font-size: 11.5px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 380px; }

.oe-link-btn { border: 0; background: transparent; padding: 4px 6px; font-size: 12.5px; font-family: var(--font); color: var(--c-accent); cursor: pointer; border-radius: 4px; }
.oe-link-btn:hover { background: var(--c-muted-hover); }
.oe-link-btn.danger { color: var(--c-danger); }

/* ─── 抽屉 ─── */
.oe-drawer-mask { position: fixed; inset: 0; background: var(--c-overlay); z-index: 90; display: flex; justify-content: flex-end; }
.oe-drawer {
  width: min(680px, 92vw); height: 100%;
  background: var(--c-panel); border-left: 1px solid var(--c-border);
  display: flex; flex-direction: column;
  box-shadow: -12px 0 40px rgba(0, 0, 0, 0.14);
  animation: oe-drawer-in 180ms ease;
}
@keyframes oe-drawer-in { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }
.oe-drawer-head {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 20px; border-bottom: 1px solid var(--c-border); flex-shrink: 0;
}
.oe-drawer-title { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.oe-drawer-name { font-size: 15px; font-weight: 700; color: var(--c-fg); }
.oe-drawer-meta { font-size: 11.5px; color: var(--c-secondary); }
.oe-close-btn { width: 30px; height: 30px; display: inline-flex; align-items: center; justify-content: center; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; flex-shrink: 0; }
.oe-close-btn:hover { background: var(--c-muted); color: var(--c-fg); }
.oe-drawer-body { flex: 1; overflow-y: auto; padding: 18px 20px; display: flex; flex-direction: column; gap: 20px; }

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

/* 本体服务区块 */
.oe-svc-tip { font-size: 12px; color: var(--c-secondary); }
.oe-svc-list { display: flex; flex-direction: column; gap: 4px; }
.oe-svc-row { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); font-size: 12px; }
.oe-svc-row.disabled { opacity: 0.55; }
.oe-svc-status { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.oe-svc-status.on { background: #16A34A; box-shadow: 0 0 6px rgba(22, 163, 74, 0.6); }
.oe-svc-status.off { background: var(--c-secondary); }
.oe-svc-name { font-weight: 600; color: var(--c-fg); flex-shrink: 0; }
.oe-svc-code { color: #2563EB; font-family: ui-monospace, Consolas, monospace; font-size: 11px; padding: 0 6px; border-radius: 8px; background: rgba(37, 99, 235, 0.08); flex-shrink: 0; }
.oe-svc-meta { color: var(--c-secondary); flex-shrink: 0; }
.oe-svc-desc { color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }
.oe-spacer { flex: 1; }
.rm-btn.sm { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--c-secondary); cursor: pointer; }
.rm-btn.sm:hover { background: rgba(220, 38, 38, 0.1); color: var(--c-danger); }
.btn.sm.ghost { background: transparent; border-color: var(--c-border); color: var(--c-secondary); }
.btn.sm.ghost:hover { border-color: var(--c-fg); color: var(--c-fg); }

.oe-modal-mask { position: fixed; inset: 0; background: var(--c-overlay); display: flex; align-items: center; justify-content: center; z-index: 100; padding: 20px; }
.oe-modal { background: var(--c-panel); border-radius: var(--radius); padding: 22px; width: 100%; max-width: 460px; box-shadow: 0 8px 30px rgba(0,0,0,0.18); }
.oe-modal h3 { font-size: 15px; font-weight: 700; margin-bottom: 16px; color: var(--c-fg); }
.oe-modal .oe-field { margin-bottom: 12px; }
.oe-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
