<template>
  <div class="trace-page">
    <header class="page-head">
      <div>
        <h2>接口追踪</h2>
        <p class="sub">
          OpenTelemetry 链路追踪 · span 自托管存储
          <template v-if="stats"> · 窗口 {{ stats.hours }}h · 共 {{ stats.total_calls }} 次调用 · 慢阈值 {{ stats.slow_threshold_ms }}ms</template>
        </p>
      </div>
      <div class="controls">
        <select v-model.number="hours" class="sel" @change="reloadAll">
          <option :value="6">近 6 小时</option>
          <option :value="24">近 24 小时</option>
          <option :value="72">近 3 天</option>
          <option :value="168">近 7 天</option>
        </select>
        <label class="chk">
          <input v-model="slowOnly" type="checkbox" @change="loadCalls" /> 只看慢调用
        </label>
        <button class="btn" :disabled="loading" @click="reloadAll">刷新</button>
      </div>
    </header>

    <div v-if="error" class="banner error">{{ error }}</div>

    <!-- 接口聚合榜（慢接口定位） -->
    <section class="panel">
      <div class="panel-head">
        <h3>接口聚合榜 <span class="hint">按 p95 倒序 · 点击行筛选调用明细</span></h3>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th>接口</th>
              <th class="num">调用数</th>
              <th class="num">avg</th>
              <th class="num">p95</th>
              <th class="num">max</th>
              <th class="num">错误</th>
              <th class="num">慢调用</th>
              <th>最近调用</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="e in endpoints"
              :key="e.endpoint"
              :class="{ active: e.endpoint === selectedEndpoint }"
              @click="toggleEndpoint(e.endpoint)"
            >
              <td class="mono">{{ e.endpoint }}</td>
              <td class="num">{{ e.count }}</td>
              <td class="num">{{ fmtMs(e.avg_ms) }}</td>
              <td class="num" :class="{ slow: isSlow(e.p95_ms) }">{{ fmtMs(e.p95_ms) }}</td>
              <td class="num" :class="{ slow: isSlow(e.max_ms) }">{{ fmtMs(e.max_ms) }}</td>
              <td class="num" :class="{ err: e.error_count > 0 }">{{ e.error_count }}</td>
              <td class="num" :class="{ slow: e.slow_count > 0 }">{{ e.slow_count }}</td>
              <td class="mono dim">{{ shortTime(e.last_seen) }}</td>
            </tr>
            <tr v-if="!endpoints.length">
              <td colspan="8" class="empty">
                {{ loading ? '加载中…' : '暂无数据（产生接口调用后这里会出现统计）' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 调用明细 -->
    <section class="panel">
      <div class="panel-head">
        <h3>
          调用明细
          <span v-if="selectedEndpoint" class="filter-tag mono" @click="clearEndpoint">
            {{ selectedEndpoint }} ✕
          </span>
        </h3>
        <span class="hint">点击任意调用展开完整瀑布图</span>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th>时间</th>
              <th>接口</th>
              <th class="num">耗时</th>
              <th class="num">状态码</th>
              <th>结果</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in calls" :key="c.trace_id + c.start_time" @click="openTrace(c)">
              <td class="mono dim">{{ shortTime(c.start_time) }}</td>
              <td class="mono">{{ c.endpoint }}</td>
              <td class="num" :class="{ slow: c.slow }">{{ fmtMs(c.duration_ms) }}</td>
              <td class="num" :class="{ err: c.error }">{{ c.status_code || '—' }}</td>
              <td>
                <span v-if="c.error" class="tag err">错误</span>
                <span v-else-if="c.slow" class="tag slow">慢</span>
                <span v-else class="tag ok">正常</span>
              </td>
            </tr>
            <tr v-if="!calls.length">
              <td colspan="5" class="empty">{{ loading ? '加载中…' : '暂无调用记录' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 调用详情抽屉：瀑布图 + 属性面板 -->
    <div v-if="drawer" class="drawer-mask" @click.self="closeDrawer">
      <div class="drawer">
        <header class="drawer-head">
          <div>
            <strong class="mono">{{ drawer.endpoint }}</strong>
            <span class="dim"> · {{ fmtMs(drawer.duration_ms) }} · {{ shortTime(drawer.start_time) }}</span>
            <div class="dim mono small">{{ drawer.trace_id }}</div>
          </div>
          <button class="btn" @click="closeDrawer">关闭</button>
        </header>
        <div class="drawer-body">
          <div v-if="detailLoading" class="empty" style="padding: 32px">加载 span 树…</div>
          <div v-else-if="detailError" class="banner error">{{ detailError }}</div>
          <template v-else-if="detail">
            <div class="legend">
              <span><i class="wf-dot server"></i>HTTP</span>
              <span><i class="wf-dot agent"></i>多智能体节点</span>
              <span><i class="wf-dot rag"></i>检索/融合</span>
              <span><i class="wf-dot llm"></i>LLM</span>
              <span><i class="wf-dot err"></i>错误</span>
            </div>
            <TraceWaterfall
              :spans="detail.spans"
              :root-start-ns="detail.root_start_ns"
              :slow-ms="stats?.slow_threshold_ms || 3000"
            />
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { fetchTraceCalls, fetchTraceDetail, fetchTraceStats } from '../../api/traces'
import TraceWaterfall from './TraceWaterfall.vue'

const hours = ref(24)
const slowOnly = ref(false)
const loading = ref(false)
const error = ref('')

const stats = ref(null)
const calls = ref([])
const selectedEndpoint = ref('')

const drawer = ref(null)          // 当前查看的调用（calls 里的一行）
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')

const endpoints = computed(() => stats.value?.endpoints ?? [])
const slowThreshold = computed(() => Number(stats.value?.slow_threshold_ms) || 3000)

function isSlow(ms) {
  return Number(ms) >= slowThreshold.value
}

async function loadStats() {
  try {
    stats.value = await fetchTraceStats(hours.value)
  } catch (e) {
    error.value = `聚合榜加载失败：${e.message}`
  }
}

async function loadCalls() {
  try {
    const data = await fetchTraceCalls({
      hours: hours.value,
      endpoint: selectedEndpoint.value,
      slowOnly: slowOnly.value,
      limit: 200,
    })
    calls.value = data.calls || []
  } catch (e) {
    error.value = `调用明细加载失败：${e.message}`
  }
}

async function reloadAll() {
  error.value = ''
  loading.value = true
  await Promise.all([loadStats(), loadCalls()])
  loading.value = false
}

function toggleEndpoint(endpoint) {
  selectedEndpoint.value = selectedEndpoint.value === endpoint ? '' : endpoint
  loadCalls()
}

function clearEndpoint() {
  selectedEndpoint.value = ''
  loadCalls()
}

async function openTrace(call) {
  drawer.value = call
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  try {
    detail.value = await fetchTraceDetail(call.trace_id)
  } catch (e) {
    detailError.value = `span 树加载失败：${e.message}`
  } finally {
    detailLoading.value = false
  }
}

function closeDrawer() {
  drawer.value = null
  detail.value = null
}

function fmtMs(v) {
  const n = Number(v) || 0
  if (n >= 1000) return (n / 1000).toFixed(2) + ' s'
  return n.toFixed(n < 10 ? 1 : 0) + ' ms'
}

function shortTime(iso) {
  if (!iso) return '—'
  // iso: 2026-09-20T12:34:56.789 → 09-20 12:34:56
  const m = String(iso).match(/(\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/)
  return m ? `${m[1]} ${m[2]}` : iso
}

onMounted(reloadAll)
</script>

<style scoped>
.trace-page { max-width: 1280px; margin: 0 auto; }

.page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 16px; }
.page-head h2 { margin: 0 0 4px; font-size: 22px; font-weight: 700; color: var(--c-fg); }
.sub { margin: 0; color: var(--c-secondary); font-size: 13px; }

.controls { display: flex; align-items: center; gap: 12px; }
.sel {
  padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); font-size: 13px; color: var(--c-fg);
}
.chk { display: flex; align-items: center; gap: 5px; font-size: 13px; color: var(--c-secondary); cursor: pointer; }
.chk input { accent-color: var(--c-accent); }

.banner { padding: 10px 14px; border-radius: var(--radius-sm); margin-bottom: 14px; font-size: 13px; }
.banner.error {
  background: color-mix(in srgb, var(--c-danger) 12%, transparent);
  color: var(--c-danger);
  border: 1px solid color-mix(in srgb, var(--c-danger) 35%, transparent);
}

.panel {
  background: var(--c-panel-elevated); border: 1px solid var(--c-border); border-radius: var(--radius);
  margin-bottom: 16px; overflow: hidden;
}
.panel-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 16px; border-bottom: 1px solid var(--c-border);
}
.panel-head h3 { margin: 0; font-size: 14px; color: var(--c-fg); }
.hint { color: var(--c-secondary); font-size: 12px; font-weight: 400; margin-left: 8px; }
.filter-tag {
  cursor: pointer;
  background: color-mix(in srgb, var(--c-accent) 14%, transparent);
  color: var(--c-accent);
  border-radius: 4px; padding: 2px 8px; font-size: 12px; margin-left: 8px;
}

.tbl-wrap { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td { padding: 8px 12px; text-align: left; white-space: nowrap; }
.tbl th { color: var(--c-secondary); font-weight: 600; border-bottom: 1px solid var(--c-border); background: var(--c-muted); }
.tbl td { border-bottom: 1px solid color-mix(in srgb, var(--c-border) 55%, transparent); color: var(--c-fg); }
.tbl tbody tr { cursor: pointer; transition: background 120ms; }
.tbl tbody tr:hover { background: var(--c-muted); }
.tbl tbody tr.active { background: color-mix(in srgb, var(--c-accent) 12%, transparent); }
.tbl .num { text-align: right; font-variant-numeric: tabular-nums; }
.tbl .empty { text-align: center; color: var(--c-secondary); padding: 28px; cursor: default; }

.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.dim { color: var(--c-secondary); }
.small { font-size: 11px; }
.slow { color: #d97706; font-weight: 600; }
.err { color: var(--c-danger); font-weight: 600; }

.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.tag.ok { background: color-mix(in srgb, var(--c-success) 16%, transparent); color: var(--c-success); }
.tag.slow { background: rgba(217, 119, 6, 0.16); color: #d97706; }
.tag.err { background: color-mix(in srgb, var(--c-danger) 16%, transparent); color: var(--c-danger); }

.legend { display: flex; gap: 16px; padding: 0 0 10px; color: var(--c-secondary); font-size: 12px; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.legend i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.legend i.server { background: #3b82f6; }
.legend i.agent { background: #8b5cf6; }
.legend i.rag { background: #10b981; }
.legend i.llm { background: #f59e0b; }
.legend i.err { background: var(--c-danger); }

.drawer-mask {
  position: fixed; inset: 0; background: var(--c-overlay);
  z-index: 100; display: flex; justify-content: flex-end;
}
.drawer {
  width: min(760px, 92vw); background: var(--c-bg); height: 100%;
  border-left: 1px solid var(--c-border);
  display: flex; flex-direction: column; box-shadow: -8px 0 24px rgba(0, 0, 0, 0.25);
  animation: slide-in 0.18s ease-out;
}
@keyframes slide-in {
  from { transform: translateX(24px); opacity: 0.6; }
  to { transform: translateX(0); opacity: 1; }
}
.drawer-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 16px; border-bottom: 1px solid var(--c-border);
}
.drawer-body { padding: 14px 16px; overflow-y: auto; flex: 1; }
</style>
