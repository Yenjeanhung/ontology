<script setup>
import { ref, computed, onMounted, onActivated } from 'vue'
import { fetchKbs, fetchCleanupSuggestions, applyCleanup } from '../../api'
import { useToast } from '../../composables/useToast'
import SearchableSelect from '../common/SearchableSelect.vue'
import ModalDialog from '../common/ModalDialog.vue'

const toast = useToast()

const kbs = ref([])
const selectedKbId = ref('')

const loading = ref(false)
const applying = ref(false)
const hasLoaded = ref(false)

// 三类建议（清洗前端可勾选/改 canonical 的可变副本）
const groups = ref([])        // 合并组
const delEntities = ref([])   // 待删实体
const delRelations = ref([])  // 待删通用关系
const summary = ref({})

const kbOptions = computed(() => [
  { value: '', label: '请选择知识库', meta: '' },
  ...kbs.value.map(k => ({ value: k.id, label: k.name, meta: `${k.file_count || 0} 文件` })),
])

const selectedCounts = computed(() => {
  const mergeCount = groups.value.filter(g =>
    g.canonicalId && g.members.some(m => m.id !== g.canonicalId && g.memberChecked[m.id])
  ).length
  const delEntCount = delEntities.value.filter(e => e.checked).length
  const delRelCount = delRelations.value.filter(r => r.checked).length
  return { mergeCount, delEntCount, delRelCount }
})

const hasAny = computed(() =>
  !!(groups.value.length || delEntities.value.length || delRelations.value.length)
)

const allClean = computed(() => hasLoaded.value && !hasAny.value)

const activeTab = ref('merge')
const tabs = computed(() => [
  { key: 'merge', label: '合并重复实体', count: groups.value.length },
  { key: 'entities', label: '噪声实体', count: delEntities.value.length },
  { key: 'relations', label: '无语义关系', count: delRelations.value.length },
])

function initFromSuggestions(data) {
  groups.value = (data.merge_groups || []).map(g => ({
    entity_type: g.entity_type,
    reason: g.reason,
    members: g.members,
    canonicalId: g.canonical_id,
    memberChecked: Object.fromEntries((g.members || []).map(m => [m.id, true])),
  }))
  delEntities.value = (data.delete_entities || []).map(e => ({ ...e, checked: true }))
  delRelations.value = (data.delete_relations || []).map(r => ({ ...r, checked: true }))
  summary.value = data.summary || {}
  // 默认落到第一个有建议的类别 Tab
  if (groups.value.length) activeTab.value = 'merge'
  else if (delEntities.value.length) activeTab.value = 'entities'
  else if (delRelations.value.length) activeTab.value = 'relations'
}

async function loadSuggestions() {
  if (!selectedKbId.value) {
    toast.info('请先选择知识库')
    return
  }
  loading.value = true
  hasLoaded.value = true
  try {
    const data = await fetchCleanupSuggestions(selectedKbId.value)
    initFromSuggestions(data)
  } catch (e) {
    toast.error('生成清洗建议失败：' + e.message)
    groups.value = []
    delEntities.value = []
    delRelations.value = []
  } finally {
    loading.value = false
  }
}

function payloadFor(scope) {
  const merges = groups.value
    .filter(g => g.canonicalId)
    .map(g => ({
      canonical_id: g.canonicalId,
      merged_ids: g.members
        .filter(m => m.id !== g.canonicalId && g.memberChecked[m.id])
        .map(m => m.id),
    }))
    .filter(g => g.merged_ids.length > 0)
  const deleteEntityIds = delEntities.value.filter(e => e.checked).map(e => e.id)
  const deleteRelationIds = delRelations.value.filter(r => r.checked).map(r => r.id)
  const kbId = selectedKbId.value
  if (scope === 'merge') return { kbId, merges, deleteEntityIds: [], deleteRelationIds: [] }
  if (scope === 'entities') return { kbId, merges: [], deleteEntityIds, deleteRelationIds: [] }
  if (scope === 'relations') return { kbId, merges: [], deleteEntityIds: [], deleteRelationIds }
  return { kbId, merges, deleteEntityIds, deleteRelationIds }
}

