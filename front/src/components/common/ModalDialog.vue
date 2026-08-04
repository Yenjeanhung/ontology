<script setup>
import { onMounted, onBeforeUnmount, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  title: {
    type: String,
    default: '',
  },
  // 'sm' | 'md' | 'lg'
  size: {
    type: String,
    default: 'md',
  },
  // 点击遮罩是否关闭
  closeOnMask: {
    type: Boolean,
    default: true,
  },
  // 是否显示右上角关闭按钮
  showClose: {
    type: Boolean,
    default: true,
  },
  // 确认按钮文本（提供则显示底部默认操作栏）
  confirmText: {
    type: String,
    default: '',
  },
  cancelText: {
    type: String,
    default: '取消',
  },
  confirmLoading: {
    type: Boolean,
    default: false,
  },
  // 'default' | 'danger'
  confirmVariant: {
    type: String,
    default: 'default',
  },
  // 禁用确认按钮
  confirmDisabled: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue', 'close', 'confirm', 'cancel'])

function close() {
  emit('update:modelValue', false)
  emit('close')
}

function onMaskClick() {
  if (props.closeOnMask) close()
}

function onCancel() {
  emit('cancel')
  close()
}

function onConfirm() {
  if (props.confirmDisabled || props.confirmLoading) return
  emit('confirm')
}

function handleEsc(e) {
  if (e.key === 'Escape' && props.modelValue) {
    close()
  }
}

onMounted(() => {
  document.addEventListener('keydown', handleEsc)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleEsc)
})

// 打开时锁定 body 滚动
watch(() => props.modelValue, (val) => {
  if (typeof document === 'undefined') return
  document.body.style.overflow = val ? 'hidden' : ''
})

onBeforeUnmount(() => {
  if (typeof document !== 'undefined') document.body.style.overflow = ''
})
</script>

<template>
  <transition name="md-fade">
    <div v-if="modelValue" class="md-mask" @click.self="onMaskClick">
      <div class="md-dialog" :class="`size-${size}`" @click.stop>
        <div v-if="title || showClose" class="md-header">
          <h3 class="md-title">{{ title }}</h3>
          <button v-if="showClose" type="button" class="md-close" @click="close" aria-label="关闭">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        <div class="md-body">
          <slot></slot>
        </div>

        <div v-if="$slots.footer || confirmText" class="md-footer">
          <slot name="footer">
            <button class="btn" @click="onCancel" :disabled="confirmLoading">{{ cancelText }}</button>
            <button
              class="btn primary"
              :class="{ danger: confirmVariant === 'danger' }"
              @click="onConfirm"
              :disabled="confirmDisabled || confirmLoading"
            >
              <span v-if="confirmLoading" class="spinner"></span>
              {{ confirmText }}
            </button>
          </slot>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.md-mask {
  position: fixed;
  inset: 0;
  background: var(--c-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: 20px;
}

.md-dialog {
  background: var(--c-panel);
  border-radius: var(--radius);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.18);
  width: 100%;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.md-dialog.size-sm { max-width: 380px; }
.md-dialog.size-md { max-width: 560px; }
.md-dialog.size-lg { max-width: 760px; }

.md-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--c-border);
  flex-shrink: 0;
}

.md-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--c-fg);
  margin: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.md-close {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--c-secondary);
  cursor: pointer;
  transition: background 150ms, color 150ms;
}

.md-close:hover {
  background: var(--c-muted);
  color: var(--c-fg);
}

.md-body {
  padding: 20px;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

.md-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid var(--c-border);
  flex-shrink: 0;
}

.md-fade-enter-active,
.md-fade-leave-active {
  transition: opacity 180ms ease;
}

.md-fade-enter-active .md-dialog,
.md-fade-leave-active .md-dialog {
  transition: transform 180ms ease, opacity 180ms ease;
}

.md-fade-enter-from,
.md-fade-leave-to {
  opacity: 0;
}

.md-fade-enter-from .md-dialog,
.md-fade-leave-to .md-dialog {
  transform: translateY(-12px) scale(0.98);
  opacity: 0;
}

.btn.primary.danger {
  background: var(--c-danger);
  border-color: var(--c-danger);
  color: #fff;
}

.btn.primary.danger:hover {
  opacity: 0.88;
  background: var(--c-danger);
}

:root[data-theme='dark'] .btn.primary.danger {
  background: var(--c-danger);
  border-color: var(--c-danger);
  color: #fff;
}
</style>
