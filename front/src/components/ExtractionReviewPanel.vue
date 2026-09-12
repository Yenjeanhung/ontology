<script setup>
import { computed, ref, watch } from 'vue'
import {
  approveExtractionReview,
  batchExtractionReviews,
  fetchExtractionReviewStats,
  fetchExtractionReviews,
  rejectExtractionReview,
} from '../api'
import { auth } from '../stores/auth'

const props = defineProps({
  kbId: { type: String, required: true },
})
const emit = defineEmits(['changed'])

const RULE_LABELS = {
  enum: '枚举不匹配',
  pattern: '正则不匹配',
  range: '超出范围',
  length: '长度不符',
  confidence: '属性置信度不足',
  required: '缺少必填属性',
  name_pattern: '实体名不合规',
  min_attributes: '有效属性不足',
  min_confidence: '实体置信度不足',
  type: '类型解析失败',
}

const STATUS_LABELS = {
  pending: '待审核',
  approved: '已通过',
  rejected: '已驳回',
  expired: '已过期',
}

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const statusFilter = ref('pending')
const ruleFilter = ref('')
const loading = ref(false)
const error = ref('')
const message = ref('')
const stats = ref({ by_status: {}, pending_by_rule: {}, total: 0 })
const checked = ref(new Set())
const busy = ref(false)
const editingId = ref('')
const editName = ref('')
const editProps = ref('')

const reviewer = computed(() => auth.user?.username || auth.user?.name || '')
const pendingCount = computed(() => stats.value.by_status?.pending || 0)
const ruleOptions = computed(() =>
  Object.entries(stats.value.pending_by_rule || {}).map(([value, count]) => ({
    value,
    label: `${RULE_LABELS[value] || value} (${count})`,
  }))
)
const isAllChecked = computed(
  () => items.value.length > 0 && items.value.every(i => checked.value.has(i.id))
)

async function loadStats() {
  if (!props.kbId) return
  try {
    stats.value = await fetchExtractionReviewStats(props.kbId)
  } catch {
    /* 统计失败不影响列表展示 */
  }
}

async function load() {
  if (!props.kbId) return
  loading.value = true
  error.value = ''
  try {
    const res = await fetchExtractionReviews({
      kb_id: props.kbId,
      status: statusFilter.value,
      rule: ruleFilter.value,
      page: page.value,
      page_size: pageSize.value,
    })
    items.value = res.items || []
    total.value = res.total || 0
    checked.value = new Set()
  } catch (e) {
    error.value = '加载失败：' + e.message
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.kbId, statusFilter.value, ruleFilter.value, page.value],
  () => {
    load()
    loadStats()
  },
  { immediate: true }
)

function toggleCheck(id) {
  const next = new Set(checked.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  checked.value = next
}

function toggleAll() {
  checked.value = isAllChecked.value
    ? new Set()
    : new Set(items.value.map(i => i.id))
}

function startEdit(item) {
  editingId.value = item.id
  editName.value = item.entity_name || ''
  editProps.value = JSON.stringify(item.properties || {}, null, 2)
}

function cancelEdit() {
  editingId.value = ''
  editName.value = ''
  editProps.value = ''
}

function parseProps() {
  const raw = (editProps.value || '').trim()
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      error.value = '属性必须是 JSON 对象'
      return null
    }
    return parsed
  } catch (e) {
    error.value = '属性 JSON 格式有误：' + e.message
    return null
  }
}

async function doApprove(item) {
  error.value = ''
  message.value = ''
  let payload = { reviewer: reviewer.value }
  if (editingId.value === item.id) {
    const parsed = parseProps()
    if (parsed === null) return
    payload.name = editName.value.trim() || undefined
    payload.properties = parsed
  }
  busy.value = true
  try {
    await approveExtractionReview(item.id, payload)
    message.value = `「${item.entity_name}」已通过并入库`
    cancelEdit()
    await load()
    await loadStats()
    emit('changed')
  } catch (e) {
    error.value = '通过失败：' + e.message
  } finally {
    busy.value = false
  }
}

async function doReject(item) {
  error.value = ''
  message.value = ''
  busy.value = true
  try {
    await rejectExtractionReview(item.id, { reviewer: reviewer.value })
    message.value = `「${item.entity_name}」已驳回`
    cancelEdit()
    await load()
    await loadStats()
  } catch (e) {
    error.value = '驳回失败：' + e.message
  } finally {
    busy.value = false
  }
}