const SCOPE_LABEL = { all: '全部', merge: '合并重复实体', entities: '噪声实体', relations: '无语义关系' }
const confirmOpen = ref(false)
const confirmScope = ref('all')
const confirmPayload = computed(() => payloadFor(confirmScope.value))
const confirmTitle = computed(() =>
  confirmScope.value === 'all' ? '确认全部清洗' : `确认清洗：${SCOPE_LABEL[confirmScope.value]}`
)

function askCleanup(scope) {
  const p = payloadFor(scope)
  if (!p.merges.length && !p.deleteEntityIds.length && !p.deleteRelationIds.length) {
    toast.info('该范围未勾选任何清洗项')
    return
  }
  confirmScope.value = scope
  confirmOpen.value = true
}

async function runCleanup() {
  const payload = payloadFor(confirmScope.value)
  applying.value = true
  try {
    const res = await applyCleanup(payload)
    toast.success(
      `清洗完成：合并 ${res.merged || 0} 个实体，` +
      `删除 ${res.entities_deleted || 0} 个实体 / ${res.relations_deleted || 0} 条关系`
    )
    confirmOpen.value = false
    await loadSuggestions()
  } catch (e) {
    toast.error('清洗失败：' + e.message)
  } finally {
    applying.value = false
  }
}

// —— 逐条处理：单组合并 / 单个删除，立即执行，无需走批量确认弹窗。
// 后端 applyCleanup 天然支持单元素数组，单条操作不会触发 80% 安全护栏。 ——
async function applyOne(payload) {
  applying.value = true
  try {
    const res = await applyCleanup(payload)
    toast.success(
      `已处理：合并 ${res.merged || 0} 个实体，` +
      `删除 ${res.entities_deleted || 0} 个实体 / ${res.relations_deleted || 0} 条关系`
    )
    await loadSuggestions()
  } catch (e) {
    toast.error('清洗失败：' + e.message)
  } finally {
    applying.value = false
  }
}

// 单组合并：尊重当前选定的主实体与成员勾选状态
function mergeOneGroup(g) {
  const mergedIds = g.members
    .filter(m => m.id !== g.canonicalId && g.memberChecked[m.id])
    .map(m => m.id)
  if (!mergedIds.length) { toast.info('该组未勾选可合并成员'); return }
  return applyOne({
    kbId: selectedKbId.value,
    merges: [{ canonical_id: g.canonicalId, merged_ids: mergedIds }],
    deleteEntityIds: [],
    deleteRelationIds: [],
  })
}

function deleteOneEntity(e) {
  return applyOne({
    kbId: selectedKbId.value,
    merges: [],
    deleteEntityIds: [e.id],
    deleteRelationIds: [],
  })
}

function deleteOneRelation(r) {
  return applyOne({
    kbId: selectedKbId.value,
    merges: [],
    deleteEntityIds: [],
    deleteRelationIds: [r.id],
  })
}

function onKbChange() {
  // 切换知识库即清空旧建议，等用户重新生成
  groups.value = []
  delEntities.value = []
  delRelations.value = []
  summary.value = {}
  hasLoaded.value = false
}

let firstActivate = true
onMounted(async () => {
  try { kbs.value = await fetchKbs() } catch { kbs.value = [] }
})

