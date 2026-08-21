<script setup>
import { ref, reactive, watch } from 'vue'
import { invokeEntityService } from '../../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  entityId: { type: String, required: true },
  entityName: { type: String, default: '' },
  /** 要执行的服务（有效服务集项） */
  service: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

const params = reactive({})
const running = ref(false)
const result = ref(null)
const error = ref('')

watch(() => props.modelValue, (v) => {
  if (!v) return
  Object.keys(params).forEach(k => delete params[k])
  result.value = null
  error.value = ''
  for (const p of props.service?.params || []) {
    if (p.default != null && p.default !== '') params[p.name] = p.default
  }
})

async function run() {
  if (!props.service) return
  running.value = true
  result.value = null
  error.value = ''
  try {
    result.value = await invokeEntityService(props.entityId, props.service.id, { params: { ...params } })
  } catch (e) {
    error.value = e.message || '执行失败'
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div v-if="modelValue && service" class="sid-mask" @click.self="emit('update:modelValue', false)">
    <div class="sid-modal">
      <h3>执行动作：{{ service.name }}</h3>
      <div v-if="service.description" class="sid-desc">{{ service.description }}</div>

      <div v-if="service.params?.length" class="sid-form">
        <div v-for="p in service.params" :key="p.name" class="sid-field">
          <label>{{ p.label || p.name }} <i v-if="p.required" class="req">*</i></label>
          <template v-if="p.type === 'boolean'">
            <select v-model="params[p.name]">
              <option :value="true">true</option>
              <option :value="false">false</option>
            </select>
          </template>
          <input v-else :type="p.type === 'number' ? 'number' : 'text'" v-model="params[p.name]"
            :placeholder="p.description || p.default || ''">
        </div>
      </div>
      <div v-else class="sid-hint">该动作无需参数。</div>

      <div class="sid-actions-top">
        <button class="btn primary sm" @click="run" :disabled="running">
          <span v-if="running" class="spinner"></span> 执行
        </button>
      </div>

      <div v-if="error" class="sid-result err">{{ error }}</div>
      <div v-if="result" class="sid-result" :class="{ fail: !result.success }">
        <div class="sid-meta">
          <span class="sid-status" :class="result.success ? 'ok' : 'fail'">
            {{ result.success ? '成功' : '失败' }}
          </span>
          <span>耗时 {{ result.duration_ms }}ms</span>
        </div>
        <div v-if="result.error" class="sid-err">{{ result.error }}</div>
        <template v-if="result.data != null">
          <div class="sid-label">返回数据</div>
          <pre>{{ JSON.stringify(result.data, null, 2) }}</pre>
        </template>
        <template v-if="result.stdout">
          <div class="sid-label">stdout</div>
          <pre>{{ result.stdout }}</pre>
        </template>
      </div>

      <div class="sid-actions">
        <button class="btn" @click="emit('update:modelValue', false)">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sid-mask { position: fixed; inset: 0; background: var(--c-overlay); display: flex; align-items: center; justify-content: center; z-index: 100; padding: 20px; }
.sid-modal { background: var(--c-panel); border-radius: var(--radius); padding: 20px 22px; width: 100%; max-width: 560px; max-height: 86vh; overflow-y: auto; box-shadow: 0 8px 30px rgba(0,0,0,0.18); display: flex; flex-direction: column; gap: 12px; }
.sid-modal h3 { font-size: 15px; font-weight: 700; color: var(--c-fg); }
.sid-desc { font-size: 12px; color: var(--c-secondary); }
.sid-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }
.sid-field { display: flex; flex-direction: column; gap: 4px; }
.sid-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.sid-field .req { color: var(--c-danger); font-style: normal; }
.sid-field input, .sid-field select { width: 100%; box-sizing: border-box; padding: 6px 9px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; }
.sid-field input:focus, .sid-field select:focus { border-color: var(--c-fg); }
.sid-hint { font-size: 12px; color: var(--c-secondary); }
.sid-actions-top { display: flex; justify-content: flex-end; }

.sid-result { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 10px 12px; background: var(--c-muted); font-size: 12px; display: flex; flex-direction: column; gap: 6px; }
.sid-result.err { color: var(--c-danger); }
.sid-result.fail { border-color: var(--c-danger); }
.sid-meta { display: flex; align-items: center; gap: 10px; color: var(--c-secondary); }
.sid-status { font-weight: 800; }
.sid-status.ok { color: var(--c-success, #16A34A); }
.sid-status.fail { color: var(--c-danger); }
.sid-err { color: var(--c-danger); font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; word-break: break-all; }
.sid-label { font-weight: 700; color: var(--c-secondary); }
.sid-result pre { margin: 0; padding: 8px; border-radius: var(--radius-sm); background: var(--c-panel); font-family: ui-monospace, Consolas, monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; max-height: 220px; overflow-y: auto; }
.sid-actions { display: flex; justify-content: flex-end; }
</style>