async function doBatch(action) {
  const ids = [...checked.value]
  if (!ids.length) return
  error.value = ''
  message.value = ''
  busy.value = true
  try {
    const res = await batchExtractionReviews(ids, action, reviewer.value)
    message.value = `批量${action === 'approve' ? '通过' : '驳回'}：成功 ${res.succeeded} 条，失败 ${res.failed} 条`
    if (res.failed) {
      const first = (res.results || []).find(r => !r.ok)
      if (first) error.value = `部分失败，例：${first.error}`
    }
    checked.value = new Set()
    await load()
    await loadStats()
    emit('changed')
  } catch (e) {
    error.value = '批量操作失败：' + e.message
  } finally {
    busy.value = false
  }
}

function ruleLabel(rule) {
  return RULE_LABELS[rule] || rule || '未知'
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status
}

function confText(conf) {
  return typeof conf === 'number' ? conf.toFixed(2) : '-'
}
</script>

<template>
  <div class="erp-root">
    <div class="erp-bar">
      <div class="erp-stats">
        <span class="erp-badge pending">待审核 {{ pendingCount }}</span>
        <span class="erp-badge">已通过 {{ stats.by_status?.approved || 0 }}</span>
        <span class="erp-badge">已驳回 {{ stats.by_status?.rejected || 0 }}</span>
      </div>
      <div class="erp-filters">
        <select v-model="statusFilter">
          <option value="pending">待审核</option>
          <option value="approved">已通过</option>
          <option value="rejected">已驳回</option>
          <option value="expired">已过期</option>
          <option value="">全部状态</option>
        </select>
        <select v-model="ruleFilter">
          <option value="">全部规则</option>
          <option v-for="r in ruleOptions" :key="r.value" :value="r.value">{{ r.label }}</option>
        </select>
        <span class="erp-spacer"></span>
        <button class="btn sm" :disabled="!checked.size || busy" @click="doBatch('approve')">
          批量通过（{{ checked.size }}）
        </button>
        <button class="btn sm" :disabled="!checked.size || busy" @click="doBatch('reject')">
          批量驳回（{{ checked.size }}）
        </button>
      </div>
    </div>

    <div v-if="error" class="erp-msg err">{{ error }}</div>
    <div v-if="message" class="erp-msg ok">{{ message }}</div>

    <div v-if="loading" class="erp-empty">加载中…</div>
    <div v-else-if="!items.length" class="erp-empty">
      {{ statusFilter === 'pending' ? '没有待复核的实体' : '该状态下没有记录' }}
    </div>

    <div v-else class="erp-list">
      <div v-if="statusFilter === 'pending'" class="erp-check-all">
        <label><input type="checkbox" :checked="isAllChecked" @change="toggleAll"> 全选本页</label>
      </div>

      <div v-for="item in items" :key="item.id" class="erp-card">
        <div class="erp-card-head">
          <input
            v-if="statusFilter === 'pending'"
            type="checkbox"
            :checked="checked.has(item.id)"
            @change="toggleCheck(item.id)"
          >
          <span class="erp-type">{{ item.entity_type }}</span>
          <span class="erp-name">{{ item.entity_name }}</span>
          <span class="erp-conf" :title="'证据计算的实体置信度'">置信度 {{ confText(item.confidence) }}</span>
          <span class="erp-spacer"></span>
          <span class="erp-rule">{{ ruleLabel(item.primary_rule) }}</span>
          <span class="erp-status" :class="item.status">{{ statusLabel(item.status) }}</span>
        </div>

        <div class="erp-violations">
          <div v-for="(v, i) in item.violations" :key="i" class="erp-violation">
            <span class="erp-v-rule">{{ ruleLabel(v.rule) }}</span>
            <span class="erp-v-text">{{ v.reason }}</span>
          </div>
        </div>

        <div class="erp-props">
          <span class="erp-props-label">当前属性</span>
          <code>{{ JSON.stringify(item.properties || {}) }}</code>
        </div>
        <div v-if="item.raw_properties && Object.keys(item.raw_properties).length" class="erp-props raw">
          <span class="erp-props-label">原始抽取</span>
          <code>{{ JSON.stringify(item.raw_properties) }}</code>
        </div>
        <div v-if="item.review_notes" class="erp-notes">备注：{{ item.review_notes }}</div>

        <div v-if="editingId === item.id" class="erp-edit">
          <div class="erp-field">
            <label>实体名称</label>
            <input type="text" v-model="editName">
          </div>
          <div class="erp-field">
            <label>属性（JSON 对象，可补全缺失的必填属性）</label>
            <textarea v-model="editProps" rows="5"></textarea>
          </div>
        </div>

        <div v-if="item.status === 'pending'" class="erp-actions">
          <button class="btn sm" @click="editingId === item.id ? cancelEdit() : startEdit(item)">
            {{ editingId === item.id ? '取消编辑' : '编辑并入库' }}
          </button>
          <button class="btn primary sm" :disabled="busy" @click="doApprove(item)">
            {{ editingId === item.id ? '保存并通过' : '通过' }}
          </button>
          <button class="btn sm danger" :disabled="busy" @click="doReject(item)">驳回</button>
        </div>
      </div>
    </div>

    <div v-if="total > pageSize" class="erp-pager">
      <button class="btn sm" :disabled="page <= 1" @click="page -= 1">上一页</button>
      <span>第 {{ page }} / {{ Math.ceil(total / pageSize) }} 页（共 {{ total }} 条）</span>
      <button class="btn sm" :disabled="page >= Math.ceil(total / pageSize)" @click="page += 1">下一页</button>
    </div>
  </div>