// keep-alive 重新激活时刷新知识库列表
onActivated(async () => {
  if (firstActivate) { firstActivate = false; return }
  try { kbs.value = await fetchKbs() } catch {}
})
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">图谱清洗</h2>
        <span class="page-subtitle">合并重复实体、删除噪声节点与无语义关系，让图谱更精简可读</span>
      </div>
    </div>

    <!-- 工具条：知识库选择 + 生成建议 -->
    <div class="toolbar">
      <div class="kb-filter">
        <SearchableSelect
          v-model="selectedKbId"
          :options="kbOptions"
          :searchable="true"
          placeholder="选择知识库"
          @change="onKbChange"
        />
      </div>
      <button class="btn primary" :disabled="!selectedKbId || loading || applying" @click="loadSuggestions">
        <span v-if="loading" class="spinner sm"></span>
        {{ loading ? '分析中...' : '生成清洗建议' }}
      </button>
    </div>

    <!-- 概要 -->
    <div v-if="hasLoaded && summary.entity_total !== undefined" class="summary-bar">
      <span class="sum-chip">实体 {{ summary.entity_total }}</span>
      <span class="sum-chip">关系 {{ summary.relation_total }}</span>
      <span class="sum-chip" v-if="summary.merge_group_count">建议合并 {{ summary.merge_group_count }} 组</span>
      <span class="sum-chip" v-if="summary.delete_entity_count">噪声实体 {{ summary.delete_entity_count }}</span>
      <span class="sum-chip" v-if="summary.delete_relation_count">无语义关系 {{ summary.delete_relation_count }}</span>
    </div>

    <div v-if="loading && !hasAny" class="loading-state"><span class="spinner"></span> 分析图谱中...</div>

    <template v-else-if="hasAny">
      <!-- 类别 Tab -->
      <div class="tabs">
        <button
          v-for="t in tabs"
          :key="t.key"
          class="tab"
          :class="{ active: activeTab === t.key }"
          @click="activeTab = t.key"
        >
          <span class="tab-label">{{ t.label }}</span>
          <span class="tab-count">{{ t.count }}</span>
        </button>
      </div>

      <div class="tab-body">
        <!-- 合并组 -->
        <section v-show="activeTab === 'merge'" class="clean-section">
          <div v-if="!groups.length" class="tab-empty">该类别暂无建议</div>
          <template v-else>
            <div class="section-head">
              <span class="section-hint">名称高度相似的同类实体，可调整主实体或取消勾选部分成员</span>
              <button class="btn danger sm" :disabled="!selectedCounts.mergeCount || applying" @click="askCleanup('merge')">
                仅清洗此类（{{ selectedCounts.mergeCount }}）
              </button>
            </div>
            <div class="group-list">
              <div v-for="(g, gi) in groups" :key="gi" class="group-card">
                <div class="group-card-head">
                  <span class="type-tag">{{ g.entity_type }}</span>
                  <span class="group-reason">{{ g.reason }}</span>
                  <div class="canonical-pick">
                    <span class="canonical-label">合并为：</span>
                    <select v-model="g.canonicalId" class="canonical-select">
                      <option v-for="m in g.members" :key="m.id" :value="m.id">{{ m.name }}</option>
                    </select>
                  </div>
                  <button class="btn primary sm" :disabled="applying" @click="mergeOneGroup(g)">合并本组</button>
                </div>
                <div class="member-list">
                  <label
                    v-for="m in g.members"
                    :key="m.id"
                    class="member-row"
                    :class="{ 'is-canonical': m.id === g.canonicalId }"
                  >
                    <input type="checkbox" v-model="g.memberChecked[m.id]">
                    <span class="member-name">{{ m.name }}</span>
                    <span class="member-degree">度数 {{ m.degree }}</span>
                    <span v-if="m.id === g.canonicalId" class="canonical-tag">主实体</span>
                  </label>
                </div>
              </div>
            </div>
          </template>
        </section>

        <!-- 待删实体 -->
        <section v-show="activeTab === 'entities'" class="clean-section">
          <div v-if="!delEntities.length" class="tab-empty">该类别暂无建议</div>
          <template v-else>
            <div class="section-head">
              <span class="section-hint">日期、数值、整句等低价值实体名（孤岛节点不再自动建议删除，避免级联清空）</span>
              <button class="btn danger sm" :disabled="!selectedCounts.delEntCount || applying" @click="askCleanup('entities')">
                仅清洗此类（{{ selectedCounts.delEntCount }}）
              </button>
            </div>
            <div class="check-table">
              <div v-for="e in delEntities" :key="e.id" class="check-row">
                <label class="check-main">
                  <input type="checkbox" v-model="e.checked">
                  <span class="check-name">{{ e.name }}</span>
                  <span class="type-tag">{{ e.entity_type || '—' }}</span>
                  <span class="check-reason">{{ e.reason }}</span>
                </label>
                <button class="btn danger sm row-action" :disabled="applying" @click="deleteOneEntity(e)">删除</button>
              </div>
            </div>
          </template>
        </section>

        <!-- 待删关系 -->
        <section v-show="activeTab === 'relations'" class="clean-section">
          <div v-if="!delRelations.length" class="tab-empty">该类别暂无建议</div>
          <template v-else>
            <div class="section-head">
              <span class="section-hint">"涉及/提到/关联"等无信息量关系，建议删除</span>
              <button class="btn danger sm" :disabled="!selectedCounts.delRelCount || applying" @click="askCleanup('relations')">
                仅清洗此类（{{ selectedCounts.delRelCount }}）
              </button>
            </div>
            <div class="check-table">
              <div v-for="r in delRelations" :key="r.id" class="check-row">
                <label class="check-main">
                  <input type="checkbox" v-model="r.checked">
                  <span class="rel-tag">{{ r.relation_type }}</span>
                  <span class="check-reason">无语义通用关系</span>
                </label>
                <button class="btn danger sm row-action" :disabled="applying" @click="deleteOneRelation(r)">删除</button>
              </div>
            </div>
          </template>
        </section>
      </div>

      <!-- 执行栏 -->
      <div class="action-bar">
        <div class="action-summary">
          将合并 <b>{{ selectedCounts.mergeCount }}</b> 组 ·
          删除 <b>{{ selectedCounts.delEntCount }}</b> 个实体 ·
          删除 <b>{{ selectedCounts.delRelCount }}</b> 条关系
        </div>
        <button
          class="btn danger solid"
          :disabled="applying || (!selectedCounts.mergeCount && !selectedCounts.delEntCount && !selectedCounts.delRelCount)"
          @click="askCleanup('all')"
        >
          {{ applying ? '执行中...' : '全部清洗' }}
        </button>
      </div>
    </template>

    <!-- 空态 -->
    <div v-else-if="allClean" class="empty-state">
      <div class="empty-icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
      </div>
      <div class="empty-title">图谱已经很干净</div>
      <div class="empty-desc">未发现重复实体、噪声节点或无语义关系</div>
    </div>

    <div v-else class="empty-state">
      <div class="empty-icon">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      </div>
      <div class="empty-title">选择知识库并生成清洗建议</div>
      <div class="empty-desc">系统会自动识别可合并的重复实体、噪声节点与无语义关系</div>
    </div>

    <!-- 清洗确认弹窗 -->
    <ModalDialog
      v-model="confirmOpen"
      :title="confirmTitle"
      size="sm"
      confirm-text="确认清洗"
      confirm-variant="danger"
      :confirm-loading="applying"
      @confirm="runCleanup"
    >
      <div class="confirm-body">
        <p class="confirm-line">本次将执行以下操作：</p>
        <ul class="confirm-list">
          <li v-if="confirmPayload.merges.length">合并 <b>{{ confirmPayload.merges.length }}</b> 组重复实体</li>
          <li v-if="confirmPayload.deleteEntityIds.length">删除 <b>{{ confirmPayload.deleteEntityIds.length }}</b> 个噪声实体</li>
          <li v-if="confirmPayload.deleteRelationIds.length">删除 <b>{{ confirmPayload.deleteRelationIds.length }}</b> 条无语义关系</li>
        </ul>
        <p class="confirm-warn">此操作不可撤销，将同步写入 SQLite 与图谱。</p>
      </div>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }

