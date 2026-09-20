<template>
  <div class="waterfall">
    <div v-if="!rows.length" class="wf-empty">该调用暂无 span 数据</div>

    <!-- 表头 -->
    <div v-if="rows.length" class="wf-head">
      <span class="wf-name-col">Span</span>
      <span class="wf-bar-col">时间线（总 {{ fmtMs(totalMs) }}）</span>
      <span class="wf-dur-col">耗时</span>
    </div>

    <!-- 瀑布行（树形 + 并列横条） -->
    <div
      v-for="row in rows"
      :key="row.span.span_id"
      class="wf-row"
      :class="{ selected: selected?.span_id === row.span.span_id, error: row.span.error }"
      @click="select(row.span)"
    >
      <span class="wf-name-col" :style="{ paddingLeft: 8 + row.depth * 16 + 'px' }">
        <button
          v-if="row.hasChildren"
          class="wf-toggle"
          @click.stop="toggle(row.span.span_id)"
        >{{ collapsed.has(row.span.span_id) ? '+' : '−' }}</button>
        <span class="wf-dot" :class="colorKind(row.span)"></span>
        <span class="wf-name" :title="row.span.name">{{ shortName(row.span.name) }}</span>
      </span>
      <span class="wf-bar-col">
        <span class="wf-track">
          <span
            class="wf-bar"
            :class="colorKind(row.span)"
            :style="{ left: barLeft(row.span) + '%', width: barWidth(row.span) + '%' }"
          ></span>
        </span>
      </span>
      <span class="wf-dur-col" :class="{ slow: row.span.duration_ms >= slowMs }">
        {{ fmtMs(row.span.duration_ms) }}
      </span>
    </div>

    <!-- 属性面板 -->
    <div v-if="selected" class="wf-attrs">
      <div class="wf-attrs-head">
        <strong>{{ selected.name }}</strong>
        <button class="wf-close" @click="selected = null">关闭</button>
      </div>
      <div v-if="selected.error" class="wf-err">{{ selected.status_message || '（无错误信息）' }}</div>
      <table class="wf-attr-tbl">
        <tbody>
          <tr v-for="(v, k) in sortedAttrs" :key="k">
            <td class="k">{{ k }}</td>
            <td class="v">{{ v }}</td>
          </tr>
          <tr v-if="!Object.keys(sortedAttrs).length">
            <td class="k">—</td><td class="v dim">无属性</td>
          </tr>
        </tbody>
      </table>
      <div v-if="exceptionEvents.length" class="wf-events">
        <div class="wf-events-title">异常事件</div>
        <div v-for="(e, i) in exceptionEvents" :key="i" class="wf-event">
          {{ e.name }} · {{ e.attributes?.['exception.type'] || '' }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  spans: { type: Array, default: () => [] },
  rootStartNs: { type: Number, default: 0 },
  totalMs: { type: Number, default: 0 },
  slowMs: { type: Number, default: 3000 },
})

const collapsed = ref(new Set())
const selected = ref(null)

// ── 树构建与展平（默认全展开，collapsed 集合控制折叠） ──
const rows = computed(() => {
  if (!props.spans.length) return []
  const byParent = new Map()
  for (const s of props.spans) {
    const key = s.parent_span_id || ''
    if (!byParent.has(key)) byParent.set(key, [])
    byParent.get(key).push(s)
  }
  const out = []
  const walk = (list, depth) => {
    for (const span of list) {
      const children = byParent.get(span.span_id) || []
      out.push({ span, depth, hasChildren: children.length > 0 })
      if (children.length && !collapsed.value.has(span.span_id)) {
        walk(children, depth + 1)
      }
    }
  }
  walk(byParent.get('') || [], 0)
  return out
})

// 时间轴总长（ns）：最后一个 span 结束时刻 - 根开始时刻
const totalNs = computed(() => {
  if (!props.spans.length) return 1
  const maxEnd = Math.max(
    ...props.spans.map((s) => Number(s.start_ns) + Math.round(Number(s.duration_ms) * 1e6))
  )
  return Math.max(1, maxEnd - Number(props.rootStartNs))
})

function barLeft(span) {
  return Math.max(0, ((Number(span.start_ns) - props.rootStartNs) / totalNs.value) * 100)
}

function barWidth(span) {
  const pct = (Math.round(Number(span.duration_ms) * 1e6) / totalNs.value) * 100
  return Math.max(0.4, Math.min(100, pct))
}

function toggle(spanId) {
  const next = new Set(collapsed.value)
  if (next.has(spanId)) next.delete(spanId)
  else next.add(spanId)
  collapsed.value = next
}

function select(span) {
  selected.value = selected.value?.span_id === span.span_id ? null : span
}

// ── 展示辅助 ──
function colorKind(span) {
  if (span.error) return 'err'
  if (span.kind === 1) return 'server' // SpanKind.SERVER（OTel Python 编号）
  const n = span.name || ''
  if (n.includes('llm')) return 'llm'
  if (n.startsWith('agent.')) return 'agent'
  if (n.startsWith('rag.') || n.startsWith('oag.')) return 'rag'
  return 'other'
}