</template>

<style scoped>
.erp-root { display: flex; flex-direction: column; gap: 10px; }
.erp-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.erp-stats { display: flex; gap: 6px; }
.erp-badge {
  font-size: 11.5px; padding: 2px 9px; border-radius: 10px;
  background: var(--c-muted); color: var(--c-secondary);
}
.erp-badge.pending { background: rgba(220, 38, 38, 0.12); color: var(--c-danger); }
.erp-filters { display: flex; align-items: center; gap: 8px; margin-left: auto; flex-wrap: wrap; }
.erp-filters select { padding: 4px 8px; font-size: 12px; }
.erp-spacer { flex: 1; }
.erp-msg { font-size: 12px; padding: 6px 10px; border-radius: var(--radius-sm); }
.erp-msg.err { color: var(--c-danger); background: rgba(220, 38, 38, 0.08); }
.erp-msg.ok { color: var(--c-success); background: rgba(22, 163, 74, 0.08); }
.erp-empty { padding: 26px; text-align: center; color: var(--c-secondary); font-size: 13px; }

.erp-list { display: flex; flex-direction: column; gap: 8px; }
.erp-check-all { font-size: 12px; color: var(--c-secondary); }
.erp-card {
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); padding: 10px 12px;
  display: flex; flex-direction: column; gap: 7px;
}
.erp-card-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.erp-type { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); }
.erp-name { font-size: 13px; font-weight: 600; color: var(--c-fg); }
.erp-conf { font-size: 11px; color: var(--c-secondary); }
.erp-rule { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: rgba(180, 83, 9, 0.12); color: #B45309; }
.erp-status { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); }
.erp-status.pending { background: rgba(220, 38, 38, 0.12); color: var(--c-danger); }
.erp-status.approved { background: rgba(22, 163, 74, 0.12); color: var(--c-success); }

.erp-violations { display: flex; flex-direction: column; gap: 3px; }
.erp-violation { display: flex; gap: 7px; font-size: 12px; align-items: baseline; }
.erp-v-rule { color: var(--c-secondary); flex-shrink: 0; font-size: 11px; }
.erp-v-text { color: var(--c-fg); }

.erp-props { display: flex; gap: 7px; font-size: 11.5px; align-items: baseline; }
.erp-props code {
  background: var(--c-muted); padding: 2px 7px; border-radius: 4px;
  word-break: break-all; color: var(--c-secondary);
}
.erp-props.raw code { color: var(--c-secondary); opacity: 0.8; }
.erp-props-label { color: var(--c-secondary); flex-shrink: 0; }
.erp-notes { font-size: 11.5px; color: var(--c-secondary); font-style: italic; }

.erp-edit {
  border-top: 1px dashed var(--c-border); padding-top: 8px;
  display: flex; flex-direction: column; gap: 8px;
}
.erp-field { display: flex; flex-direction: column; gap: 4px; }
.erp-field label { font-size: 11.5px; color: var(--c-secondary); }
.erp-field input, .erp-field textarea {
  font-size: 12px; padding: 5px 8px; font-family: inherit;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-bg); color: var(--c-fg);
}
.erp-field textarea { font-family: ui-monospace, monospace; }

.erp-actions { display: flex; gap: 7px; }
.btn.sm { padding: 4px 10px; font-size: 12px; }
.btn.danger { color: var(--c-danger); }

.erp-pager { display: flex; align-items: center; gap: 10px; justify-content: center; font-size: 12px; color: var(--c-secondary); }
</style>
