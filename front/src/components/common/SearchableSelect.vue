<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'

const props = defineProps({
  modelValue: {
    type: [String, Array, null],
    default: null,
  },
  options: {
    type: Array,
    default: () => [],
    // 每项：{ value, label, meta?, disabled? }
  },
  multiple: {
    type: Boolean,
    default: false,
  },
  searchable: {
    type: Boolean,
    default: true,
  },
  placeholder: {
    type: String,
    default: '请选择...',
  },
  emptyText: {
    type: String,
    default: '无选项',
  },
  searchPlaceholder: {
    type: String,
    default: '搜索...',
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  // 用于在 trigger 中显示的图标 (SVG 字符串)
  iconSvg: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['update:modelValue', 'change'])

const rootRef = ref(null)
const searchInputRef = ref(null)
const open = ref(false)
const query = ref('')

const selectedValues = computed(() => {
  if (props.multiple) {
    return Array.isArray(props.modelValue) ? props.modelValue : []
  }
  return props.modelValue ? [props.modelValue] : []
})

const filtered = computed(() => {
  const q = query.value.toLowerCase().trim()
  if (!q) return props.options
  return props.options.filter(opt => {
    const label = String(opt.label || '').toLowerCase()
    const meta = String(opt.meta || '').toLowerCase()
    return label.includes(q) || meta.includes(q)
  })
})

const selectedLabels = computed(() => {
  return selectedValues.value.map(v => {
    const opt = props.options.find(o => o.value === v)
    return opt ? opt.label : v
  })
})

const triggerText = computed(() => {
  if (selectedValues.value.length === 0) return props.placeholder
  if (props.multiple) {
    if (selectedValues.value.length === 1) return selectedLabels.value[0]
    return `已选 ${selectedValues.value.length} 项`
  }
  return selectedLabels.value[0] || props.placeholder
})

const isPlaceholder = computed(() => selectedValues.value.length === 0)

function toggleOpen() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    query.value = ''
    if (props.searchable) {
      nextTick(() => {
        searchInputRef.value?.focus()
      })
    }
  }
}

function close() {
  open.value = false
  query.value = ''
}

function isSelected(value) {
  return selectedValues.value.includes(value)
}

function selectOption(opt) {
  if (opt.disabled) return
  if (props.multiple) {
    const current = Array.isArray(props.modelValue) ? [...props.modelValue] : []
    const idx = current.indexOf(opt.value)
    if (idx >= 0) {
      current.splice(idx, 1)
    } else {
      current.push(opt.value)
    }
    emit('update:modelValue', current)
    emit('change', current)
  } else {
    emit('update:modelValue', opt.value)
    emit('change', opt.value)
    close()
  }
}

function clearAll(e) {
  e.stopPropagation()
  if (props.multiple) {
    emit('update:modelValue', [])
    emit('change', [])
  } else {
    emit('update:modelValue', null)
    emit('change', null)
  }
}

function handleClickOutside(e) {
  if (rootRef.value && !rootRef.value.contains(e.target)) {
    close()
  }
}

function handleEsc(e) {
  if (e.key === 'Escape' && open.value) {
    close()
  }
}

onMounted(() => {
  document.addEventListener('mousedown', handleClickOutside)
  document.addEventListener('keydown', handleEsc)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleClickOutside)
  document.removeEventListener('keydown', handleEsc)
})

watch(() => props.options, () => {
  // 当选项变化时，过滤掉已不在选项中的选中值（保持一致性）
  if (props.multiple) {
    const validValues = props.options.map(o => o.value)
    const current = Array.isArray(props.modelValue) ? props.modelValue : []
    const filtered = current.filter(v => validValues.includes(v))
    if (filtered.length !== current.length) {
      emit('update:modelValue', filtered)
    }
  }
}, { deep: true })
</script>

