<script setup>
/** 人工任务处理表单：编辑器处理卡与待办中心共用。 */
import { ref, reactive, computed, watch } from 'vue'

const props = defineProps({
  mode: { type: String, default: 'approve' },        // approve | form
  description: { type: String, default: '' },
  formData: { type: Object, default: () => ({}) },   // 只读待审内容（已渲染）
  formFields: { type: Array, default: () => [] },    // 可填写字段定义
  decisions: { type: Array, default: () => [] },     // 审批选项
  submitText: { type: String, default: '提交' },
  commentLabel: { type: String, default: '处理意见' },
  commentPlaceholder: { type: String, default: '' },
  commentRequired: { type: Boolean, default: false },
  taskId: { type: String, default: '' },
  assignee: { type: String, default: '' },
  dueAt: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  submitting: { type: Boolean, default: false },
  // 待办中心场景：隐藏处理人（由列表批量填写）
  showOperator: { type: Boolean, default: true },
  defaultOperator: { type: String, default: '' },
})

const emit = defineEmits(['submit'])

const OPERATOR_KEY = 'ks_human_operator'

const values = reactive({})
const comment = ref('')
const operator = ref(props.defaultOperator || localStorage.getItem(OPERATOR_KEY) || '')
const errors = reactive({})
const touched = ref(false)

// 每次切换任务时重置表单
watch(() => props.taskId, () => {
  Object.keys(values).forEach(k => delete values[k])
  Object.keys(errors).forEach(k => delete errors[k])
  comment.value = ''
  touched.value = false
  for (const f of props.formFields || []) {
    if (f.default !== undefined && f.default !== null) values[f.key] = f.default
  }
}, { immediate: true })

watch(() => props.formFields, (fields) => {
  for (const f of fields || []) {
    if (values[f.key] === undefined && f.default !== undefined && f.default !== null) {
      values[f.key] = f.default
    }
  }
}, { immediate: true, deep: true })

const overdue = computed(() => {
  if (!props.dueAt) return false
  try { return new Date() > new Date(props.dueAt) } catch { return false }
})

const decisions = computed(() => {
  const d = props.decisions || []
  if (d.length) return d
  return props.mode === 'form' ? [] : [
    { key: 'approved', label: '通过', style: 'primary' },
    { key: 'rejected', label: '驳回', style: 'danger' },
  ]
})

function fmtVal(v) {
  if (v == null) return '—'
  if (typeof v === 'object') {
    try { return JSON.stringify(v, null, 2) } catch { return String(v) }
  }
  return String(v)
}

function validate(decision) {
  const errs = {}
  // 意见必填：全局必填，或命中 required_on 的场景（由后端判定，这里用 commentRequired 兜底）
  const needComment = props.commentRequired
    || (props.mode !== 'form' && decision === 'rejected' && props.commentRequired)
  if (needComment && !comment.value.trim()) errs.__comment = `请填写${props.commentLabel}`
  // 表单字段
  if (props.mode === 'form') {
    for (const f of props.formFields || []) {
      if (!f.required) continue
      const v = values[f.key]
      if (v === undefined || v === null || v === '') errs[f.key] = '必填'
    }
  }
  Object.keys(errors).forEach(k => delete errors[k])
  Object.assign(errors, errs)
  return Object.keys(errs).length === 0
}

function submit(decision) {
  touched.value = true
  if (!validate(decision)) return
  if (props.showOperator && operator.value.trim()) {
    localStorage.setItem(OPERATOR_KEY, operator.value.trim())
  }
  emit('submit', {
    decision,
    comment: comment.value.trim(),
    data: props.mode === 'form' ? { ...values } : {},
    operator: operator.value.trim(),
  })
}
</script>

