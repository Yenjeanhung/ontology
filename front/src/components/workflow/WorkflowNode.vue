<script setup>
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

const props = defineProps({
  id: { type: String, required: true },
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const TYPE_META = {
  start: { name: '开始', icon: '▶', color: '#64748b' },
  end: { name: '结束', icon: '■', color: '#64748b' },
  agent: { name: '智能体', icon: '🤖', color: '#d97706' },
  service: { name: '实体服务', icon: '⚙️', color: '#2563eb' },
  llm: { name: '大模型', icon: '✨', color: '#7c3aed' },
  condition: { name: '条件分支', icon: '⇄', color: '#ea580c' },
  code: { name: '代码', icon: '</>', color: '#059669' },
}

const type = computed(() => props.data?.nodeType || 'start')
const meta = computed(() => TYPE_META[type.value] || TYPE_META.start)
const title = computed(() => props.data?.title || meta.value.name)
const isCondition = computed(() => type.value === 'condition')
const status = computed(() => props.data?.status || '')
const elapsedText = computed(() => props.data?.elapsedText || '')

const STATUS_LABEL = { running: '运行中', succeeded: '完成', failed: '失败', skipped: '跳过' }

function bodyText(t, cfg = {}) {
  if (t === 'agent') return cfg.agent_id ? '引用已配置智能体' : (cfg.kb_id ? '内联 · KB + 技能' : '未配置知识库')
  if (t === 'service') return cfg.entity_id ? '实体服务' : (cfg.service_id ? '本体服务' : '未配置服务')
  if (t === 'llm') return cfg.prompt_template ? '自定义提示词' : '通用补全'
  if (t === 'condition') return `${cfg.operator || '=='} ${cfg.left || '...'}`
  if (t === 'code') return '沙箱 Python'
  if (t === 'end') return '汇总输出'
  return '运行入口'
}
</script>

<template>
  <div class="wf-node" :class="[status, { selected, condition: isCondition }]" :style="{ '--nc': meta.color }">
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <template v-if="isCondition">
      <Handle id="true" type="source" :position="Position.Right" class="wf-handle wf-handle-true" style="top: 34%" />
      <Handle id="false" type="source" :position="Position.Right" class="wf-handle wf-handle-false" style="top: 66%" />
    </template>
    <Handle v-else type="source" :position="Position.Right" class="wf-handle" />

    <div class="wf-head">
      <span class="wf-ico" :style="{ background: meta.color }">{{ meta.icon }}</span>
      <div class="wf-title">{{ title }}</div>
      <span v-if="status" class="wf-status-chip" :class="'sc-' + status">
        <span v-if="status === 'running'" class="wf-pulse"></span>
        {{ STATUS_LABEL[status] }}<template v-if="elapsedText"> · {{ elapsedText }}</template>
      </span>
    </div>
    <div class="wf-body">{{ bodyText(type, props.data?.config) }}</div>
  </div>
</template>

<style scoped>
.wf-node {
  position: relative;
  width: 200px;
  background: var(--c-panel);
  border: 1px solid var(--c-border-strong, #d8cdbb);
  border-radius: var(--radius, 8px);
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
  font-family: var(--font);
  transition: box-shadow .15s, border-color .15s;
}
.wf-node:hover { box-shadow: 0 4px 14px rgba(0,0,0,.12); }
.wf-node.selected { border-color: var(--c-accent); box-shadow: 0 0 0 2px var(--c-accent-weak, rgba(161,98,7,.10)); }
/* 运行中：只有边框变色 + 徽章圆点闪，卡片本体完全静止（不闪不呼吸） */
.wf-node.running { border-color: var(--c-accent); border-width: 1.5px; }
.wf-node.running .n-head { background: var(--c-accent-weak, rgba(161,98,7,.10)); }

.wf-status-chip {
  display: inline-flex; align-items: center; gap: 4px; flex-shrink: 0;
  padding: 1px 7px; border-radius: 999px; font-size: 9.5px; font-weight: 700; line-height: 1.5;
}
.wf-status-chip.sc-running { color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 14%, transparent); }
.wf-status-chip.sc-succeeded { color: var(--c-success); background: color-mix(in srgb, var(--c-success) 14%, transparent); }
.wf-status-chip.sc-failed { color: var(--c-danger); background: color-mix(in srgb, var(--c-danger) 14%, transparent); }
.wf-status-chip.sc-skipped { color: var(--c-secondary); background: var(--c-muted); }
.wf-pulse {
  width: 7px; height: 7px; border-radius: 50%; background: currentColor;
  animation: wf-dot 1s ease-in-out infinite;
}
@keyframes wf-dot { 50% { opacity: .2; } }
.wf-node.succeeded { border-color: var(--c-success); }
.wf-node.failed { border-color: var(--c-danger); }
.wf-node.skipped { opacity: .45; }

.wf-head {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border-bottom: 1px solid var(--c-border); border-radius: var(--radius, 8px) var(--radius, 8px) 0 0;
}
.wf-node.succeeded .wf-head { background: rgba(22,163,74,.08); }
.wf-node.failed .wf-head { background: rgba(220,38,38,.08); }
.wf-node.running .wf-head { background: var(--c-accent-weak, rgba(161,98,7,.10)); }

.wf-ico {
  width: 22px; height: 22px; border-radius: 6px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; color: #fff; font-size: 12px;
}
.wf-title {
  font-size: 12.5px; font-weight: 700; color: var(--c-fg);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;
}
.wf-body { padding: 7px 10px; font-size: 11px; color: var(--c-secondary); min-height: 20px; }

.wf-handle { width: 9px; height: 9px; border: 2px solid var(--c-border-strong, #d8cdbb); background: var(--c-panel); }
.wf-node:hover .wf-handle { border-color: var(--c-accent); }
.wf-handle-true { border-color: var(--c-success) !important; }
.wf-handle-false { border-color: var(--c-danger) !important; }
</style>
