<template>
  <div class="ars-page">
    <header class="page-head">
      <div>
        <h2>Agent 调用统计</h2>
        <p class="sub">
          一次智能体执行记一行 · 明细保留最近 30 天
          <template v-if="stats.start_date"> · 窗口 {{ stats.start_date }} ~ {{ stats.end_date }}</template>
        </p>
      </div>
      <div class="controls">
        <select v-model.number="days" class="sel" @change="reloadAll">
          <option :value="7">近 7 天</option>
          <option :value="14">近 14 天</option>
          <option :value="30">近 30 天</option>
        </select>
        <button class="btn" :disabled="loading" @click="reloadAll">刷新</button>
        <button class="btn ghost" :disabled="loading" @click="doCleanup">清理旧数据</button>
      </div>
    </header>

    <div v-if="error" class="banner err">{{ error }}</div>

    <!-- 概览 -->
    <section class="cards">
      <div class="card">
        <span class="k">调用总量</span>
        <strong class="v">{{ overview ? overview.total : '—' }}</strong>
        <span class="d">近 {{ days }} 天</span>
      </div>
      <div class="card">
        <span class="k">今日调用</span>
        <strong class="v">{{ overview ? overview.today_total : '—' }}</strong>
        <span class="d">成功 {{ overview ? overview.today_success : '—' }} 次</span>
      </div>
      <div class="card">
        <span class="k">成功率</span>
        <strong class="v">{{ overview ? overview.success_rate + '%' : '—' }}</strong>
        <span class="d">失败 {{ overview ? overview.failed : '—' }} 次</span>
      </div>
      <div class="card">
        <span class="k">平均耗时</span>
        <strong class="v">{{ overview ? fmtMs(overview.avg_duration_ms) : '—' }}</strong>
        <span class="d">单次调用</span>
      </div>
      <div class="card">
        <span class="k">活跃智能体</span>
        <strong class="v">{{ overview ? overview.active_agents : '—' }}</strong>
        <span class="d">活跃用户 {{ overview ? overview.active_users : '—' }}</span>
      </div>
    </section>

    <!-- 每日趋势（仅 date 维度） -->
    <section v-if="groupBy === 'date' && stats.items.length" class="panel">
      <div class="panel-head">
        <h3>每日调用量 <span class="hint">柱高 = 当天调用次数</span></h3>
      </div>
      <div class="bars">
        <div
          v-for="it in stats.items"
          :key="it.key"
          class="bar-col"
          :title="`${it.key}：共 ${it.total} 次（成功 ${it.success} / 失败 ${it.failed}）`"
        >
          <span class="bar-num">{{ it.total }}</span>
          <div class="bar" :style="{ height: barHeight(it.total) }"></div>
          <span class="bar-label">{{ shortDate(it.key) }}</span>
        </div>
      </div>
    </section>

    <!-- 维度聚合 -->
    <section class="panel">
      <div class="panel-head">
        <h3>维度聚合</h3>
        <div class="filters">
          <select v-model="groupBy" class="sel" @change="loadStats">
            <option value="date">按日期</option>
            <option value="agent">按智能体</option>
            <option value="scene">按场景</option>
            <option value="user">按用户</option>
            <option value="model">按模型</option>
            <option value="success">按成败</option>
          </select>
          <select v-model="agentId" class="sel" @change="reloadAll">
            <option value="">全部智能体</option>
            <option v-for="a in agents" :key="a.id" :value="a.id">
              {{ a.name }}（{{ a.total }}）
            </option>
          </select>
          <select v-model="scene" class="sel" @change="reloadAll">
            <option value="">全部场景</option>
            <option value="single">单智能体问答</option>
            <option value="assistant">智能助手</option>
            <option value="multi">多智能体协作</option>
            <option value="deep">深度模式</option>
            <option value="target">目标研判</option>
          </select>
          <select v-model="success" class="sel" @change="reloadAll">
            <option value="">全部结果</option>
            <option value="1">仅成功</option>
            <option value="0">仅失败</option>
          </select>
        </div>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th>{{ groupLabel }}</th>
              <th class="num">调用量</th>
              <th class="num">成功</th>
              <th class="num">失败</th>
              <th class="num">成功率</th>
              <th class="num">平均耗时</th>
              <th>最近一次</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="it in stats.items" :key="it.key">
              <td>{{ it.label || it.key || '（未指定）' }}</td>
              <td class="num">{{ it.total }}</td>
              <td class="num">{{ it.success }}</td>
              <td class="num" :class="{ err: it.failed > 0 }">{{ it.failed }}</td>
              <td class="num">{{ it.success_rate }}%</td>
              <td class="num">{{ fmtMs(it.avg_duration_ms) }}</td>
              <td class="dim mono">{{ shortTime(it.last_at) }}</td>
            </tr>
            <tr v-if="!stats.items.length">
              <td colspan="7" class="empty">
                {{ loading ? '加载中…' : '暂无数据（产生智能体问答/协作/研判后这里会出现统计）' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 明细 -->
    <section class="panel">
      <div class="panel-head">
        <h3>调用明细 <span class="hint">共 {{ logs.total }} 条</span></h3>
        <div class="pager">
          <button class="btn ghost" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
          <span class="dim">第 {{ logs.page }} 页</span>
          <button class="btn ghost" :disabled="page * logs.page_size >= logs.total" @click="goPage(page + 1)">
            下一页
          </button>
        </div>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th>时间</th>
              <th>场景</th>
              <th>智能体</th>
              <th>用户</th>
              <th class="num">耗时</th>
              <th>结果</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in logs.items" :key="r.id">
              <td class="mono dim">{{ shortTime(r.created_at) }}</td>
              <td>
                <span class="tag">{{ r.scene_label || r.scene || '—' }}</span>
              </td>
              <td>{{ r.agent_name || r.agent_id || '（未指定）' }}</td>
              <td>{{ r.username || r.user_id || '—' }}</td>
              <td class="num">{{ fmtMs(r.duration_ms) }}</td>
              <td>
                <span v-if="r.success" class="tag ok">成功</span>
                <span v-else class="tag err" :title="r.error_msg">失败</span>
              </td>
            </tr>
            <tr v-if="!logs.items.length">
              <td colspan="6" class="empty">{{ loading ? '加载中…' : '暂无明细' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  cleanupAgentResearch,
  fetchAgentResearchAgents,
  fetchAgentResearchLogs,
  fetchAgentResearchOverview,
  fetchAgentResearchStats,
} from '../../api/agentStats'

const days = ref(7)
const groupBy = ref('date')
const agentId = ref('')
const scene = ref('')
const success = ref('')
const page = ref(1)

const agents = ref([])
const overview = ref(null)
const stats = ref({ items: [], start_date: '', end_date: '' })
const logs = ref({ items: [], total: 0, page: 1, page_size: 20 })
const loading = ref(false)
const error = ref('')

const GROUP_LABELS = {
  date: '日期', agent: '智能体', scene: '场景',
  user: '用户', model: '模型', success: '结果',
}
const groupLabel = computed(() => GROUP_LABELS[groupBy.value] || '维度')

function filterParams(extra = {}) {
  return {
    days: days.value,
    agent_id: agentId.value,
    scene: scene.value,
    success: success.value === '' ? undefined : success.value === '1',
    ...extra,
  }
}

function fmtMs(ms) {
  const v = Number(ms || 0)
  if (v >= 60000) return (v / 60000).toFixed(1) + 'min'
  if (v >= 1000) return (v / 1000).toFixed(1) + 's'
  return v + 'ms'
}

function shortTime(iso) {
  if (!iso) return '—'
  return String(iso).slice(5, 16).replace('T', ' ')
}

function shortDate(d) {
  return String(d || '').slice(5)
}

function barHeight(total) {
  const max = Math.max(...stats.value.items.map((i) => i.total), 1)
  return Math.max(4, Math.round((total / max) * 100)) + '%'
}

async function loadOverview() {
  try {
    overview.value = await fetchAgentResearchOverview(days.value)
  } catch (e) {
    error.value = e?.message || '概览加载失败'
  }
}

async function loadStats() {
  try {
    stats.value = await fetchAgentResearchStats(
      filterParams({ group_by: groupBy.value }))
  } catch (e) {
    error.value = e?.message || '维度聚合加载失败'
  }
}

async function loadLogs() {
  try {
    logs.value = await fetchAgentResearchLogs(
      filterParams({ page: page.value, page_size: 20 }))
  } catch (e) {
    error.value = e?.message || '明细加载失败'
  }
}

async function loadAgents() {
  try {
    const data = await fetchAgentResearchAgents(days.value)
    agents.value = data?.items || []
  } catch {
    agents.value = []
  }
}

async function reloadAll() {
  loading.value = true
  error.value = ''
  page.value = 1
  try {
    await Promise.all([loadOverview(), loadStats(), loadLogs(), loadAgents()])
  } finally {
    loading.value = false
  }
}

function goPage(p) {
  if (p < 1) return
  page.value = p
  loadLogs()
}

async function doCleanup() {
  if (!window.confirm('将删除 30 天之前的调用明细，确定继续？')) return
  try {
    const res = await cleanupAgentResearch(30)
    window.alert(`已清理 ${res?.removed ?? 0} 条旧明细`)
    reloadAll()
  } catch (e) {
    error.value = e?.message || '清理失败'
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.ars-page {
  padding: 20px 24px 40px;
  color: var(--text, #e6e8ee);
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.page-head h2 { margin: 0 0 4px; font-size: 20px; }
.sub { margin: 0; font-size: 13px; color: var(--text-dim, #8b93a7); }
.controls { display: flex; align-items: center; gap: 8px; }

.banner {
  margin-bottom: 14px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}
.banner.err {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.35);
  color: #f87171;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.card {
  background: var(--panel, #171a23);
  border: 1px solid var(--border, #262b38);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.card .k { font-size: 12px; color: var(--text-dim, #8b93a7); }
.card .v { font-size: 22px; font-weight: 600; }
.card .d { font-size: 12px; color: var(--text-dim, #8b93a7); }

.panel {
  background: var(--panel, #171a23);
  border: 1px solid var(--border, #262b38);
  border-radius: 8px;
  margin-bottom: 16px;
  overflow: hidden;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border, #262b38);
  flex-wrap: wrap;
}
.panel-head h3 { margin: 0; font-size: 14px; font-weight: 600; }
.hint { font-weight: 400; font-size: 12px; color: var(--text-dim, #8b93a7); }
.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.pager { display: flex; align-items: center; gap: 8px; font-size: 13px; }

.bars {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  padding: 16px 14px;
  min-height: 140px;
  overflow-x: auto;
}
.bar-col {
  flex: 1 1 0;
  min-width: 34px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}
.bar-num { font-size: 11px; color: var(--text-dim, #8b93a7); }
.bar {
  width: 60%;
  min-height: 4px;
  background: linear-gradient(180deg, #4f8cff, #2b5fd9);
  border-radius: 3px 3px 0 0;
}
.bar-label { font-size: 11px; color: var(--text-dim, #8b93a7); }

.tbl-wrap { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th, .tbl td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border, #262b38);
  white-space: nowrap;
}
.tbl th { font-weight: 600; color: var(--text-dim, #8b93a7); font-size: 12px; }
.tbl tbody tr:hover { background: rgba(255, 255, 255, 0.03); }
.tbl .num { text-align: right; }
.tbl .num.err { color: #f87171; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.dim { color: var(--text-dim, #8b93a7); }
.empty { text-align: center; color: var(--text-dim, #8b93a7); padding: 24px; }

.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
  background: rgba(255, 255, 255, 0.08);
}
.tag.ok { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
.tag.err { background: rgba(239, 68, 68, 0.16); color: #f87171; }

.sel, .btn {
  background: var(--panel-2, #1f2430);
  color: inherit;
  border: 1px solid var(--border, #262b38);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 13px;
}
.btn { cursor: pointer; }
.btn:hover { border-color: #3a4152; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.ghost { background: transparent; }
</style>
