<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  connectMonitorStream,
  fetchMonitorOverview,
  streamMonitorLlm,
  triggerMonitorCheck,
} from '../../api/monitor'
import ComponentCard from './ComponentCard.vue'
import SystemInfoPanel from './SystemInfoPanel.vue'

const loading = ref(true)
const errorMsg = ref('')
const checking = ref(false)
const checkedAt = ref('')
const summary = ref({ total: 0, ok: 0, error: 0, disabled: 0, unconfigured: 0 })
const components = ref([])
const system = ref({})
let streamCtl = null

// 弹窗状态
const dialogVisible = ref(false)
const dialogKey = ref('')
const testLoading = ref(false)
const testResult = ref(null)
const testError = ref('')

// LLM 测试状态
const llmPrompt = ref('你好，请用中文简单介绍一下你自己。')
const llmReasoning = ref('')
const llmAnswer = ref('')
const llmError = ref('')

const dialogCompRef = ref(null)
const isLlmDialog = computed(() => dialogKey.value === 'llm')

const CATEGORIES = [
  { key: 'data_store', label: '数据存储' },
  { key: 'ai', label: 'AI 能力' },
  { key: 'parse', label: '解析采集' },
  { key: 'service', label: '运行服务' },
]

const groups = computed(() => {
  return CATEGORIES.map((cat) => ({
    ...cat,
    items: components.value.filter((c) => c.category === cat.key),
  })).filter((g) => g.items.length)
})

function applySnapshot(data) {
  if (!data) return
  if (data.checked_at) checkedAt.value = data.checked_at
  if (data.summary) summary.value = data.summary
  if (Array.isArray(data.components)) components.value = data.components
  if (data.system) system.value = data.system
}

function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

async function doRefresh() {
  checking.value = true
  errorMsg.value = ''
  try {
    const data = await triggerMonitorCheck()
    applySnapshot(data)
  } catch (e) {
    errorMsg.value = e.message || '刷新失败'
  } finally {
    checking.value = false
  }
}

function openTest(comp) {
  dialogKey.value = comp.key
  dialogCompRef.value = comp
  dialogVisible.value = true
  testResult.value = null
  testError.value = ''
  llmReasoning.value = ''
  llmAnswer.value = ''
  llmError.value = ''
}

function closeDialog() {
  dialogVisible.value = false
}

async function runComponentTest() {
  if (testLoading.value) return
  testLoading.value = true
  testResult.value = null
  testError.value = ''
  try {
    const data = await triggerMonitorCheck(dialogKey.value)
    testResult.value = data
  } catch (e) {
    testError.value = e.message || '检测失败'
  } finally {
    testLoading.value = false
  }
}

async function runLlmTest() {
  if (testLoading.value) return
  testLoading.value = true
  llmReasoning.value = ''
  llmAnswer.value = ''
  llmError.value = ''
  try {
    await streamMonitorLlm(llmPrompt.value, {
      onReasoning: (t) => { llmReasoning.value += t },
      onContent: (t) => { llmAnswer.value += t },
    })
  } catch (e) {
    llmError.value = e.message || '调用失败'
  } finally {
    testLoading.value = false
  }
}

onMounted(async () => {
  try {
    const data = await fetchMonitorOverview()
    applySnapshot(data)
  } catch (e) {
    errorMsg.value = '加载失败：' + (e.message || '')
  } finally {
    loading.value = false
  }

  // 建立 SSE 长连接：服务端定时推送组件状态
  streamCtl = connectMonitorStream({
    onSnapshot: applySnapshot,
    onError: () => { /* EventSource 自动重连 */ },
  })
})

onBeforeUnmount(() => {
  if (streamCtl) streamCtl.close()
})
</script>