<template>
  <div class="ht-form" :class="{ 'ht-disabled': disabled || submitting }">
    <!-- 待办元信息 -->
    <div class="ht-meta" v-if="taskId">
      <span class="ht-task">#{{ taskId }}</span>
      <span v-if="assignee" class="ht-assignee">指定：{{ assignee }}</span>
      <span v-if="overdue" class="ht-overdue">已超时</span>
    </div>

    <p class="ht-desc" v-if="description">{{ description }}</p>

    <!-- 只读待审内容 -->
    <div class="ht-fields" v-if="Object.keys(formData || {}).length">
      <div v-for="(v, k) in formData" :key="k" class="ht-row">
        <span class="ht-k">{{ k }}</span>
        <pre class="ht-v">{{ fmtVal(v) }}</pre>
      </div>
    </div>

    <!-- 表单模式：动态字段 -->
    <template v-if="mode === 'form'">
      <div class="ht-sep">填写内容</div>
      <div v-for="f in formFields" :key="f.key" class="ht-input-row">
        <label class="ht-label">
          {{ f.label || f.key }}
          <i v-if="f.required" class="ht-req">*</i>
        </label>

        <input v-if="f.type === 'text' || !f.type" type="text" v-model="values[f.key]"
               :placeholder="f.placeholder || ''" :disabled="disabled || submitting" />
        <input v-else-if="f.type === 'number'" type="number" v-model.number="values[f.key]"
               :placeholder="f.placeholder || ''" :disabled="disabled || submitting" />
        <input v-else-if="f.type === 'date'" type="date" v-model="values[f.key]" :disabled="disabled || submitting" />
        <textarea v-else-if="f.type === 'textarea'" v-model="values[f.key]" rows="3"
                  :placeholder="f.placeholder || ''" :disabled="disabled || submitting"></textarea>
        <select v-else-if="f.type === 'select'" v-model="values[f.key]" :disabled="disabled || submitting">
          <option value="">请选择…</option>
          <option v-for="o in (f.options || [])" :key="o" :value="o">{{ o }}</option>
        </select>
        <label v-else-if="f.type === 'boolean'" class="ht-switch">
          <input type="checkbox" v-model="values[f.key]" :disabled="disabled || submitting" />
          <span>{{ values[f.key] ? '是' : '否' }}</span>
        </label>

        <span v-if="touched && errors[f.key]" class="ht-err">{{ errors[f.key] }}</span>
      </div>
    </template>

    <!-- 意见 -->
    <div class="ht-input-row">
      <label class="ht-label">{{ commentLabel }}<i v-if="commentRequired" class="ht-req">*</i></label>
      <textarea v-model="comment" rows="2" :placeholder="commentPlaceholder || '选填，便于后续追溯'"
                :disabled="disabled || submitting"></textarea>
      <span v-if="touched && errors.__comment" class="ht-err">{{ errors.__comment }}</span>
    </div>

    <!-- 处理人 -->
    <div class="ht-input-row" v-if="showOperator">
      <label class="ht-label">处理人</label>
      <input type="text" v-model="operator" placeholder="可留空（自动记住上次填写）"
             :disabled="disabled || submitting" />
    </div>

    <!-- 动作按钮 -->
    <div class="ht-actions">
      <template v-if="mode === 'form'">
        <button type="button" class="ht-btn primary" :disabled="disabled || submitting" @click="submit('submitted')">
          {{ submitting ? '提交中…' : submitText }}
        </button>
      </template>
      <template v-else>
        <button v-for="d in decisions" :key="d.key" type="button"
                class="ht-btn" :class="d.style || 'primary'"
                :disabled="disabled || submitting" @click="submit(d.key)">
          {{ d.label }}
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.ht-form { display: flex; flex-direction: column; gap: 10px; }
.ht-form.ht-disabled { opacity: .7; pointer-events: none; }

.ht-meta { display: flex; align-items: center; gap: 8px; font-size: 10.5px; color: var(--c-secondary); }
.ht-task { font-family: ui-monospace, monospace; }
.ht-overdue { color: var(--c-danger); font-weight: 700; }

.ht-desc {
  margin: 0; padding: 7px 9px; border-radius: 6px; font-size: 11.5px; line-height: 1.6;
  color: var(--c-fg); background: color-mix(in srgb, var(--c-accent) 8%, transparent);
  border-left: 3px solid var(--c-accent);
}

.ht-fields { display: flex; flex-direction: column; gap: 6px; }
.ht-row { display: grid; grid-template-columns: 88px 1fr; gap: 8px; align-items: start; }
.ht-k { font-size: 11px; font-weight: 700; color: var(--c-accent); line-height: 1.5; word-break: break-all; }
.ht-v {
  margin: 0; padding: 5px 7px; border-radius: 5px; font-size: 11px; line-height: 1.5;
  font-family: ui-monospace, monospace; white-space: pre-wrap; word-break: break-word;
  background: var(--c-bg-soft, rgba(255,255,255,.04)); color: var(--c-fg);
  max-height: 160px; overflow-y: auto;
}

.ht-sep {
  font-size: 10.5px; font-weight: 700; color: var(--c-secondary);
  padding-bottom: 4px; border-bottom: 1px solid var(--c-border);
}

.ht-input-row { display: flex; flex-direction: column; gap: 4px; }
.ht-label { font-size: 11px; font-weight: 600; color: var(--c-fg); }
.ht-req { color: var(--c-danger); font-style: normal; margin-left: 3px; }
.ht-switch { display: flex; align-items: center; gap: 6px; font-size: 11px; }

.ht-input-row input[type="text"],
.ht-input-row input[type="number"],
.ht-input-row input[type="date"],
.ht-input-row select,
.ht-input-row textarea {
  width: 100%; padding: 5px 8px; border-radius: 5px;
  border: 1px solid var(--c-border-strong, #d8cdbb);
  background: var(--c-panel); color: var(--c-fg);
  font-size: 11.5px; font-family: inherit; line-height: 1.5;
}
.ht-input-row textarea { resize: vertical; }
.ht-input-row input:focus, .ht-input-row select:focus, .ht-input-row textarea:focus {
  outline: none; border-color: var(--c-accent);
}

.ht-err { font-size: 10.5px; color: var(--c-danger); }

.ht-actions { display: flex; gap: 8px; margin-top: 2px; }
.ht-btn {
  flex: 1; padding: 6px 10px; border-radius: 6px; cursor: pointer;
  font-size: 12px; font-weight: 600; border: 1px solid transparent;
}
.ht-btn.primary { background: var(--c-success); color: #fff; }
.ht-btn.primary:hover { filter: brightness(1.06); }
.ht-btn.danger { background: var(--c-danger); color: #fff; }
.ht-btn.danger:hover { filter: brightness(1.06); }
.ht-btn:disabled { opacity: .6; cursor: not-allowed; }
</style>