.toolbar { display: flex; align-items: center; gap: 10px; }
.kb-filter { width: 280px; flex-shrink: 0; }

.summary-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sum-chip { font-size: 12px; padding: 3px 10px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); }

.clean-section { display: flex; flex-direction: column; gap: 10px; }
.section-head { display: flex; align-items: center; gap: 12px; }
.section-head .section-hint { flex: 1; min-width: 0; }
.btn.sm { padding: 4px 11px; font-size: 12px; }
.btn.danger.solid { background: var(--c-danger); border-color: var(--c-danger); color: #fff; }
.btn.danger.solid:hover { opacity: 0.88; background: var(--c-danger); }
.section-title { font-size: 15px; font-weight: 700; color: var(--c-fg); margin: 0; }
.section-hint { font-size: 12px; color: var(--c-secondary); }

/* 合并组 */
.group-list { display: flex; flex-direction: column; gap: 8px; }
.group-card { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 12px 14px; }
.group-card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.group-reason { font-size: 12px; color: var(--c-secondary); flex: 1; min-width: 80px; }
.canonical-pick { display: flex; align-items: center; gap: 6px; }
.canonical-label { font-size: 12px; color: var(--c-secondary); }
.canonical-select {
  appearance: none; padding: 4px 26px 4px 10px; height: 30px; min-width: 160px; max-width: 280px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel) url("data:image/svg+xml,%3Csvg width='10' height='6' viewBox='0 0 10 6' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23888' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat right 8px center;
  color: var(--c-fg); font-size: 13px; font-family: var(--font); cursor: pointer; outline: none;
}
.canonical-select:focus { border-color: var(--c-fg); }

.member-list { display: flex; flex-direction: column; gap: 2px; }
.member-row { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: var(--radius-sm); cursor: pointer; font-size: 13px; }
.member-row:hover { background: var(--c-muted); }
.member-row.is-canonical { background: rgba(160, 98, 7, 0.08); }
.member-name { color: var(--c-fg); font-weight: 500; }
.member-degree { font-size: 11px; color: var(--c-secondary); margin-left: auto; }
.canonical-tag { font-size: 10px; padding: 1px 7px; border-radius: 8px; background: var(--c-accent); color: #fff; }

/* 待删列表 */
.check-table { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); overflow: hidden; }
.check-row { display: flex; align-items: center; gap: 10px; padding: 9px 14px; border-bottom: 1px solid var(--c-border); font-size: 13px; transition: background 120ms; }
.check-row:last-child { border-bottom: 0; }
.check-row:hover { background: var(--c-muted); }
.check-main { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; cursor: pointer; }
.row-action { flex-shrink: 0; }
.check-name { color: var(--c-fg); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 340px; }
.type-tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); flex-shrink: 0; }
.rel-tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: rgba(220, 38, 38, 0.1); color: var(--c-danger); flex-shrink: 0; font-weight: 600; }
.check-reason { font-size: 12px; color: var(--c-secondary); margin-left: auto; flex-shrink: 0; }

