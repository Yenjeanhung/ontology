<script setup>
import { onMounted, ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  getSchedule, createSchedule, updateSchedule, fetchWorkflows, previewNextRun,
} from '../../api'
import { useToast } from '../../composables/useToast'
import CronBuilder from './CronBuilder.vue'

const router = useRouter()
const route = useRoute()
const toast = useToast()

const scheduleId = computed(() => route.params.scheduleId)
const isNew = computed(() => scheduleId.value === 'new' || !scheduleId.value)

const workflows = ref([])
const selectedWf = ref(null)
const startInputs = ref([]) // 绑定工作流的 start 节点入参声明

const form = ref({
  name: '',
  description: '',
  workflow_id: '',
  trigger: 'cron',
  trigger_config: { minute: '0', hour: '8', day: '*', month: '*', day_of_week: '*' },
  input_params: {},
  enabled: true,
  muted: false,
  max_failures_alert: 3,
  alert_on_failure: true,
})

const cronCfg = ref({ ...form.value.trigger_config })
const intervalCfg = ref({ every: 30, unit: 'minutes' })
const onceCfg = ref({ run_at: '' })
const saving = ref(false)
const previewNext = ref(null)
const previewErr = ref('')
const loading = ref(false)

onMounted(async () => {
  try { workflows.value = await fetchWorkflows() } catch { toast.error('加载工作流列表失败') }
  if (!isNew.value) {
    loading.value = true
    try {
      const s = await getSchedule(scheduleId.value)
      form.value = {
        name: s.name,
        description: s.description,
        workflow_id: s.workflow_id,
        trigger: s.trigger,
        trigger_config: s.trigger_config,
        input_params: s.input_params || {},
        enabled: s.enabled,
        muted: s.muted,
        max_failures_alert: s.max_failures_alert,
        alert_on_failure: s.alert_on_failure,
      }
      cronCfg.value = { ...form.value.trigger_config }
      intervalCfg.value = { every: form.value.trigger_config.every || 30, unit: form.value.trigger_config.unit || 'minutes' }
      onceCfg.value = { run_at: form.value.trigger_config.run_at || '' }
      await loadWfInputs(s.workflow_id)
    } catch (e) { toast.error(`加载计划失败: ${e.message}`) }
    loading.value = false
  }
})

async function loadWfInputs(wfId) {
  const wf = workflows.value.find(w => w.id === wfId)
  if (!wf) return
  selectedWf.value = wf
  // 从工作流 definition 取 start 节点 inputs
  try {
    const def = typeof wf.definition === 'string' ? JSON.parse(wf.definition) : (wf.definition || {})
    const nodes = def.nodes || []
    const start = nodes.find(n => n.type === 'start')
    startInputs.value = (start?.data?.config?.inputs) || []
    // 为未出现在 input_params 的字段补默认值
    for (const it of startInputs.value) {
      if (!(it.name in form.value.input_params)) {
        const d = it.default
        form.value.input_params[it.name] = d != null ? d : (it.type === 'boolean' ? false : '')
      }
    }
  } catch {
    startInputs.value = []
  }
}

async function onWorkflowChange() {
  await loadWfInputs(form.value.workflow_id)
}

function buildTriggerConfig() {
  if (form.value.trigger === 'cron') return { ...cronCfg.value }
  if (form.value.trigger === 'interval') return { ...intervalCfg.value }
  if (form.value.trigger === 'once') return { ...onceCfg.value }
  return {}
}

async function refreshPreview() {
  previewErr.value = ''
  try {
    const r = await previewNextRun(form.value.trigger, buildTriggerConfig())
    previewNext.value = r.next_run_at
  } catch (e) { previewErr.value = e.message; previewNext.value = null }
}

