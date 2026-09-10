<template>
  <div class="if-root">
    <div class="if-head">
      <div class="if-title">
        <h3>接口管理</h3>
        <span class="if-sub">面向动作的属性契约：本体实现接口后即可参与多态查询</span>
      </div>
      <button class="btn primary" @click="openCreate">新建接口</button>
    </div>

    <div v-if="loading" class="if-loading">加载中…</div>
    <div v-else-if="!interfaces.length" class="if-empty">暂无接口。点击「新建接口」定义第一个属性契约（如「可投资对象」）。</div>

    <div v-else class="if-list">
      <div v-for="iface in interfaces" :key="iface.id" class="if-item" :class="{ open: expandedId === iface.id }">
        <div class="if-row" @click="toggleExpand(iface.id)">
          <span class="if-chevron" :class="{ open: expandedId === iface.id }">▸</span>
          <span class="if-name">{{ iface.name }}</span>
          <span class="if-code">{{ iface.code }}</span>
          <span class="if-kind" :class="iface.kind">{{ kindLabel(iface.kind) }}</span>
          <span class="if-count">{{ iface.property_count ?? (iface.properties || []).length }} 属性</span>
          <span class="if-count">{{ iface.implementation_count ?? (iface.implementations || []).length }} 实现</span>
          <span class="if-desc" v-if="iface.description">{{ iface.description }}</span>
          <button class="btn sm ghost" @click.stop="openPolyForRow(iface)" title="跨本体多态查询">查询对象</button>
          <button class="btn sm danger" @click.stop="removeIface(iface)" title="删除接口">删除</button>
        </div>

        <div v-if="expandedId === iface.id" class="if-body">
          <div v-if="detailLoading" class="if-loading">加载接口详情…</div>
          <template v-else-if="detail">
            <!-- 基本信息 -->
            <div class="if-sec">
              <div class="if-sec-head">
                <span class="if-sec-title">基本信息</span>
                <button v-if="!editingBase" class="btn sm" @click="startEditBase">编辑</button>
              </div>
              <template v-if="editingBase">
                <div class="if-grid3">
                  <div class="if-field"><label>名称</label><input v-model="baseName" placeholder="如：可投资对象"></div>
                  <div class="if-field"><label>编码</label><input v-model="baseCode" placeholder="如 investable"></div>
                  <div class="if-field">
                    <label>类型</label>
                    <select v-model="baseKind">
                      <option value="functional">功能接口</option>
                      <option value="abstract_object">抽象对象</option>
                    </select>
                  </div>
                </div>
                <div class="if-field"><label>描述</label><input v-model="baseDesc" placeholder="接口的语义说明"></div>
                <div class="if-actions">
                  <button class="btn sm" @click="editingBase = false">取消</button>
                  <button class="btn primary sm" :disabled="baseSaving || !baseName.trim() || !baseCode.trim()" @click="saveBase">
                    <span v-if="baseSaving" class="spinner"></span> 保存
                  </button>
                </div>
              </template>
              <template v-else>
                <p class="if-text">{{ detail.description || '暂无描述' }}</p>
              </template>
            </div>

            <!-- 属性契约 -->
            <div class="if-sec">
              <div class="if-sec-head">
                <span class="if-sec-title">接口属性</span>
                <div class="if-sec-ops">
                  <button class="btn sm" @click="addPropRow">添加属性</button>
                  <button class="btn primary sm" :disabled="propsSaving" @click="saveProps">
                    <span v-if="propsSaving" class="spinner"></span> 保存属性
                  </button>
                </div>
              </div>
              <p class="if-hint">
                把接口属性挂到共享属性后，本体实现该接口时若其属性也引用同一共享属性，系统会自动建立映射，无需逐本体手动配置。
              </p>
              <table v-if="propRows.length" class="if-table">
                <thead>
                  <tr><th>属性名</th><th>编码</th><th>类型</th><th>必填</th><th>共享属性</th><th></th></tr>
                </thead>
                <tbody>
                  <tr v-for="(p, i) in propRows" :key="i">
                    <td><input v-model="p.name" placeholder="如：标的代码"></td>
                    <td><input v-model="p.code" placeholder="如 ticker"></td>
                    <td>
                      <select v-model="p.data_type">
                        <option v-for="d in DATA_TYPES" :key="d" :value="d">{{ d }}</option>
                      </select>
                    </td>
                    <td class="if-center"><input type="checkbox" v-model="p.is_required"></td>
                    <td>
                      <select v-model="p.shared_property_id">
                        <option value="">—</option>
                        <option v-for="sp in sharedProps" :key="sp.id" :value="sp.id">{{ sp.name }}</option>
                      </select>
                    </td>
                    <td class="if-center"><button class="btn sm danger" @click="propRows.splice(i, 1)">删</button></td>
                  </tr>
                </tbody>
              </table>
              <p v-else class="if-text muted">尚无属性。添加属性后，实现该接口的本体需映射这些属性。</p>
            </div>

            <!-- 实现情况 -->
            <div class="if-sec">
              <div class="if-sec-head">
                <span class="if-sec-title">实现情况（{{ detail.implementations.length }}）</span>
                <button v-if="!addingImpl" class="btn sm" @click="openAddImpl">添加实现</button>
              </div>
              <div v-if="detail.implementations.length" class="if-impl-list">
                <div v-for="impl in detail.implementations" :key="impl.ontology_id" class="if-impl-row">
                  <span class="if-impl-ont">{{ impl.ontology_name }}</span>
                  <span class="if-impl-tag" :class="impl.status">{{ impl.status === 'complete' ? '完整映射' : '部分映射' }}</span>
                  <span class="if-impl-map">{{ mapSummary(impl.property_mapping) }}</span>
                  <button class="btn sm danger" @click="removeImpl(impl)">移除</button>
                </div>
              </div>
              <p v-else class="if-text muted">暂无本体实现该接口。</p>
              <p v-if="hasStaleImplementations" class="if-error sm">
                部分实现映射中的接口属性编码已不存在（接口属性已更名），这些属性不再参与多态查询，请到对应本体重新调整映射。
              </p>

              <div v-if="addingImpl" class="if-impl-form">
                <div class="if-grid3">
                  <div class="if-field">
                    <label>选择本体</label>
                    <select v-model="implOntId" @change="loadImplOntAttrs">
                      <option value="">请选择…</option>
                      <option v-for="o in ontologies" :key="o.id" :value="o.id">{{ o.name }}</option>
                    </select>
                  </div>
                </div>
                <table v-if="implOntId && detail.properties.length" class="if-table">
                  <thead>
                    <tr><th>接口属性</th><th>类型</th><th>映射到本体属性</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="p in detail.properties" :key="p.code">
                      <td>{{ p.name }} <span class="if-mini-code">{{ p.code }}</span></td>
                      <td>{{ p.data_type }}</td>
                      <td>
                        <select v-model="implMapping[p.code]">
                          <option value="">未映射</option>
                          <option v-for="a in implOntAttrs" :key="a.value" :value="a.value">{{ a.label }}</option>
                        </select>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div class="if-actions">
                  <button class="btn sm" @click="addingImpl = false">取消</button>
                  <button class="btn primary sm" :disabled="implSaving || !implOntId" @click="saveImpl">
                    <span v-if="implSaving" class="spinner"></span> 保存实现
                  </button>
                </div>
              </div>
            </div>

          </template>
        </div>
      </div>
    </div>

    <!-- 多态查询弹窗（点击遮罩不关闭，仅关闭按钮） -->
    <div v-if="showPoly" class="if-mask poly-mask">
      <div class="if-modal poly-modal">
        <div class="if-modal-head">
          <h3>多态查询预览 · {{ detail?.name }}</h3>
          <button class="btn sm" @click="showPoly = false">关闭</button>
        </div>
        <div class="if-modal-filters">
          <div class="if-field grow">
            <label>实体名</label>
            <input v-model="polyQ" placeholder="按实体名称筛选" @keydown.enter="applyFilters">
          </div>
          <div class="if-field fixed">
            <label>本体名</label>
            <select v-model="polyOntId">
              <option value="">全部本体</option>
              <option v-for="impl in detail?.implementations || []" :key="impl.ontology_id" :value="impl.ontology_id">{{ impl.ontology_name }}</option>
            </select>
          </div>
          <div class="if-field prop" v-for="p in detail?.properties || []" :key="p.code">
            <label :title="`${p.name} (${p.code})`">{{ p.name }}<span class="if-mini-code">{{ p.code }}</span></label>
            <input v-model="polyPropFilters[p.code]" placeholder="按属性值筛选" @keydown.enter="applyFilters">
          </div>
          <button class="btn primary sm filter-btn" :disabled="polyLoading" @click="applyFilters">查询</button>
        </div>
        <div v-if="polyError" class="if-error">{{ polyError }}</div>
        <div v-else-if="polyLoading" class="if-text muted">查询中…</div>
        <div v-else class="poly-body">
          <table v-if="polyRows.length" class="if-table">
            <thead>
              <tr>
                <th>本体</th>
                <th>名称</th>
                <th v-for="p in detail.properties" :key="p.code">{{ p.name }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in polyRows" :key="r.id || i">
                <td><span class="if-mini-code">{{ r._ontology }}</span></td>
                <td>{{ r.name }}</td>
                <td v-for="p in detail.properties" :key="p.code">
                  {{ polyPropMapped(r.ontology_id, p.code) ? (r.properties?.[p.code] ?? '—') : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else-if="polyQueried && !polyRows.length" class="if-text muted">无匹配对象，请调整筛选条件。</p>
        </div>
        <Pagination
          v-if="!polyLoading && !polyError && polyTotal > 0"
          v-model:page="polyPage"
          v-model:page-size="polyPageSize"
          :total="polyTotal"
          @change="onPolyPagerChange"
        />
      </div>
    </div>

    <!-- 新建接口弹窗 -->
    <div v-if="showCreate" class="if-mask">
      <div class="if-modal">
        <h3>新建接口</h3>
        <div class="if-field"><label>名称</label><input v-model="newName" placeholder="如：可投资对象" @keydown.enter="submitCreate"></div>
        <div class="if-field"><label>编码</label><input v-model="newCode" placeholder="如 investable（类别内唯一）"></div>
        <div class="if-field">
          <label>类型</label>
          <select v-model="newKind">
            <option value="functional">功能接口（面向动作）</option>
            <option value="abstract_object">抽象对象（面向分类）</option>
          </select>
        </div>
        <div class="if-field"><label>描述</label><input v-model="newDesc" placeholder="接口的语义说明"></div>
        <div class="if-actions">
          <button class="btn" @click="showCreate = false">取消</button>
          <button class="btn primary" :disabled="creating || !newName.trim() || !newCode.trim()" @click="submitCreate">
            <span v-if="creating" class="spinner"></span> 创建
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import Pagination from '../common/Pagination.vue'
import {
  fetchInterfaces, createInterface, getInterfaceDetail, updateInterface, deleteInterface,
  setInterfaceProperties, implementInterface, removeImplementation,
  fetchOntologies, getOntologyDetail, fetchSharedProperties, resolveInterfaceObjects,
} from '../../api'
import { useEscClose } from '../../composables/useEscClose'

const props = defineProps({ categoryId: { type: String, required: true } })

const DATA_TYPES = ['string', 'text', 'number', 'boolean', 'date', 'datetime']
const kindLabel = k => (k === 'abstract_object' ? '抽象对象' : '功能接口')

const interfaces = ref([])
const loading = ref(false)
const expandedId = ref('')

const detail = ref(null)
const detailLoading = ref(false)

// 基本信息
const editingBase = ref(false)
const baseName = ref(''); const baseCode = ref(''); const baseKind = ref('functional'); const baseDesc = ref('')
const baseSaving = ref(false)

// 属性契约
const propRows = ref([])
const propsSaving = ref(false)

// 实现
const ontologies = ref([])
const sharedProps = ref([])
const addingImpl = ref(false)
const implOntId = ref('')
const implOntAttrs = ref([])   // [{value: 属性编码, label: 名称(编码)}]
const implMapping = ref({})
const implSaving = ref(false)

const hasStaleImplementations = computed(() => {
  const propCodes = new Set((detail.value?.properties || []).map(p => p.code))
  return (detail.value?.implementations || []).some(impl =>
    Object.keys(impl.property_mapping || {}).some(k => !propCodes.has(k))
  )
})

// 多态预览
const polyRows = ref([]); const polyLoading = ref(false); const polyError = ref(''); const polyQueried = ref(false)
const showPoly = ref(false)
const polyPage = ref(1); const polyPageSize = ref(10); const polyTotal = ref(0)
const polyQ = ref('')
const polyOntId = ref('')
const polyPropFilters = ref({})   // {接口属性code: 关键字}

// 新建
const showCreate = ref(false)
const newName = ref(''); const newCode = ref(''); const newKind = ref('functional'); const newDesc = ref('')
const creating = ref(false)

// 弹窗支持按 ESC 关闭
useEscClose(() => [
  [showPoly.value, () => { showPoly.value = false }],
  [showCreate.value, () => { showCreate.value = false }],
])

async function load() {
  loading.value = true
  try {
    const [ifaces, onts, sps] = await Promise.all([
      fetchInterfaces(props.categoryId),
      fetchOntologies(props.categoryId).catch(() => []),
      fetchSharedProperties().catch(() => []),
    ])
    interfaces.value = ifaces
    ontologies.value = onts
    sharedProps.value = sps
  } catch (e) {
    alert('加载接口失败：' + e.message)
  } finally {
    loading.value = false
  }
}

async function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? '' : id
  detail.value = null
  polyRows.value = []; polyError.value = ''; polyQueried.value = false
  showPoly.value = false; polyPage.value = 1; polyTotal.value = 0; polyQ.value = ''; polyOntId.value = ''; polyPropFilters.value = {}
  addingImpl.value = false
  if (expandedId.value) await loadDetail(id)
}

async function loadDetail(id) {
  detailLoading.value = true
  try {
    const d = await getInterfaceDetail(id)
    detail.value = d
    propRows.value = (d.properties || []).map(p => ({
      name: p.name, code: p.code, data_type: p.data_type,
      is_required: !!p.is_required, shared_property_id: p.shared_property_id || '',
    }))
    editingBase.value = false
  } catch (e) {
    alert('加载详情失败：' + e.message)
  } finally {
    detailLoading.value = false
  }
}

function startEditBase() {
  const d = detail.value; if (!d) return
  baseName.value = d.name; baseCode.value = d.code
  baseKind.value = d.kind; baseDesc.value = d.description || ''
  editingBase.value = true
}

async function saveBase() {
  baseSaving.value = true
  try {
    await updateInterface(detail.value.id, {
      name: baseName.value.trim(), code: baseCode.value.trim(),
      kind: baseKind.value, description: baseDesc.value.trim(),
    })
    editingBase.value = false
    await load()
    await loadDetail(detail.value.id)
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    baseSaving.value = false
  }
}

function addPropRow() {
  propRows.value.push({ name: '', code: '', data_type: 'string', is_required: false, shared_property_id: '' })
}

async function saveProps() {
  propsSaving.value = true
  try {
    // 旧接口属性编码集合：保存后属性被改名/删除时，对应实现映射会变成"已失效"
    const oldCodes = new Set((detail.value?.properties || []).map(p => p.code))
    const newCodes = new Set(propRows.value.filter(p => p.code).map(p => p.code))
    const removedOrRenamed = [...oldCodes].filter(c => !newCodes.has(c))
    await setInterfaceProperties(detail.value.id, propRows.value)
    // 实现映射存于接口实现表的 property_mapping（JSON）：键为接口属性 code，值为本体属性 code。
    // 接口属性 code 被改/删后，原 key 变成无效项，这里主动从所有实现映射里剔除，避免堆积失效映射。
    if (removedOrRenamed.length) {
      const impls = detail.value?.implementations || []
      for (const impl of impls) {
        const mapping = impl.property_mapping || {}
        const cleaned = {}
        let changed = false
        for (const k of Object.keys(mapping)) {
          if (newCodes.has(k)) cleaned[k] = mapping[k]
          else changed = true
        }
        if (changed) {
          try {
            await implementInterface(detail.value.id, {
              ontology_id: impl.ontology_id,
              property_mapping: cleaned,
            })
          } catch (e) { /* 单条失败不阻断其它 */ }
        }
      }
    }
    await load()
    await loadDetail(detail.value.id)
  } catch (e) {
    alert('保存契约失败：' + e.message)
  } finally {
    propsSaving.value = false
  }
}

function mapSummary(mapping) {
  const keys = Object.keys(mapping || {})
  if (!keys.length) return '空映射'
  const propCodes = new Set((detail.value?.properties || []).map(p => p.code))
  return keys.map(k => {
    const stale = !propCodes.has(k)
    return `${k}→${mapping[k] || '∅'}${stale ? '（已失效）' : ''}`
  }).join('，')
}

function openAddImpl() {
  implOntId.value = ''; implMapping.value = {}; implOntAttrs.value = []
  addingImpl.value = true
}

async function loadImplOntAttrs() {
  implMapping.value = {}; implOntAttrs.value = []
  if (!implOntId.value || !detail.value) return
  try {
    const d = await getOntologyDetail(props.categoryId, implOntId.value)
    const attrs = [{ name: 'name', code: 'name' }, ...(d.attributes || [])]
    implOntAttrs.value = attrs
      .filter(a => a.code || a.name)
      .map(a => ({
        value: a.code || a.name,
        label: a.code && a.code !== a.name ? `${a.name} (${a.code})` : a.name,
      }))
    // 不自动匹配：映射必须由用户显式建立
    const draft = {}
    for (const p of detail.value.properties || []) draft[p.code] = ''
    implMapping.value = draft
  } catch { /* 忽略，用户可手动选 */ }
}

async function saveImpl() {
  implSaving.value = true
  try {
    await implementInterface(detail.value.id, {
      ontology_id: implOntId.value,
      property_mapping: { ...implMapping.value },
    })
    addingImpl.value = false
    await load()
    await loadDetail(detail.value.id)
  } catch (e) {
    alert('保存实现失败：' + e.message)
  } finally {
    implSaving.value = false
  }
}

async function removeImpl(impl) {
  if (!confirm(`移除「${impl.ontology_name}」对该接口的实现？`)) return
  try {
    await removeImplementation(detail.value.id, impl.ontology_id)
    await load()
    await loadDetail(detail.value.id)
  } catch (e) {
    alert('移除失败：' + e.message)
  }
}

async function openPolyForRow(iface) {
  const switched = expandedId.value !== iface.id || !detail.value
  if (switched) {
    expandedId.value = iface.id
    await loadDetail(iface.id)
  }
  // 换接口时旧的属性筛选 code 不再适用，清空
  if (switched) { polyPropFilters.value = {}; polyQ.value = ''; polyOntId.value = '' }
  showPoly.value = true
  polyPage.value = 1
  runPolyQuery(1)
}

async function runPolyQuery(page = polyPage.value) {
  polyLoading.value = true; polyError.value = ''; polyQueried.value = false
  try {
    const res = await resolveInterfaceObjects(props.categoryId, detail.value.code, {
      q: polyQ.value,
      ontology_id: polyOntId.value,
      prop_filters: { ...polyPropFilters.value },
      limit: polyPageSize.value,
      offset: (page - 1) * polyPageSize.value,
    })
    polyRows.value = (res.items || []).map(o => ({ ...o, _ontology: o.ontology_name || o.ontology_id }))
    polyTotal.value = res.total ?? polyRows.value.length
    polyPage.value = page
    polyQueried.value = true
  } catch (e) {
    polyError.value = '查询失败：' + e.message
  } finally {
    polyLoading.value = false
  }
}

function polyPropMapped(ontologyId, propCode) {
  const impl = (detail.value?.implementations || []).find(i => i.ontology_id === ontologyId)
  return !!(impl?.property_mapping?.[propCode])
}

function applyFilters() {
  polyPage.value = 1
  runPolyQuery(1)
}

function onPolyPagerChange() {
  runPolyQuery(polyPage.value)
}

async function removeIface(iface) {
  if (!confirm(`删除接口「${iface.name}」？其下 ${iface.implementation_count ?? 0} 个实现关系将一并删除。`)) return
  try {
    await deleteInterface(iface.id)
    if (expandedId.value === iface.id) expandedId.value = ''
    await load()
  } catch (e) {
    alert('删除失败：' + e.message)
  }
}

function openCreate() {
  newName.value = ''; newCode.value = ''; newKind.value = 'functional'; newDesc.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  try {
    await createInterface(props.categoryId, {
      name: newName.value.trim(), code: newCode.value.trim(),
      kind: newKind.value, description: newDesc.value.trim(),
    })
    showCreate.value = false
    await load()
  } catch (e) {
    alert('创建失败：' + e.message)
  } finally {
    creating.value = false
  }
}

onMounted(load)
defineExpose({ reload: load })
</script>

<style scoped>
.if-root { display: flex; flex-direction: column; gap: 12px; width: 100%; flex: 1; min-height: 0; overflow: hidden; }
.if-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-shrink: 0; }
.if-title { display: flex; flex-direction: column; gap: 2px; }
.if-title h3 { font-size: 14px; font-weight: 700; margin: 0; color: var(--c-fg); }
.if-sub { font-size: 12px; color: var(--c-secondary); }
.if-loading, .if-empty { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }
.if-error { font-size: 12px; color: var(--c-danger); }
.if-error.sm { font-size: 11px; margin-top: 6px; }

.if-list { display: flex; flex-direction: column; gap: 8px; flex: 1; min-height: 0; overflow-y: auto; }
.if-item { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); overflow: hidden; flex-shrink: 0; }
.if-item.open { border-color: var(--c-fg); }
.if-row { display: flex; align-items: center; gap: 10px; padding: 10px 14px; cursor: pointer; flex-wrap: wrap; }
.if-row:hover { background: var(--c-muted); }
.if-chevron { color: var(--c-secondary); font-size: 11px; transition: transform 120ms; }
.if-chevron.open { transform: rotate(90deg); }
.if-name { font-size: 13px; font-weight: 700; color: var(--c-fg); }
.if-code { font-family: ui-monospace, Consolas, monospace; font-size: 11px; color: var(--c-secondary); background: var(--c-muted); padding: 1px 6px; border-radius: 4px; }
.if-kind { font-size: 10px; padding: 0 6px; border-radius: 4px; background: rgba(37, 99, 235, 0.08); color: #2563EB; }
.if-kind.abstract_object { background: rgba(147, 51, 234, 0.1); color: #9333EA; }
.if-count { font-size: 11px; color: var(--c-secondary); }
.if-desc { font-size: 12px; color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px; }
.if-row .btn { margin-left: auto; }
.if-row .btn + .btn { margin-left: 8px; }
.if-row .btn.ghost { background: transparent; color: var(--c-fg); }
.if-row .btn.ghost:hover { border-color: var(--c-fg); }

.if-body { border-top: 1px solid var(--c-border); padding: 14px 16px; display: flex; flex-direction: column; gap: 18px; background: var(--c-bg); }
.if-sec { display: flex; flex-direction: column; gap: 8px; }
.if-sec-head { display: flex; align-items: center; justify-content: space-between; }
.if-sec-title { font-size: 12.5px; font-weight: 700; color: var(--c-fg); }
.if-sec-ops { display: flex; gap: 8px; }
.if-text { font-size: 12.5px; color: var(--c-secondary); margin: 0; }
.if-text.muted { font-style: italic; }
.if-hint { font-size: 11px; color: var(--c-secondary); margin: 0; line-height: 1.5; }

.if-grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.if-field { display: flex; flex-direction: column; gap: 4px; }
.if-field label { font-size: 11.5px; font-weight: 600; color: var(--c-secondary); }
.if-field input, .if-field select { width: 100%; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12.5px; font-family: var(--font); outline: none; }
.if-field input:focus, .if-field select:focus { border-color: var(--c-fg); }
.if-actions { display: flex; justify-content: flex-end; gap: 8px; }

.if-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.if-table th, .if-table td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--c-border); }
.if-table th { color: var(--c-secondary); font-weight: 500; font-size: 11px; }
.if-table input, .if-table select { width: 100%; padding: 4px 8px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 12px; outline: none; }
.if-center { text-align: center; }
.if-mini-code { font-family: ui-monospace, Consolas, monospace; font-size: 10px; color: var(--c-secondary); margin-left: 4px; }