function shortName(name) {
  return String(name || '').replace('agent.multi.node[', 'node:').replace(/\]$/, '')
}

const sortedAttrs = computed(() => {
  if (!selected.value) return {}
  const entries = Object.entries(selected.value.attributes || {})
  entries.sort(([a], [b]) => a.localeCompare(b))
  return Object.fromEntries(entries)
})

const exceptionEvents = computed(() => {
  if (!selected.value) return []
  return (selected.value.events || []).filter((e) => String(e.name).includes('exception'))
})

function fmtMs(v) {
  const n = Number(v) || 0
  if (n >= 1000) return (n / 1000).toFixed(2) + ' s'
  return n.toFixed(n < 10 ? 1 : 0) + ' ms'
}
</script>

<style scoped>
.waterfall { font-size: 12px; color: var(--c-fg); }

.wf-empty { padding: 24px; text-align: center; color: var(--c-secondary); }

.wf-head, .wf-row {
  display: grid;
  grid-template-columns: minmax(180px, 34%) 1fr 64px;
  align-items: center;
  gap: 8px;
}
.wf-head {
  padding: 6px 8px;
  color: var(--c-secondary);
  font-weight: 600;
  border-bottom: 1px solid var(--c-border);
  background: var(--c-muted);
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
}
.wf-row {
  padding: 3px 8px;
  border-bottom: 1px solid color-mix(in srgb, var(--c-border) 55%, transparent);
  cursor: pointer;
  line-height: 22px;
  transition: background 120ms;
}
.wf-row:hover { background: var(--c-muted); }
.wf-row.selected { background: color-mix(in srgb, var(--c-accent) 12%, transparent); }
.wf-row.error .wf-name { color: var(--c-danger); }

.wf-name-col { display: flex; align-items: center; gap: 6px; min-width: 0; overflow: hidden; }
.wf-toggle {
  flex: none; width: 16px; height: 16px; line-height: 13px; padding: 0;
  border: 1px solid var(--c-border); border-radius: 3px; background: var(--c-panel);
  color: var(--c-secondary); font-size: 11px; cursor: pointer;
}
.wf-toggle:hover { color: var(--c-fg); background: var(--c-muted-hover); }
.wf-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; }
.wf-dot.server { background: #3b82f6; }
.wf-dot.agent { background: #8b5cf6; }
.wf-dot.rag { background: #10b981; }
.wf-dot.llm { background: #f59e0b; }
.wf-dot.err { background: var(--c-danger); }
.wf-dot.other { background: var(--c-secondary); }
.wf-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.wf-track { position: relative; display: block; height: 12px; background: var(--c-muted); border-radius: 3px; overflow: hidden; }
.wf-bar { position: absolute; top: 0; height: 100%; border-radius: 3px; min-width: 3px; }
.wf-bar.server { background: #3b82f6; }
.wf-bar.agent { background: #8b5cf6; }
.wf-bar.rag { background: #10b981; }
.wf-bar.llm { background: #f59e0b; }
.wf-bar.err { background: var(--c-danger); }
.wf-bar.other { background: var(--c-secondary); }

.wf-dur-col { text-align: right; font-variant-numeric: tabular-nums; color: var(--c-fg); }
.wf-dur-col.slow { color: var(--c-danger); font-weight: 600; }

.wf-attrs { margin-top: 10px; border-top: 1px dashed var(--c-border); padding-top: 8px; }
.wf-attrs-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; color: var(--c-fg); }
.wf-close {
  border: 1px solid var(--c-border); border-radius: 4px; background: var(--c-panel);
  padding: 2px 10px; font-size: 12px; cursor: pointer; color: var(--c-secondary);
}
.wf-close:hover { color: var(--c-fg); background: var(--c-muted); }
.wf-err {
  background: color-mix(in srgb, var(--c-danger) 12%, transparent);
  color: var(--c-danger);
  border: 1px solid color-mix(in srgb, var(--c-danger) 35%, transparent);
  border-radius: 4px; padding: 6px 8px; margin-bottom: 6px;
  white-space: pre-wrap; word-break: break-all;
}
.wf-attr-tbl { width: 100%; border-collapse: collapse; }
.wf-attr-tbl td { padding: 3px 6px; border-bottom: 1px solid color-mix(in srgb, var(--c-border) 55%, transparent); vertical-align: top; }
.wf-attr-tbl .k { width: 38%; color: var(--c-secondary); word-break: break-all; }
.wf-attr-tbl .v { word-break: break-all; }
.wf-attr-tbl .v.dim { color: var(--c-secondary); }
.wf-events { margin-top: 8px; }
.wf-events-title { font-weight: 600; color: var(--c-danger); margin-bottom: 4px; }
.wf-event { color: var(--c-danger); padding: 2px 0; }
</style>