async function save() {
  if (!form.value.name.trim()) { toast.error('名称不能为空'); return }
  if (!form.value.workflow_id) { toast.error('请选择关联工作流'); return }
  saving.value = true
  const payload = {
    name: form.value.name.trim(),
    description: form.value.description,
    workflow_id: form.value.workflow_id,
    trigger: form.value.trigger,
    trigger_config: buildTriggerConfig(),
    input_params: form.value.input_params,
    enabled: form.value.enabled,
    muted: form.value.muted,
    max_failures_alert: form.value.max_failures_alert,
    alert_on_failure: form.value.alert_on_failure,
  }
  try {
    if (isNew.value) {
      await createSchedule(payload)
      toast.success('计划已创建')
    } else {
      await updateSchedule(scheduleId.value, payload)
      toast.success('计划已更新')
    }
    router.push('/schedules')
  } catch (e) {
    toast.error(`保存失败: ${e.message}`)
  }
  saving.value = false
}

function cancel() { router.push('/schedules') }

function fmtTime(t) {
  if (!t) return '—'
  return String(t).replace('T', ' ').slice(0, 19)
}
</script>

<template>
  <div class="ed-page" v-if="!loading">
    <div class="page-head">
      <div>
        <button class="back" @click="cancel">← 返回</button>
        <h3>{{ isNew ? '新建定时计划' : '编辑定时计划' }}</h3>
        <p class="desc">选择一个工作流并配置触发规则，系统会按时自动执行该工作流。</p>
      </div>
    </div>

    <div class="card">
      <div class="card-title">基本信息</div>
      <div class="form">
        <div class="field">
          <label>计划名称 <span class="req">*</span></label>
          <input type="text" v-model="form.name" placeholder="如：每日财经早报">
        </div>
        <div class="field">
          <label>描述</label>
          <textarea rows="2" v-model="form.description" placeholder="可选"></textarea>
        </div>
        <div class="field">
          <label>关联工作流 <span class="req">*</span></label>
          <select v-model="form.workflow_id" @change="onWorkflowChange">
            <option value="" disabled>请选择工作流</option>
            <option v-for="w in workflows" :key="w.id" :value="w.id">{{ w.name }}</option>
          </select>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">触发器</div>
      <div class="trig-tabs">
        <button :class="['trig-tab', { active: form.trigger === 'cron' }]" @click="form.trigger = 'cron'; refreshPreview()">Cron（定时）</button>
        <button :class="['trig-tab', { active: form.trigger === 'interval' }]" @click="form.trigger = 'interval'; refreshPreview()">周期（Interval）</button>
        <button :class="['trig-tab', { active: form.trigger === 'once' }]" @click="form.trigger = 'once'; refreshPreview()">一次性（Once）</button>
      </div>

      <div v-if="form.trigger === 'cron'" class="trig-body">
        <CronBuilder v-model="cronCfg" @preview="refreshPreview" />
      </div>

      <div v-else-if="form.trigger === 'interval'" class="trig-body inline">
        <span>每</span>
        <input type="number" min="1" v-model.number="intervalCfg.every" @change="refreshPreview">
        <select v-model="intervalCfg.unit" @change="refreshPreview">
          <option value="minutes">分钟</option>
          <option value="hours">小时</option>
          <option value="days">天</option>
        </select>
      </div>

      <div v-else class="trig-body inline">
        <span>执行时间</span>
        <input type="datetime-local" v-model="onceCfg.run_at" @change="refreshPreview">
        <span class="hint">（时区：Asia/Shanghai，须晚于当前时间）</span>
      </div>

      <div class="preview">
        下次运行：
        <code v-if="previewNext">{{ fmtTime(previewNext) }}</code>
        <span v-else class="preview-err">{{ previewErr || '—' }}</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">固定入参</div>
      <div class="form" v-if="startInputs.length">
        <div class="field" v-for="it in startInputs" :key="it.name">
          <label>
            {{ it.label || it.name }}
            <span class="req" v-if="it.required">*</span>
            <span class="type-tag">{{ it.type }}</span>
          </label>
          <input v-if="it.type === 'number' || it.type === 'integer'" type="number" v-model.number="form.input_params[it.name]">
          <input v-else-if="it.type === 'boolean'" type="checkbox" v-model="form.input_params[it.name]" class="cb">
          <input v-else type="text" v-model="form.input_params[it.name]" :placeholder="it.default != null ? String(it.default) : ''">
        </div>
        <div class="hint">入参需与所选工作流 start 节点声明一致，保存时强校验。</div>
      </div>
      <div class="hint" v-else>请先选择关联工作流以加载其入参。</div>
    </div>

    <div class="card">
      <div class="card-title">告警</div>
      <div class="form">
        <label class="switch-row">
          <input type="checkbox" v-model="form.alert_on_failure">
          <span>失败时发送通知（右上角消息中心提示）</span>
        </label>
        <div class="field" v-if="form.alert_on_failure">
          <label>连续失败达到以下次数告警</label>
          <input type="number" min="1" v-model.number="form.max_failures_alert" style="max-width:120px">
        </div>
        <label class="switch-row">
          <input type="checkbox" v-model="form.muted">
          <span>静默（仍执行，但不告警）</span>
        </label>
      </div>
    </div>

    <div class="actions">
      <button class="btn" @click="cancel">取消</button>
      <button class="btn primary" :disabled="saving" @click="save">{{ saving ? '保存中...' : '保存计划' }}</button>
    </div>
  </div>
  <div v-else class="ed-loading">加载中...</div>