/* 执行栏 */
.action-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 12px 16px; border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); position: sticky; bottom: 0;
}
.action-summary { font-size: 13px; color: var(--c-secondary); }
.action-summary b { color: var(--c-fg); }

.loading-state { padding: 40px; text-align: center; color: var(--c-secondary); }
.empty-state { text-align: center; padding: 48px 20px; color: var(--c-secondary); }
.empty-state .empty-icon { margin-bottom: 12px; color: var(--c-border); }
.empty-state .empty-title { font-size: 15px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.empty-state .empty-desc { font-size: 13px; }

.spinner.sm { width: 14px; height: 14px; border-width: 2px; }

/* 类别 Tab */
.tabs { display: flex; align-items: center; gap: 2px; border-bottom: 1px solid var(--c-border); }
.tab {
  position: relative; display: inline-flex; align-items: center; gap: 8px;
  padding: 9px 14px; border: 0; background: transparent; cursor: pointer;
  font-size: 13px; font-family: var(--font); color: var(--c-secondary);
  border-bottom: 2px solid transparent; transition: color 150ms, border-color 150ms, background 150ms;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
}
.tab:hover { color: var(--c-fg); background: var(--c-muted); }
.tab.active { color: var(--c-accent); border-bottom-color: var(--c-accent); font-weight: 600; }
.tab-count { font-size: 11px; padding: 1px 7px; border-radius: 9px; background: var(--c-muted); color: var(--c-secondary); }
.tab.active .tab-count { background: var(--c-accent); color: #fff; }
.tab-body { padding-top: 14px; }
.tab-empty { padding: 36px; text-align: center; color: var(--c-secondary); font-size: 13px; }

/* 确认弹窗正文 */
.confirm-body { display: flex; flex-direction: column; gap: 10px; }
.confirm-line { margin: 0; font-size: 13px; color: var(--c-fg); font-weight: 600; }
.confirm-list { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; }
.confirm-list li { font-size: 13px; color: var(--c-secondary); }
.confirm-list li b { color: var(--c-danger); font-weight: 700; }
.confirm-warn { margin: 0; padding-top: 4px; font-size: 12px; color: var(--c-secondary); border-top: 1px dashed var(--c-border); }
</style>
