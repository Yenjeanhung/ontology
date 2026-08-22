<script setup>
import { computed } from 'vue'

const props = defineProps({
  // tabs: [{ key, label, badge?, iconSvg? }]
  tabs: {
    type: Array,
    default: () => [],
  },
  modelValue: {
    type: [String, Number],
    default: '',
  },
  // 'underline' | 'pill' 两种风格
  variant: {
    type: String,
    default: 'underline',
  },
})

const emit = defineEmits(['update:modelValue', 'change'])

const activeKey = computed(() => props.modelValue)

function selectTab(tab) {
  if (tab.disabled) return
  if (tab.key === activeKey.value) return
  emit('update:modelValue', tab.key)
  emit('change', tab.key)
}
</script>

<template>
  <div class="tab-nav" :class="`v-${variant}`">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      type="button"
      class="tab-item"
      :class="{ active: tab.key === activeKey, disabled: tab.disabled }"
      :disabled="tab.disabled"
      @click="selectTab(tab)"
    >
      <span v-if="tab.iconSvg" class="tab-icon" v-html="tab.iconSvg"></span>
      <span class="tab-label">{{ tab.label }}</span>
      <span v-if="tab.badge != null && tab.badge !== ''" class="tab-badge">{{ tab.badge }}</span>
    </button>
  </div>
</template>

<style scoped>
.tab-nav {
  display: flex;
  align-items: center;
  gap: 4px;
  border-bottom: 1px solid var(--c-border);
  padding: 0 4px;
}

.tab-nav.v-pill {
  border-bottom: 0;
  gap: 6px;
  background: var(--c-muted);
  padding: 4px;
  border-radius: var(--radius);
  display: inline-flex;
}

.tab-item {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border: 0;
  background: transparent;
  color: var(--c-secondary);
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font);
  cursor: pointer;
  white-space: nowrap;
  transition: color 150ms, background 150ms;
}

.tab-item:hover:not(.disabled):not(.active) {
  color: var(--c-fg);
}

.tab-item.active {
  color: var(--c-fg);
}

.tab-item.disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* underline 风格：激活项底部带下划线 */
.v-underline .tab-item.active::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: -1px;
  height: 2px;
  background: var(--c-fg);
  border-radius: 2px 2px 0 0;
}

/* pill 风格：激活项带背景 */
.v-pill .tab-item {
  border-radius: var(--radius-sm);
}

.v-pill .tab-item.active {
  background: var(--c-panel);
  color: var(--c-fg);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.tab-icon {
  display: inline-flex;
  align-items: center;
  color: currentColor;
}

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--c-muted-hover);
  color: var(--c-secondary);
  font-size: 11px;
  font-weight: 700;
}

.tab-item.active .tab-badge {
  background: var(--c-fg);
  color: var(--c-bg);
}

.v-pill .tab-badge {
  background: rgba(0, 0, 0, 0.08);
}

.v-pill .tab-item.active .tab-badge {
  background: var(--c-fg);
  color: var(--c-bg);
}

:root:is([data-theme='dark'], [data-theme='platform-dark']) .v-pill .tab-badge {
  background: rgba(255, 255, 255, 0.12);
}
</style>