<template>
  <div class="mon-page">
    <header class="mon-head">
      <div>
        <h1>系统监控</h1>
        <p>系统全部后端组件运行状态一览，通过 SSE 定时推送自动刷新。</p>
      </div>
      <button class="btn primary" :disabled="checking" @click="doRefresh">
        <span v-if="checking" class="spinner" style="border-top-color:#fff" />
        {{ checking ? '检测中…' : '刷新' }}
      </button>
    </header>

    <div v-if="loading" class="mon-loading"><span class="spinner" /> 加载中…</div>

    <template v-else>
      <div v-if="errorMsg" class="mon-error">{{ errorMsg }}</div>

      <!-- 状态摘要条 -->
      <section class="mon-summary">
        <div class="ms-item"><span class="ms-num ok">{{ summary.ok }}</span><span class="ms-label">正常</span></div>
        <div class="ms-item"><span class="ms-num error">{{ summary.error }}</span><span class="ms-label">异常</span></div>
        <div class="ms-item"><span class="ms-num disabled">{{ summary.disabled }}</span><span class="ms-label">未启用</span></div>
        <div class="ms-item"><span class="ms-num unconfigured">{{ summary.unconfigured }}</span><span class="ms-label">未配置</span></div>
        <div class="ms-item ms-time"><span class="ms-label">最近检测：{{ fmtTime(checkedAt) }}</span></div>
      </section>

      <!-- 组件分组 -->
      <section v-for="g in groups" :key="g.key" class="mon-group">
        <h3 class="mon-group-title">{{ g.label }}</h3>
        <div class="mon-grid">
          <ComponentCard
            v-for="c in g.items"
            :key="c.key"
            :comp="c"
            @test="openTest"
          />
        </div>
      </section>

      <!-- 系统信息 -->
      <SystemInfoPanel :system="system" />

      <!-- 测试弹窗 -->
      <Teleport to="body">
        <div v-if="dialogVisible" class="dlg-overlay" @click.self="closeDialog">
          <div class="dlg-box">
            <header class="dlg-head">
              <h3>
                <span v-if="dialogCompRef">{{ dialogCompRef.name }}</span>
                <span v-else>组件测试</span>
                <span class="dlg-sub">{{ isLlmDialog ? '流式调用测试' : '手动连通性检测' }}</span>
              </h3>
              <button class="dlg-close" @click="closeDialog">&times;</button>
            </header>

            <!-- LLM 测试 -->
            <template v-if="isLlmDialog">
              <div class="dlg-body">
                <div class="dlg-field">
                  <label>Prompt</label>
                  <textarea v-model="llmPrompt" rows="3" placeholder="输入测试提示词…" />
                </div>
                <div class="dlg-actions">
                  <button class="btn primary" :disabled="testLoading" @click="runLlmTest">
                    <span v-if="testLoading" class="spinner" style="border-top-color:#fff" />
                    {{ testLoading ? '生成中…' : '调用模型' }}
                  </button>
                </div>
                <div v-if="llmReasoning || llmAnswer || llmError" class="dlg-output">
                  <div v-if="llmReasoning" class="dlg-block">
                    <div class="dlg-block-title">思考链</div>
                    <pre class="dlg-reasoning">{{ llmReasoning }}</pre>
                  </div>
                  <div v-if="llmAnswer" class="dlg-block">
                    <div class="dlg-block-title">回答</div>
                    <div class="dlg-answer">{{ llmAnswer }}</div>
                  </div>
                  <div v-if="llmError" class="dlg-error">调用出错：{{ llmError }}</div>
                </div>
              </div>
            </template>

            <!-- 普通组件测试 -->
            <template v-else>
              <div class="dlg-body">
                <div class="dlg-actions">
                  <button class="btn primary" :disabled="testLoading" @click="runComponentTest">
                    <span v-if="testLoading" class="spinner" style="border-top-color:#fff" />
                    {{ testLoading ? '检测中…' : '开始检测' }}
                  </button>
                </div>
                <div v-if="testResult" class="dlg-result">
                  <div class="dlg-res-row">
                    <span class="dlg-res-k">状态</span>
                    <span class="dlg-res-v" :class="testResult.status">{{ testResult.status }}</span>
                  </div>
                  <div class="dlg-res-row">
                    <span class="dlg-res-k">消息</span>
                    <span class="dlg-res-v">{{ testResult.message }}</span>
                  </div>
                  <div class="dlg-res-row">
                    <span class="dlg-res-k">耗时</span>
                    <span class="dlg-res-v">{{ testResult.latency_ms }}ms</span>
                  </div>
                  <div v-if="testResult.provider" class="dlg-res-row">
                    <span class="dlg-res-k">Provider</span>
                    <span class="dlg-res-v">{{ testResult.provider }}</span>
                  </div>
                </div>
                <div v-if="testError" class="dlg-error">{{ testError }}</div>
              </div>
            </template>
          </div>
        </div>
      </Teleport>
    </template>
  </div>
</template>

<style scoped>
.mon-page { max-width: 1100px; margin: 0 auto; }

