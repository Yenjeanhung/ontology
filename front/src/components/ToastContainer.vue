<script setup>
import { useToast } from '../composables/useToast'

const { toasts, dismiss } = useToast()

const ICONS = { success: '✓', error: '!', info: 'i' }
</script>

<template>
  <TransitionGroup name="toast" tag="div" class="toast-wrap">
    <div
      v-for="t in toasts"
      :key="t.id"
      class="toast"
      :class="`toast-${t.type}`"
      @click="dismiss(t.id)"
    >
      <span class="toast-icon">{{ ICONS[t.type] }}</span>
      <span class="toast-text">{{ t.text }}</span>
    </div>
  </TransitionGroup>
</template>

<style scoped>
.toast-wrap {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10000;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 280px;
  max-width: 480px;
  padding: 11px 16px;
  border-radius: var(--radius);
  background: var(--c-panel-elevated);
  border: 1px solid var(--c-border);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.22);
  color: var(--c-fg);
  font-size: 13px;
  font-family: var(--font);
  cursor: pointer;
  backdrop-filter: blur(8px);
}
.toast-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
}
.toast-success .toast-icon { background: var(--c-success); }
.toast-error .toast-icon { background: var(--c-danger); }
.toast-info .toast-icon { background: var(--c-accent); }
.toast-error { border-color: var(--c-danger); }

.toast-enter-active,
.toast-leave-active {
  transition: opacity 220ms ease, transform 220ms ease;
}
.toast-enter-from {
  opacity: 0;
  transform: translateY(-12px);
}
.toast-leave-to {
  opacity: 0;
  transform: translateY(-8px) scale(0.98);
}
</style>
