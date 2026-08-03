<script setup>
import { ref } from 'vue'
import { createKb as apiCreateKb } from '../api'

const emit = defineEmits(['close', 'created'])

const name = ref('')
const creating = ref(false)

async function create() {
  const n = name.value.trim()
  if (!n) return
  creating.value = true
  try {
    const kb = await apiCreateKb(n)
    emit('created', kb.id)
  } catch {}
  creating.value = false
}
</script>

<template>
  <div class="modal-mask" @click.self="emit('close')">
    <div class="modal" @click.stop>
      <h3>新建知识库</h3>
      <div class="field">
        <label>名称</label>
        <input type="text" v-model="name" placeholder="例如：项目文档" @keydown.enter="create" autofocus>
      </div>
      <div class="actions">
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" @click="create" :disabled="!name.trim() || creating">{{ creating ? '创建中...' : '创建' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,0.3);
  display: flex; align-items: center; justify-content: center;
  z-index: 100; animation: fadeIn 150ms;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.modal {
  background: var(--c-bg); border-radius: var(--radius); padding: 24px;
  width: 360px; max-width: 90vw; box-shadow: 0 8px 30px rgba(0,0,0,0.12);
}
h3 { font-size: 16px; font-weight: 700; margin-bottom: 16px; }
.field { margin-bottom: 16px; }
.field label { display: block; font-size: 13px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