<template>
  <div class="ss-root" :class="{ disabled, open }" ref="rootRef">
    <button
      type="button"
      class="ss-trigger"
      :disabled="disabled"
      @click="toggleOpen"
    >
      <span v-if="iconSvg" class="ss-trigger-icon" v-html="iconSvg"></span>
      <span class="ss-trigger-text" :class="{ placeholder: isPlaceholder }">
        {{ triggerText }}
      </span>
      <span class="ss-trigger-actions">
        <button
          v-if="selectedValues.length > 0 && !disabled"
          type="button"
          class="ss-clear"
          title="清除"
          @click="clearAll"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
        <svg class="ss-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
      </span>
    </button>

    <transition name="ss-dropdown">
      <div v-if="open" class="ss-dropdown">
        <div v-if="searchable" class="ss-search-wrap">
          <svg class="ss-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <input
            ref="searchInputRef"
            type="text"
            v-model="query"
            :placeholder="searchPlaceholder"
            class="ss-search-input"
          />
        </div>

        <div class="ss-options">
          <button
            v-for="opt in filtered"
            :key="opt.value"
            type="button"
            class="ss-option"
            :class="{ active: isSelected(opt.value), disabled: opt.disabled }"
            :disabled="opt.disabled"
            @click="selectOption(opt)"
          >
            <span v-if="multiple" class="ss-check" :class="{ checked: isSelected(opt.value) }">
              <svg v-if="isSelected(opt.value)" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            </span>
            <span v-else class="ss-radio" :class="{ checked: isSelected(opt.value) }"></span>
            <span class="ss-option-body">
              <span class="ss-option-label">{{ opt.label }}</span>
              <span v-if="opt.meta" class="ss-option-meta">{{ opt.meta }}</span>
            </span>
          </button>

          <div v-if="filtered.length === 0" class="ss-empty">
            {{ query ? '无匹配项' : emptyText }}
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.ss-root {
  position: relative;
  width: 100%;
}

.ss-trigger {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-panel);
  color: var(--c-fg);
  font-size: 14px;
  font-family: var(--font);
  text-align: left;
  cursor: pointer;
  min-height: 38px;
  transition: border-color 150ms, background 150ms;
}

.ss-trigger:hover {
  border-color: var(--c-fg);
}

.ss-root.open .ss-trigger {
  border-color: var(--c-fg);
}

.ss-root.disabled .ss-trigger {
  opacity: 0.5;
  cursor: not-allowed;
}

.ss-trigger-icon {
  display: inline-flex;
  align-items: center;
  color: var(--c-secondary);
  flex-shrink: 0;
}

.ss-trigger-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--c-fg);
}

.ss-trigger-text.placeholder {
  color: var(--c-secondary);
  opacity: 0.7;
}

.ss-trigger-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.ss-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: 50%;
  background: var(--c-muted);
  color: var(--c-secondary);
  cursor: pointer;
  padding: 0;
  transition: background 150ms, color 150ms;
}

.ss-clear:hover {
  background: var(--c-muted-hover);
  color: var(--c-fg);
}

.ss-caret {
  color: var(--c-secondary);
  transition: transform 180ms ease;
}

.ss-root.open .ss-caret {
  transform: rotate(180deg);
}

.ss-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  z-index: 50;
  padding: 6px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  background: var(--c-panel-elevated);
  box-shadow: 0 18px 40px rgba(23, 23, 23, 0.12);
  backdrop-filter: blur(10px);
  max-height: 320px;
  display: flex;
  flex-direction: column;
}

.ss-dropdown-enter-active,
.ss-dropdown-leave-active {
  transition: opacity 150ms ease, transform 150ms ease;
}

.ss-dropdown-enter-from,
.ss-dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.ss-search-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  margin-bottom: 4px;
  border-bottom: 1px solid var(--c-border);
}

.ss-search-icon {
  color: var(--c-secondary);
  flex-shrink: 0;
}

.ss-search-input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--c-fg);
  font-size: 13px;
  font-family: var(--font);
  padding: 2px 0;
}

.ss-search-input::placeholder {
  color: var(--c-secondary);
  opacity: 0.7;
}

.ss-options {
  overflow-y: auto;
  flex: 1;
  padding: 2px;
}

.ss-option {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--c-fg);
  font-size: 13px;
  font-family: var(--font);
  text-align: left;
  cursor: pointer;
  transition: background 150ms;
}

.ss-option:hover:not(.disabled) {
  background: var(--c-muted);
}

.ss-option.active {
  background: var(--c-muted-hover);
  font-weight: 600;
}

.ss-option.disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.ss-check,
.ss-radio {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1.5px solid var(--c-border);
  background: var(--c-panel);
  color: var(--c-fg);
}

.ss-check {
  width: 16px;
  height: 16px;
  border-radius: 4px;
}

.ss-check.checked {
  background: var(--c-fg);
  border-color: var(--c-fg);
}

.ss-radio {
  width: 16px;
  height: 16px;
  border-radius: 50%;
}

.ss-radio.checked {
  border-color: var(--c-fg);
  background: radial-gradient(circle, var(--c-fg) 0 5px, transparent 6px);
}

.ss-option-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ss-option-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ss-option-meta {
  font-size: 11px;
  color: var(--c-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ss-empty {
  padding: 20px 10px;
  text-align: center;
  color: var(--c-secondary);
  font-size: 13px;
}
</style>
