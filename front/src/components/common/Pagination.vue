<script setup>
import { computed } from 'vue'

const props = defineProps({
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 10 },
  total: { type: Number, default: 0 },
  pageSizeOptions: { type: Array, default: () => [10, 20, 50, 100] }
})

const emit = defineEmits(['update:page', 'update:pageSize', 'change'])

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const canPrev = computed(() => props.page > 1)
const canNext = computed(() => props.page < totalPages.value && props.total > 0)

function setPage(p) {
  const target = Math.max(1, Math.min(totalPages.value, p))
  if (target === props.page) return
  emit('update:page', target)
  emit('change')
}

function setPageSize(size) {
  const newSize = Number(size)
  if (newSize === props.pageSize) return
  emit('update:pageSize', newSize)
  // 切换每页条数后回到第一页，避免当前页码超出新总页数
  emit('update:page', 1)
  emit('change')
}
</script>

<template>
  <div class="pagination-bar">
    <div class="pagination-info">
      共 {{ total }} 条 / {{ totalPages }} 页
    </div>
    <div class="pagination-controls">
      <select class="page-size-select" :value="pageSize" @change="setPageSize($event.target.value)">
        <option v-for="opt in pageSizeOptions" :key="opt" :value="opt">{{ opt }} 条/页</option>
      </select>
      <button
        class="page-btn"
        :disabled="!canPrev"
        @click="setPage(page - 1)"
      >
        上一页
      </button>
      <span class="page-current">{{ page }} / {{ totalPages }}</span>
      <button
        class="page-btn"
        :disabled="!canNext"
        @click="setPage(page + 1)"
      >
        下一页
      </button>
    </div>
  </div>
</template>

<style scoped>
.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 0;
  margin-top: 8px;
  border-top: 1px solid var(--c-border);
  font-size: 13px;
  color: var(--c-secondary);
}

.pagination-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.page-size-select {
  background: var(--c-bg);
  color: var(--c-fg);
  border: 1px solid var(--c-border);
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 13px;
  cursor: pointer;
  outline: none;
}

.page-size-select:focus {
  border-color: var(--c-primary);
}

.page-btn {
  background: var(--c-bg);
  color: var(--c-fg);
  border: 1px solid var(--c-border);
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 13px;
  cursor: pointer;
  transition: all 150ms;
}

.page-btn:hover:not(:disabled) {
  border-color: var(--c-primary);
  color: var(--c-primary);
}

.page-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.page-current {
  min-width: 56px;
  text-align: center;
  color: var(--c-fg);
  font-weight: 500;
}
</style>
