<script setup>
import ModalDialog from './ModalDialog.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '确认操作' },
  // 支持 \n 换行
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  cancelText: { type: String, default: '取消' },
  loading: { type: Boolean, default: false },
  // 操作失败的提示（显示在消息下方）
  error: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'confirm', 'cancel'])

function close() {
  if (props.loading) return
  emit('update:modelValue', false)
  emit('cancel')
}

function onConfirm() {
  if (props.loading) return
  emit('confirm')
}
</script>

<template>
  <ModalDialog
    :model-value="modelValue"
    :title="title"
    size="sm"
    :show-close="false"
    @update:model-value="(v) => emit('update:modelValue', v)"
    @cancel="close"
  >
    <div class="confirm-body">
      <div class="confirm-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      </div>
      <div class="confirm-message">{{ message }}</div>
      <div v-if="error" class="confirm-error">{{ error }}</div>
    </div>
    <template #footer>
      <button class="btn" :disabled="loading" @click="close">{{ cancelText }}</button>
      <button class="btn cd-danger" :disabled="loading" @click="onConfirm">
        <span v-if="loading" class="spinner"></span>
        {{ confirmText }}
      </button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.confirm-body { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 6px 4px 2px; text-align: center; }
.confirm-icon { width: 44px; height: 44px; border-radius: 50%; background: rgba(220, 38, 38, 0.12); color: var(--c-danger); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.confirm-message { font-size: 13.5px; color: var(--c-fg); line-height: 1.7; white-space: pre-line; word-break: break-word; }
.confirm-error { font-size: 12px; color: var(--c-danger); background: rgba(220, 38, 38, 0.08); border-radius: var(--radius-sm); padding: 6px 10px; width: 100%; box-sizing: border-box; }
.btn.cd-danger { background: var(--c-danger); border-color: var(--c-danger); color: #fff; }
.btn.cd-danger:hover { background: var(--c-danger); opacity: 0.88; }
.spinner { width: 12px; height: 12px; border: 2px solid rgba(255, 255, 255, 0.35); border-top-color: #fff; border-radius: 50%; animation: cd-spin 0.7s linear infinite; }
@keyframes cd-spin { to { transform: rotate(360deg); } }
</style>
