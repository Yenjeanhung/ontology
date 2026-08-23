<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) }, // { minute, hour, day, month, day_of_week }
})
const emit = defineEmits(['update:modelValue', 'preview'])

const FIELDS = [
  { key: 'minute', title: '分钟', hint: '0-59', placeholders: ['0', '*/15', '0,30'] },
  { key: 'hour', title: '小时', hint: '0-23', placeholders: ['*', '8', '9-18'] },
  { key: 'day', title: '日', hint: '1-31', placeholders: ['*', '1', '1,15'] },
  { key: 'month', title: '月', hint: '1-12', placeholders: ['*', '1', '1-6'] },
  { key: 'day_of_week', title: '星期', hint: '0-6 (0=周日)', placeholders: ['*', '1-5', '1'] },
]

const mode = ref('builder')
const local = ref({ ...defaults(), ...props.modelValue })

function defaults() {
  return { minute: '0', hour: '8', day: '*', month: '*', day_of_week: '*' }
}

watch(() => props.modelValue, (v) => { local.value = { ...defaults(), ...v } }, { deep: true })

const templates = [
  { label: '每天 08:00', cfg: { minute: '0', hour: '8', day: '*', month: '*', day_of_week: '*' } },
  { label: '每小时整点', cfg: { minute: '0', hour: '*', day: '*', month: '*', day_of_week: '*' } },
  { label: '工作日 09:00', cfg: { minute: '0', hour: '9', day: '*', month: '*', day_of_week: '1-5' } },
  { label: '每周一 09:00', cfg: { minute: '0', hour: '9', day: '*', month: '*', day_of_week: '1' } },
  { label: '每月 1 号 09:00', cfg: { minute: '0', hour: '9', day: '1', month: '*', day_of_week: '*' } },
]

function applyTemplate(cfg) {
  local.value = { ...cfg }
  emitChange()
}

function onFieldInput() {
  emitChange()
}

function emitChange() {
  emit('update:modelValue', { ...local.value })
  emit('preview')
}

const exprText = ref(toExpr(local.value))
const exprError = ref('')
watch(() => ({ ...local.value }), (v) => { if (mode.value === 'builder') exprText.value = toExpr(v) }, { deep: true })

function toExpr(c) {
  const f = (k, d) => (c[k] == null || String(c[k]).trim() === '' ? d : c[k])
  return `${f('minute', '*')} ${f('hour', '*')} ${f('day', '*')} ${f('month', '*')} ${f('day_of_week', '*')}`
}

async function validateAdvanced() {
  const parts = exprText.value.trim().split(/\s+/)
  if (parts.length !== 5) { exprError.value = '需要 5 个字段：分 时 日 月 周'; return false }
  const fake = { minute: parts[0], hour: parts[1], day: parts[2], month: parts[3], day_of_week: parts[4] }
  try {
    const { validateCron } = await import('../../api')
    const r = await validateCron(fake)
    if (!r.valid) { exprError.value = r.error; return false }
  } catch (e) {
    exprError.value = e.message || '校验失败'
    return false
  }
  exprError.value = ''
  local.value = fake
  emit('update:modelValue', { ...fake })
  emit('preview')
  return true
}

const displayExpr = computed(() => (mode.value === 'advanced' ? exprText.value : toExpr(local.value)))

defineExpose({ validateAdvanced, displayExpr })
</script>

<template>
  <div class="cron-builder">
    <div class="cb-head">
      <div class="cb-tabs">
        <button :class="['cb-tab', { active: mode === 'builder' }]" @click="mode = 'builder'">可视化构建</button>
        <button :class="['cb-tab', { active: mode === 'advanced' }]" @click="mode = 'advanced'">高级表达式</button>
      </div>
    </div>

    <!-- 可视化构建 -->
    <div v-if="mode === 'builder'" class="cb-builder">
      <div class="cb-templates">
        <span class="cb-tpl-label">快捷：</span>
        <button v-for="t in templates" :key="t.label" class="cb-tpl" @click="applyTemplate(t.cfg)">{{ t.label }}</button>
      </div>

      <div class="cb-grid">
        <div v-for="f in FIELDS" :key="f.key" class="cb-field">
          <div class="cb-field-name">{{ f.title }} <span class="cb-hint">{{ f.hint }}</span></div>
          <input
            type="text"
            class="cb-input"
            v-model="local[f.key]"
            :placeholder="f.placeholders.join(' / ')"
            @input="onFieldInput"
          >
        </div>
      </div>
    </div>

    <!-- 高级表达式 -->
    <div v-else class="cb-advanced">
      <input type="text" class="cb-expr" v-model="exprText" placeholder="0 8 * * 1-5" @keyup.enter="validateAdvanced">
      <button class="btn sm" @click="validateAdvanced">校验</button>
      <div class="cb-expr-err" v-if="exprError">{{ exprError }}</div>
      <div class="cb-expr-hint" v-else>字段顺序：分钟 小时 日 月 星期（支持 */n、1-5、1,3,5）</div>
    </div>

    <div class="cb-foot">
      <span class="cb-expr-label">标准表达式</span>
      <code class="cb-expr-code">{{ displayExpr }}</code>
    </div>
  </div>
</template>

<style scoped>
.cron-builder { border: 1px solid var(--c-border); border-radius: 12px; padding: 12px; background: var(--c-bg); }
.cb-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.cb-tabs { display: inline-flex; gap: 4px; background: var(--c-muted); padding: 3px; border-radius: 8px; }
.cb-tab { border: 0; background: transparent; color: var(--c-secondary); font-size: 12px; padding: 5px 12px; border-radius: 6px; cursor: pointer; }
.cb-tab.active { background: var(--c-panel); color: var(--c-accent); font-weight: 600; }

.cb-templates { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 12px; }
.cb-tpl-label { font-size: 12px; color: var(--c-secondary); }
.cb-tpl { border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-fg); font-size: 12px; padding: 4px 10px; border-radius: 999px; cursor: pointer; }
.cb-tpl:hover { border-color: var(--c-accent); color: var(--c-accent); }

.cb-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; }
.cb-field { display: flex; flex-direction: column; gap: 5px; }
.cb-field-name { font-size: 12px; font-weight: 600; color: var(--c-fg); }
.cb-hint { font-size: 10px; color: var(--c-secondary); font-weight: 400; }
.cb-input { padding: 7px 10px; border: 1px solid var(--c-border); border-radius: 8px; font-size: 13px; font-family: var(--font); background: var(--c-bg); color: var(--c-fg); outline: none; }
.cb-input:focus { border-color: var(--c-accent); }

.cb-advanced { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.cb-expr { flex: 1; min-width: 220px; padding: 8px 12px; border: 1px solid var(--c-border); border-radius: 8px; font-size: 13px; font-family: var(--font); background: var(--c-bg); color: var(--c-fg); }
.cb-expr-err { width: 100%; color: #e54545; font-size: 12px; }
.cb-expr-hint { width: 100%; color: var(--c-secondary); font-size: 12px; }

.cb-foot { display: flex; align-items: center; gap: 10px; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--c-border); }
.cb-expr-label { font-size: 12px; color: var(--c-secondary); }
.cb-expr-code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; color: var(--c-accent); background: var(--c-muted); padding: 3px 8px; border-radius: 6px; }
</style>
