<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  fetchLLMPlans, createLLMPlan, updateLLMPlan,
  deleteLLMPlan, applyLLMPlan, testLLMConfig,
} from '../../api'
import Pagination from '../common/Pagination.vue'

// 国内常用模型 + 自定义格式预设（OpenAI 兼容协议）
const OPENAI_PRESETS = [
  { key: 'zhipu', label: '智谱 GLM', provider: 'openai', base_url: 'https://open.bigmodel.cn/api/paas/v4/', model: 'glm-4-plus' },
  { key: 'deepseek', label: 'DeepSeek', provider: 'openai', base_url: 'https://api.deepseek.com', model: 'deepseek-chat' },
  { key: 'qwen', label: '通义千问', provider: 'openai', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { key: 'openai', label: 'OpenAI', provider: 'openai', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
]
const ANTHROPIC_PRESETS = [
  { key: 'claude', label: 'Claude', provider: 'anthropic', base_url: 'https://api.anthropic.com', model: 'claude-3-5-sonnet-latest' },
]

// 视图：list 列表 / form 新建或编辑
const view = ref('list')
const loading = ref(true)
const status = ref(null) // { type: 'success'|'error'|'info', text, preview? }

const plans = ref([])
const editingPlan = ref(null)     // 正在编辑的方案对象；null = 新建
const page = ref(1)
const pageSize = ref(10)
const pagedPlans = computed(() =>
  plans.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value)
)
watch(plans, () => { page.value = 1 }, { deep: true })

// 表单字段
const formName = ref('')
const provider = ref('openai')
const apiKey = ref('')
const baseUrl = ref('')
const model = ref('')
const maxTokens = ref(4096)
const temperature = ref(0.7)
const hasKey = ref(false)
const keyMasked = ref('')
const showKey = ref(false)

const saving = ref(false)
const testing = ref(false)
const busy = ref(false)           // 列表上的操作（切换/删除）

const presets = computed(() => provider.value === 'anthropic' ? ANTHROPIC_PRESETS : OPENAI_PRESETS)

const activePresetKey = computed(() => {
  const hit = presets.value.find(p => p.base_url === baseUrl.value && p.model === model.value)
  return hit ? hit.key : ''
})

// 当前生效配置的方案 id（由后端 is_active 标记，同一时间最多一个）
const activePlanId = computed(() => plans.value.find(p => p.is_active)?.id || '')

const isEditingActive = computed(() => editingPlan.value && editingPlan.value.id === activePlanId.value)

function applyPreset(p) {
  provider.value = p.provider
  baseUrl.value = p.base_url
  model.value = p.model
}

function resetForm() {
  formName.value = ''
  provider.value = 'openai'
  apiKey.value = ''
  baseUrl.value = ''
  model.value = ''
  maxTokens.value = 4096
  temperature.value = 0.7
  hasKey.value = false
  keyMasked.value = ''
  showKey.value = false
}

function startCreate() {
  editingPlan.value = null
  resetForm()
  status.value = null
  view.value = 'form'
}

function startEdit(plan) {
  editingPlan.value = plan
  formName.value = plan.name || ''
  provider.value = plan.provider || 'openai'
  baseUrl.value = plan.base_url || ''
  model.value = plan.model || ''
  maxTokens.value = plan.max_tokens ?? 4096
  temperature.value = plan.temperature ?? 0.7
  hasKey.value = !!plan.has_key
  keyMasked.value = plan.api_key_masked || ''
  apiKey.value = ''
  showKey.value = false
  status.value = null
  view.value = 'form'
}

function backToList() {
  view.value = 'list'
  status.value = null
}

function makePayload() {
  return {
    name: (formName.value || '').trim(),
    provider: provider.value,
    apiKey: apiKey.value,
    baseUrl: baseUrl.value,
    model: model.value,
    maxTokens: Number(maxTokens.value) || 4096,
    temperature: Number(temperature.value) || 0.7,
  }
}

async function doTest() {
  if (!model.value) { status.value = { type: 'error', text: '请先填写模型名称' }; return }
  testing.value = true
  status.value = { type: 'info', text: '正在测试连接…' }
  try {
    const r = await testLLMConfig(makePayload())
    status.value = r.ok
      ? { type: 'success', text: r.message || '连接成功', preview: r.preview }
      : { type: 'error', text: r.message || '连接失败' }
  } catch (e) {
    status.value = { type: 'error', text: e.message || '连接失败' }
  } finally {
    testing.value = false
  }
}

async function doSave() {
  if (!formName.value) { status.value = { type: 'error', text: '请填写配置名称' }; return }
  if (!model.value) { status.value = { type: 'error', text: '请填写模型名称' }; return }
  saving.value = true
  status.value = { type: 'info', text: '正在保存…' }
  try {
    if (editingPlan.value) {
      plans.value = await updateLLMPlan(editingPlan.value.id, makePayload())
      status.value = { type: 'success', text: '配置已更新' }
    } else {
      plans.value = await createLLMPlan(makePayload())
      status.value = { type: 'success', text: '配置已创建' }
    }
    view.value = 'list'
  } catch (e) {
    status.value = { type: 'error', text: e.message || '保存失败' }
  } finally {
    saving.value = false
  }
}

async function activatePlan(plan) {
  busy.value = true
  status.value = { type: 'info', text: `正在切换到「${plan.name}」…` }
  try {
    plans.value = await applyLLMPlan(plan.id)
    status.value = { type: 'success', text: `已切换到「${plan.name}」并生效` }
  } catch (e) {
    status.value = { type: 'error', text: e.message || '切换失败' }
  } finally {
    busy.value = false
  }
}

async function removePlan(plan) {
  if (!confirm(`确认删除配置「${plan.name}」？`)) return
  busy.value = true
  try {
    plans.value = await deleteLLMPlan(plan.id)
    status.value = { type: 'success', text: '配置已删除' }
  } catch (e) {
    status.value = { type: 'error', text: e.message || '删除失败' }
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    plans.value = await fetchLLMPlans()
  } catch (e) {
    status.value = { type: 'error', text: '加载失败：' + (e.message || '') }
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="mcfg-page">
    <header class="mcfg-head">
      <div class="mcfg-head-row">
        <div>
          <h1>模型配置</h1>
          <p>管理多套大模型配置，同一时间仅一套生效。点击「设为生效」可即时切换。</p>
        </div>
        <button v-if="view === 'list'" class="btn primary" @click="startCreate">+ 新建配置</button>
      </div>
    </header>

    <div v-if="loading" class="mcfg-loading"><span class="spinner" /> 加载中…</div>

    <template v-else>
      <!-- 列表视图 -->
      <section v-if="view === 'list'" class="mcfg-card">
        <div v-if="pagedPlans.length" class="plan-list">
          <div
            v-for="p in pagedPlans"
            :key="p.id"
            class="plan-row"
            :class="{ 'is-active': p.id === activePlanId }"
          >
            <span class="plan-radio" :class="{ on: p.id === activePlanId }" aria-hidden="true"></span>
            <div class="plan-meta">
              <span class="plan-name">{{ p.name }}</span>
              <span class="plan-tag">{{ p.provider === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容' }}</span>
              <span class="plan-sub">{{ p.model || '—' }} · {{ p.base_url || '—' }}</span>
            </div>
            <div class="plan-actions">
              <span v-if="p.id === activePlanId" class="plan-current">生效中</span>
              <button v-else class="btn mini primary" :disabled="busy" @click="activatePlan(p)">设为生效</button>
              <button class="btn mini" :disabled="busy" @click="startEdit(p)">编辑</button>
              <button class="btn mini danger" :disabled="busy" @click="removePlan(p)">删除</button>
            </div>
          </div>
          <Pagination v-if="plans.length > pageSize" v-model:page="page" v-model:page-size="pageSize" :total="plans.length" />
        </div>
        <div v-else class="plan-empty">还没有配置，点击右上角「新建配置」创建第一套。</div>
      </section>

      <!-- 表单视图（新建 / 编辑） -->
      <section v-else class="mcfg-form">
        <div class="mcfg-back">
          <button class="btn mini" @click="backToList">← 返回列表</button>
          <span class="mcfg-form-title">{{ editingPlan ? '编辑配置' : '新建配置' }}</span>
          <span v-if="isEditingActive" class="plan-current">当前生效</span>
        </div>

        <section class="mcfg-card">
          <div class="mcfg-card-title">配置名称</div>
          <input type="text" class="mcfg-input" v-model="formName" placeholder="如：DeepSeek 生产" />
        </section>

        <section class="mcfg-card">
          <div class="mcfg-card-title">接口格式</div>
          <div class="mcfg-format">
            <button type="button" class="fmt-card" :class="{ 'is-active': provider === 'openai' }" @click="provider = 'openai'">
              <div class="fmt-name">OpenAI 兼容</div>
              <div class="fmt-desc">智谱 / DeepSeek / 通义千问 / OpenAI 官方 / 自定义 OpenAI 格式</div>
            </button>
            <button type="button" class="fmt-card" :class="{ 'is-active': provider === 'anthropic' }" @click="provider = 'anthropic'">
              <div class="fmt-name">Anthropic</div>
              <div class="fmt-desc">Claude 系列模型，使用 Anthropic 官方协议</div>
            </button>
          </div>
        </section>

        <section class="mcfg-card">
          <div class="mcfg-card-title">快捷预设</div>
          <div class="mcfg-presets">
            <button v-for="p in presets" :key="p.key" type="button" class="preset-chip" :class="{ 'is-active': activePresetKey === p.key }" @click="applyPreset(p)">
              {{ p.label }}
            </button>
            <span class="preset-hint">点击填充接口地址与默认模型，仍可在下方修改</span>
          </div>
        </section>

        <section class="mcfg-card">
          <div class="mcfg-card-title">详细配置</div>
          <div class="mcfg-grid">
            <label class="mcfg-field mcfg-span2">
              <span class="mcfg-label">API Key</span>
              <div class="mcfg-input-row">
                <input
                  :type="showKey ? 'text' : 'password'"
                  class="mcfg-input"
                  v-model="apiKey"
                  :placeholder="hasKey ? `已配置：${keyMasked}（留空表示不修改）` : '请输入 API Key'"
                  autocomplete="off"
                  spellcheck="false"
                />
                <button type="button" class="mcfg-mini-btn" @click="showKey = !showKey">{{ showKey ? '隐藏' : '显示' }}</button>
              </div>
            </label>

            <label class="mcfg-field mcfg-span2">
              <span class="mcfg-label">接口地址 Base URL</span>
              <input type="text" class="mcfg-input" v-model="baseUrl" placeholder="如 https://api.deepseek.com" spellcheck="false" />
            </label>

            <label class="mcfg-field">
              <span class="mcfg-label">模型名称</span>
              <input type="text" class="mcfg-input" v-model="model" placeholder="如 deepseek-chat" spellcheck="false" />
            </label>

            <label class="mcfg-field">
              <span class="mcfg-label">最大 Token 数</span>
              <input type="number" class="mcfg-input" v-model.number="maxTokens" min="1" step="1" />
            </label>

            <label class="mcfg-field">
              <span class="mcfg-label">温度 Temperature</span>
              <input type="number" class="mcfg-input" v-model.number="temperature" min="0" max="2" step="0.1" />
            </label>
          </div>
        </section>

        <div class="mcfg-actions">
          <button class="btn" :disabled="testing || saving" @click="doTest">
            <span v-if="testing" class="spinner" /> {{ testing ? '测试中…' : '测试连接' }}
          </button>
          <button class="btn primary" :disabled="testing || saving" @click="doSave">
            <span v-if="saving" class="spinner" style="border-top-color:#fff" /> {{ editingPlan ? '保存修改' : '创建配置' }}
          </button>
        </div>
      </section>

      <!-- 状态反馈（两种视图共用） -->
      <transition name="mcfg-fade">
        <div v-if="status" class="mcfg-status" :class="`is-${status.type}`">
          <span class="mcfg-status-text">{{ status.text }}</span>
          <span v-if="status.preview" class="mcfg-status-preview">模型回复：{{ status.preview }}</span>
        </div>
      </transition>
    </template>
  </div>
</template>

<style scoped>
.mcfg-page { max-width: 760px; margin: 0 auto; }

.mcfg-head { margin-bottom: 20px; }
.mcfg-head-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.mcfg-head h1 { font-size: 22px; font-weight: 700; color: var(--c-fg); margin-bottom: 6px; }
.mcfg-head p { font-size: 13px; color: var(--c-secondary); line-height: 1.6; max-width: 560px; }

.mcfg-loading { display: flex; align-items: center; gap: 10px; color: var(--c-secondary); font-size: 14px; padding: 40px 0; }

.mcfg-card {
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  background: var(--c-panel-elevated);
  padding: 18px 20px;
  margin-bottom: 16px;
}
.mcfg-card-title { font-size: 13px; font-weight: 700; color: var(--c-fg); margin-bottom: 14px; letter-spacing: 0.2px; }

/* 列表 */
.plan-list { display: flex; flex-direction: column; gap: 10px; }
.plan-row {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 16px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); transition: border-color 150ms, background 150ms;
}
.plan-row.is-active { border-color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 9%, transparent); }
.plan-radio {
  width: 16px; height: 16px; flex-shrink: 0;
  border: 2px solid var(--c-border); border-radius: 50%; position: relative; transition: border-color 150ms;
}
.plan-radio.on { border-color: var(--c-accent); }
.plan-radio.on::after { content: ''; position: absolute; inset: 3px; border-radius: 50%; background: var(--c-accent); }
.plan-meta { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; flex-wrap: wrap; }
.plan-name { font-size: 14px; font-weight: 700; color: var(--c-fg); }
.plan-tag {
  font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
  background: var(--c-muted); color: var(--c-secondary); white-space: nowrap;
}
.plan-sub { font-size: 12px; color: var(--c-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 300px; }
.plan-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.plan-current { font-size: 12px; font-weight: 700; color: var(--c-accent); }
.plan-empty { font-size: 13px; color: var(--c-secondary); padding: 10px 2px; }

/* 表单视图 */
.mcfg-back { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.mcfg-form-title { font-size: 15px; font-weight: 700; color: var(--c-fg); }

/* 接口格式 */
.mcfg-format { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.fmt-card {
  text-align: left; cursor: pointer; padding: 14px 16px; border-radius: var(--radius-sm);
  border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-fg);
  transition: border-color 150ms, background 150ms, box-shadow 150ms;
}
.fmt-card:hover { background: var(--c-muted); }
.fmt-card.is-active { border-color: var(--c-accent); box-shadow: 0 0 0 1px var(--c-accent) inset; background: color-mix(in srgb, var(--c-accent) 10%, transparent); }
.fmt-name { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
.fmt-desc { font-size: 12px; color: var(--c-secondary); line-height: 1.5; }

/* 预设 */
.mcfg-presets { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.preset-chip {
  padding: 6px 14px; border-radius: 999px; border: 1px solid var(--c-border);
  background: var(--c-panel); color: var(--c-fg); font-size: 13px; font-weight: 600; cursor: pointer;
  transition: background 150ms, border-color 150ms, color 150ms;
}
.preset-chip:hover { background: var(--c-muted); }
.preset-chip.is-active { background: var(--c-accent); color: #fff; border-color: var(--c-accent); }
.preset-hint { font-size: 12px; color: var(--c-secondary); margin-left: 4px; }

/* 表单字段 */
.mcfg-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.mcfg-field { display: flex; flex-direction: column; gap: 6px; }
.mcfg-span2 { grid-column: 1 / -1; }
.mcfg-label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.mcfg-input {
  padding: 9px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  font-size: 14px; font-family: var(--font); outline: none;
  background: var(--c-panel); color: var(--c-fg); width: 100%;
  transition: border-color 150ms, box-shadow 150ms;
}
.mcfg-input:focus { border-color: var(--c-accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c-accent) 22%, transparent); }
.mcfg-input::placeholder { color: var(--c-secondary); opacity: 0.7; }
.mcfg-input-row { display: flex; gap: 8px; align-items: stretch; }
.mcfg-input-row .mcfg-input { flex: 1; }
.mcfg-mini-btn {
  flex-shrink: 0; padding: 0 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-secondary); font-size: 12px; font-weight: 600; cursor: pointer;
}
.mcfg-mini-btn:hover { background: var(--c-muted); color: var(--c-fg); }

/* 操作 */
.mcfg-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px; }
.btn.mini { padding: 5px 11px; font-size: 12px; }

/* 状态反馈 */
.mcfg-status {
  margin-top: 14px; padding: 12px 16px; border-radius: var(--radius-sm);
  font-size: 13px; line-height: 1.6; display: flex; flex-direction: column; gap: 2px;
}
.mcfg-status.is-success { background: color-mix(in srgb, var(--c-success) 14%, transparent); color: var(--c-success); border: 1px solid color-mix(in srgb, var(--c-success) 36%, transparent); }
.mcfg-status.is-error { background: color-mix(in srgb, var(--c-danger) 14%, transparent); color: var(--c-danger); border: 1px solid color-mix(in srgb, var(--c-danger) 36%, transparent); }
.mcfg-status.is-info { background: var(--c-muted); color: var(--c-secondary); border: 1px solid var(--c-border); }
.mcfg-status-preview { font-size: 12px; opacity: 0.85; }

.mcfg-fade-enter-active, .mcfg-fade-leave-active { transition: opacity 160ms ease; }
.mcfg-fade-enter-from, .mcfg-fade-leave-to { opacity: 0; }

@media (max-width: 640px) {
  .mcfg-format { grid-template-columns: 1fr; }
  .mcfg-grid { grid-template-columns: 1fr; }
  .plan-sub { max-width: 100%; }
}
</style>