.mon-head { margin-bottom: 20px; }
.mon-head h1 { font-size: 22px; font-weight: 700; color: var(--c-fg); margin-bottom: 6px; }
.mon-head p { font-size: 13px; color: var(--c-secondary); line-height: 1.6; }
.mon-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }

.mon-loading { display: flex; align-items: center; gap: 10px; color: var(--c-secondary); font-size: 14px; padding: 40px 0; }
.mon-error {
  margin-bottom: 14px; padding: 12px 16px; border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--c-danger) 14%, transparent); color: var(--c-danger);
  border: 1px solid color-mix(in srgb, var(--c-danger) 36%, transparent); font-size: 13px;
}

/* 摘要条 */
.mon-summary {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel-elevated); padding: 14px 18px; margin-bottom: 20px;
}
.ms-item { display: flex; align-items: baseline; gap: 6px; padding-right: 14px; border-right: 1px solid var(--c-border); }
.ms-item:last-child { border-right: none; }
.ms-num { font-size: 20px; font-weight: 800; line-height: 1; }
.ms-num.ok { color: var(--c-success); }
.ms-num.error { color: var(--c-danger); }
.ms-num.disabled { color: var(--c-secondary); }
.ms-num.unconfigured { color: #b88230; }
.ms-label { font-size: 12px; color: var(--c-secondary); }
.ms-time { margin-left: auto; border-right: none; }
.ms-time .ms-label { font-size: 12px; }

/* 分组 */
.mon-group { margin-bottom: 22px; }
.mon-group-title { font-size: 14px; font-weight: 700; color: var(--c-fg); margin-bottom: 10px; }
.mon-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }

.mon-gap { margin-bottom: 22px; }

@media (max-width: 700px) {
  .mon-grid { grid-template-columns: 1fr; }
  .ms-time { width: 100%; margin-left: 0; padding-left: 0; }
}

/* 弹窗 */
.dlg-overlay {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(0,0,0,0.55);
  display: flex; align-items: center; justify-content: center;
  padding: 20px;
}
.dlg-box {
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  width: 100%; max-width: 640px; max-height: 85vh;
  display: flex; flex-direction: column;
  box-shadow: 0 20px 60px rgba(0,0,0,0.4);
}
.dlg-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid var(--c-border);
}
.dlg-head h3 { font-size: 15px; font-weight: 700; color: var(--c-fg); display: flex; align-items: center; gap: 10px; }
.dlg-sub { font-size: 12px; font-weight: 400; color: var(--c-secondary); }
.dlg-close {
  background: transparent; border: none; color: var(--c-secondary);
  font-size: 22px; line-height: 1; cursor: pointer; padding: 0 4px;
}
.dlg-close:hover { color: var(--c-fg); }

.dlg-body { padding: 16px 20px; overflow-y: auto; flex: 1; }
.dlg-field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.dlg-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.dlg-field textarea {
  background: var(--c-panel-elevated); color: var(--c-fg);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  padding: 10px 12px; font-size: 13px; line-height: 1.6; resize: vertical;
}
.dlg-actions { display: flex; justify-content: flex-end; margin-bottom: 14px; }

.dlg-output { display: flex; flex-direction: column; gap: 14px; }
.dlg-block { display: flex; flex-direction: column; gap: 6px; }
.dlg-block-title { font-size: 12px; font-weight: 700; color: var(--c-secondary); }
.dlg-reasoning {
  white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.7;
  font-family: var(--font); color: var(--c-secondary);
  background: var(--c-muted); border-radius: var(--radius-sm); padding: 12px 14px;
  max-height: 240px; overflow: auto;
}
.dlg-answer {
  font-size: 14px; line-height: 1.8; color: var(--c-fg);
  white-space: pre-wrap; word-break: break-word;
}
.dlg-error { font-size: 13px; color: var(--c-danger); margin-top: 4px; }

.dlg-result {
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel-elevated); padding: 12px 14px;
  display: flex; flex-direction: column; gap: 8px;
}
.dlg-res-row { display: flex; gap: 10px; font-size: 13px; line-height: 1.5; }
.dlg-res-k { color: var(--c-secondary); flex-shrink: 0; min-width: 64px; }
.dlg-res-v { color: var(--c-fg); word-break: break-all; }
.dlg-res-v.ok { color: var(--c-success); font-weight: 700; }
.dlg-res-v.error { color: var(--c-danger); font-weight: 700; }
.dlg-res-v.disabled { color: var(--c-secondary); font-weight: 700; }
.dlg-res-v.unconfigured { color: #b88230; font-weight: 700; }
</style>
