<script setup>
import { computed } from 'vue'

const props = defineProps({
  comp: { type: Object, required: true },
})

const emit = defineEmits(['test'])

const STATUS_META = {
  ok:          { label: '正常',     cls: 'ok' },
  error:       { label: '异常',     cls: 'error' },
  disabled:    { label: '未启用',   cls: 'disabled' },
  unconfigured: { label: '未配置',  cls: 'unconfigured' },
}

const COMP_ICONS = {
  database:      '🗄️',
  graph_store:   '🗺️',
  vector_store:  '🔎',
  embedding:     '🧠',
  llm:           '🤖',
  parser:        '📄',
  crawl:         '🕷️',
  search:        '🔍',
  scheduler:     '⏰',
  workflow:      '🔄',
}

const meta = computed(() => STATUS_META[props.comp.status] || STATUS_META.error)
const icon = computed(() => COMP_ICONS[props.comp.key] || '🔧')
const providerLabel = computed(() => props.comp.provider || '—')

const configPairs = computed(() => {
  const cfg = props.comp.config || {}
  return Object.entries(cfg)
})

const extraText = computed(() => {
  const extra = props.comp.extra || {}
  const parts = []
  if (extra.node_count != null) parts.push(`节点 ${extra.node_count}`)
  if (extra.edge_count != null) parts.push(`边 ${extra.edge_count}`)
  if (extra.collection_count != null) parts.push(`集合 ${extra.collection_count}`)
  if (extra.dimension != null) parts.push(`维度 ${extra.dimension}`)
  if (extra.job_count != null) parts.push(`任务 ${extra.job_count}`)
  return parts.join(' · ')
})
</script>

<template>
  <div class="cc-card" :class="`is-${props.comp.status}`">
    <header class="cc-head">
      <div class="cc-title-wrap">
        <span class="cc-icon" :title="props.comp.name">{{ icon }}</span>
        <span class="cc-name">{{ props.comp.name }}</span>
        <span v-if="providerLabel !== '—'" class="cc-provider-tag">{{ providerLabel }}</span>
      </div>
      <span class="cc-badge" :class="meta.cls">{{ meta.label }}</span>
    </header>

    <div v-if="props.comp.providers_available?.length" class="cc-available">
      可选：{{ props.comp.providers_available.join(' / ') }}
    </div>

    <div v-if="extraText" class="cc-extra">{{ extraText }}</div>

    <div class="cc-msg" :class="{ warn: props.comp.status !== 'ok' }">{{ props.comp.message }}</div>

    <div v-if="configPairs.length" class="cc-config">
      <div v-for="[k, v] in configPairs" :key="k" class="cc-cfg-row">
        <span class="cc-cfg-k">{{ k }}</span>
        <span class="cc-cfg-v">{{ v }}</span>
      </div>
    </div>

    <footer class="cc-foot">
      <span class="cc-latency">检测 {{ props.comp.latency_ms }}ms</span>
      <button class="cc-test-btn" @click.stop="emit('test', props.comp)">测试</button>
    </footer>
  </div>
</template>

<style scoped>
.cc-card {
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  background: var(--c-panel-elevated);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: border-color 150ms;
}
.cc-card.is-error { border-color: color-mix(in srgb, var(--c-danger) 55%, var(--c-border)); }
.cc-card.is-unconfigured { border-color: color-mix(in srgb, var(--c-warning, #e6a23c) 45%, var(--c-border)); }

.cc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.cc-title-wrap {
  display: flex; align-items: center; gap: 7px;
  min-width: 0; flex: 1;
}
.cc-icon { font-size: 16px; line-height: 1; flex-shrink: 0; filter: saturate(0.85); }
.cc-name { font-size: 14px; font-weight: 700; color: var(--c-fg); white-space: nowrap; }
.cc-provider-tag {
  font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 999px;
  background: color-mix(in srgb, var(--c-primary) 12%, transparent);
  color: var(--c-primary);
  border: 1px solid color-mix(in srgb, var(--c-primary) 22%, transparent);
  white-space: nowrap; flex-shrink: 0;
}

.cc-badge {
  font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 999px;
  white-space: nowrap; flex-shrink: 0;
}
.cc-badge.ok { background: color-mix(in srgb, var(--c-success) 16%, transparent); color: var(--c-success); }
.cc-badge.error { background: color-mix(in srgb, var(--c-danger) 16%, transparent); color: var(--c-danger); }
.cc-badge.disabled { background: var(--c-muted); color: var(--c-secondary); }
.cc-badge.unconfigured { background: color-mix(in srgb, #e6a23c 16%, transparent); color: #b88230; }

.cc-available { font-size: 11px; color: var(--c-secondary); }

.cc-extra { font-size: 12px; color: var(--c-secondary); }

.cc-msg { font-size: 12px; line-height: 1.5; color: var(--c-secondary); word-break: break-all; }
.cc-msg.warn { color: var(--c-danger); }

.cc-config { display: flex; flex-direction: column; gap: 3px; padding-top: 6px; border-top: 1px dashed var(--c-border); }
.cc-cfg-row { display: flex; gap: 8px; font-size: 11px; line-height: 1.4; }
.cc-cfg-k { color: var(--c-secondary); flex-shrink: 0; min-width: 96px; }
.cc-cfg-v { color: var(--c-fg); word-break: break-all; }

.cc-foot { margin-top: auto; padding-top: 4px; display: flex; align-items: center; justify-content: space-between; }
.cc-latency { font-size: 11px; color: var(--c-secondary); }
.cc-test-btn {
  font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 6px;
  background: transparent; color: var(--c-primary);
  border: 1px solid color-mix(in srgb, var(--c-primary) 30%, transparent);
  cursor: pointer; transition: all 150ms;
}
.cc-test-btn:hover {
  background: color-mix(in srgb, var(--c-primary) 12%, transparent);
}
</style>