</template>

<style scoped>
.ed-page { display: flex; flex-direction: column; gap: 16px; max-width: 880px; }
.page-head .back { border: 0; background: transparent; color: var(--c-secondary); font-size: 13px; cursor: pointer; padding: 0; margin-bottom: 6px; }
.page-head .back:hover { color: var(--c-accent); }
.page-head h3 { font-size: 18px; font-weight: 700; color: var(--c-fg); margin: 0 0 4px; }
.desc { font-size: 13px; color: var(--c-secondary); margin: 0; }

.card { border: 1px solid var(--c-border); border-radius: 12px; padding: 16px; background: var(--c-panel); }
.card-title { font-size: 14px; font-weight: 700; color: var(--c-fg); margin-bottom: 12px; }
.form { display: flex; flex-direction: column; gap: 12px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); display: flex; align-items: center; gap: 6px; }
.field input, .field textarea, .field select {
  padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px);
  font-size: 13px; font-family: var(--font); outline: none; background: var(--c-bg); color: var(--c-fg);
}
.field input:focus, .field textarea:focus, .field select:focus { border-color: var(--c-accent); }
.field textarea { resize: vertical; }
.type-tag { font-size: 10px; color: var(--c-secondary); background: var(--c-muted); padding: 1px 6px; border-radius: 4px; }
.cb { width: 16px; height: 16px; }
.req { color: #e54545; }
.hint { font-size: 12px; color: var(--c-secondary); }

.trig-tabs { display: inline-flex; gap: 4px; background: var(--c-muted); padding: 3px; border-radius: 8px; margin-bottom: 12px; }
.trig-tab { border: 0; background: transparent; color: var(--c-secondary); font-size: 13px; padding: 6px 14px; border-radius: 6px; cursor: pointer; }
.trig-tab.active { background: var(--c-panel); color: var(--c-accent); font-weight: 600; }

.trig-body { margin-top: 4px; }
.trig-body.inline { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--c-fg); }
.trig-body.inline input[type=number] { width: 90px; }
.trig-body.inline .hint { color: var(--c-secondary); }

.preview { margin-top: 12px; font-size: 13px; color: var(--c-fg); display: flex; align-items: center; gap: 8px; }
.preview code { font-family: ui-monospace, monospace; font-size: 13px; color: var(--c-accent); background: var(--c-muted); padding: 2px 8px; border-radius: 6px; }
.preview-err { color: #e54545; font-size: 12px; }

.switch-row { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--c-fg); cursor: pointer; }
.switch-row input { width: 16px; height: 16px; }

.actions { display: flex; justify-content: flex-end; gap: 10px; padding-top: 4px; }
.ed-loading { padding: 60px; text-align: center; color: var(--c-secondary); }
</style>