.if-impl-list { display: flex; flex-direction: column; gap: 4px; }
.if-impl-row { display: flex; align-items: center; gap: 10px; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); font-size: 12px; }
.if-impl-ont { font-weight: 600; color: var(--c-fg); }
.if-impl-tag { font-size: 10px; padding: 0 6px; border-radius: 4px; }
.if-impl-tag.complete { background: rgba(34, 197, 94, 0.12); color: #16A34A; }
.if-impl-tag.partial { background: rgba(245, 158, 11, 0.15); color: #B45309; }
.if-impl-map { color: var(--c-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.if-impl-row .btn { flex-shrink: 0; }
.if-impl-form { display: flex; flex-direction: column; gap: 10px; border-top: 1px dashed var(--c-border); padding-top: 10px; }

.if-mask { position: fixed; inset: 0; background: var(--c-overlay); display: flex; align-items: center; justify-content: center; z-index: 100; padding: 20px; }
.if-modal { background: var(--c-panel); border-radius: var(--radius); padding: 22px; width: 100%; max-width: 460px; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.18); }
.if-modal.poly-modal { max-width: 92vw; width: 1180px; height: 84vh; display: flex; flex-direction: column; }
.if-modal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.if-modal-filters { display: flex; flex-wrap: wrap; gap: 10px 12px; align-items: flex-end; margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px dashed var(--c-border); flex-shrink: 0; }
.if-modal-filters .if-field { margin-bottom: 0; }
.if-modal-filters .if-field.grow { flex: 1 1 220px; min-width: 180px; }
.if-modal-filters .if-field.fixed { flex: 0 0 180px; }
.if-modal-filters .if-field.prop { flex: 1 1 170px; min-width: 150px; max-width: 250px; }
.if-modal-filters .if-field label { font-size: 11.5px; font-weight: 600; color: var(--c-secondary); display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; overflow: hidden; max-width: 100%; }
.if-modal-filters .if-field label .if-mini-code { margin-left: 0; flex-shrink: 0; }
.if-modal-filters .filter-btn { flex: 0 0 auto; padding: 7px 16px; }
.poly-body { flex: 1; min-height: 0; overflow-y: auto; border: 1px solid var(--c-border); border-radius: var(--radius-sm); }
.poly-body .if-table { margin: 0; }
.poly-body .if-table thead th { position: sticky; top: 0; background: var(--c-muted); z-index: 1; font-weight: 600; }
.poly-body .if-table tbody tr:nth-child(even) { background: var(--c-bg); }
.poly-body .if-table tbody tr:hover { background: var(--c-muted); }
.if-modal-head h3 { font-size: 15px; font-weight: 700; color: var(--c-fg); margin: 0; }
.if-modal h3 { font-size: 15px; font-weight: 700; margin-bottom: 16px; color: var(--c-fg); }
.if-modal .if-field { margin-bottom: 12px; }

.btn.sm { padding: 5px 11px; font-size: 12px; }
.btn.danger { color: var(--c-danger); }
.spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: if-spin 700ms linear infinite; vertical-align: -2px; margin-right: 4px; }
@keyframes if-spin { to { transform: rotate(360deg); } }
</style>
