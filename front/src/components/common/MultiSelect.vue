<script setup>
/**
 * 通用多选下拉（chips 触发器 + 复选面板），替代原生 <select multiple>。
 * 面板 Teleport 到 body 并 fixed 定位，不会被弹窗等 overflow 容器裁剪；
 * 下方空间不足时自动向上翻转。默认带搜索框。
 * props:
 *  - modelValue: 选中值数组
 *  - options:    [{ value, label, desc? }]
 *  - searchable: 是否显示搜索框（默认开）
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '点击选择' },
  searchable: { type: Boolean, default: true },
  emptyText: { type: String, default: '暂无可选项' },
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const keyword = ref('')
const root = ref(null)
const triggerRef = ref(null)
const panelRef = ref(null)
const panelStyle = ref({})
const dropUp = ref(false)

const selectedSet = computed(() => new Set(props.modelValue))
const selectedOptions = computed(() => props.options.filter((o) => selectedSet.value.has(o.value)))
const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return props.options
  return props.options.filter(
    (o) => (o.label || '').toLowerCase().includes(kw) || (o.desc || '').toLowerCase().includes(kw)
  )
})

function toggle(value) {
  const next = new Set(props.modelValue)
  next.has(value) ? next.delete(value) : next.add(value)
  emit('update:modelValue', [...next])
}
function remove(value) {
  emit('update:modelValue', props.modelValue.filter((v) => v !== value))
}
function selectAll() {
  const merged = new Set(props.modelValue)
  filtered.value.forEach((o) => merged.add(o.value))
  emit('update:modelValue', [...merged])
}

function toggleOpen() {
  open.value = !open.value
  if (open.value) nextTick(updatePosition)
}

/* 面板 fixed 定位：对齐触发器，下方放不下则向上翻转 */
function updatePosition() {
  const el = triggerRef.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const gap = 5
  const need = Math.min(panelRef.value?.offsetHeight || 300, 300)
  const below = window.innerHeight - r.bottom
  dropUp.value = below < need && r.top > below
  panelStyle.value = {
    left: `${Math.round(r.left)}px`,
    width: `${Math.round(r.width)}px`,
    ...(dropUp.value
      ? { bottom: `${Math.round(window.innerHeight - r.top + gap)}px` }
      : { top: `${Math.round(r.bottom + gap)}px` }),
  }
}

function onDocMousedown(e) {
  if (!open.value) return
  const t = e.target
  if (root.value?.contains(t) || panelRef.value?.contains(t)) return
  open.value = false
}
function onKeydown(e) {
  if (e.key === 'Escape') open.value = false
}
function onScrollOrResize() {
  if (open.value) updatePosition()
}
onMounted(() => {
  document.addEventListener('mousedown', onDocMousedown)
  document.addEventListener('keydown', onKeydown)
  document.addEventListener('scroll', onScrollOrResize, true)
  window.addEventListener('resize', onScrollOrResize)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMousedown)
  document.removeEventListener('keydown', onKeydown)
  document.removeEventListener('scroll', onScrollOrResize, true)
  window.removeEventListener('resize', onScrollOrResize)
})
</script>

<template>
  <div ref="root" class="ms" :class="{ 'is-open': open }">
    <!-- 触发器：已选项 chips -->
    <button ref="triggerRef" type="button" class="ms-trigger" @click="toggleOpen">
      <span v-if="selectedOptions.length" class="ms-tags">
        <span v-for="o in selectedOptions.slice(0, 8)" :key="o.value" class="ms-tag">
          {{ o.label }}
          <i class="ms-x" title="移除" @click.stop="remove(o.value)">
            <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </i>
        </span>
        <span v-if="selectedOptions.length > 8" class="ms-more">+{{ selectedOptions.length - 8 }}</span>
      </span>
      <span v-else class="ms-placeholder">{{ placeholder }}</span>
      <svg class="ms-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
    </button>

    <!-- 下拉面板：Teleport 到 body，避免被弹窗滚动区裁剪 -->
    <Teleport to="body">
      <div v-if="open" ref="panelRef" class="ms-panel" :class="{ 'is-up': dropUp }" :style="panelStyle">
        <div v-if="searchable" class="ms-search">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
          <input v-model="keyword" placeholder="搜索…" />
        </div>
        <div class="ms-bar">
          <span class="ms-count">已选 {{ modelValue.length }} / {{ options.length }}</span>
          <span class="ms-actions">
            <button type="button" @click="selectAll">全选</button>
            <button type="button" :disabled="!modelValue.length" @click="emit('update:modelValue', [])">清空</button>
          </span>
        </div>
        <div class="ms-list">
          <label v-for="o in filtered" :key="o.value" class="ms-opt" :class="{ on: selectedSet.has(o.value) }">
            <input type="checkbox" :checked="selectedSet.has(o.value)" @change="toggle(o.value)" />
            <span class="ms-check">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
            </span>
            <span class="ms-text">
              {{ o.label }}
              <small v-if="o.desc">{{ o.desc }}</small>
            </span>
          </label>
          <div v-if="!filtered.length" class="ms-empty">{{ emptyText }}</div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.ms { position: relative; width: 100%; }

/* ── 触发器 ── */
.ms-trigger {
  display: flex; align-items: flex-start; gap: 6px; width: 100%; min-height: 34px;
  padding: 4px 28px 4px 8px; text-align: left; cursor: pointer;
  background: var(--c-bg); color: var(--c-fg); font-family: var(--font); font-size: 12.5px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  transition: border-color 140ms, box-shadow 140ms;
  position: relative;
}
.ms-trigger:hover { border-color: color-mix(in srgb, var(--c-accent) 55%, var(--c-border)); }
.ms.is-open .ms-trigger {
  border-color: var(--c-accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--c-accent) 18%, transparent);
}
.ms-placeholder { color: var(--c-secondary); align-self: center; }
.ms-tags { display: flex; flex-wrap: wrap; gap: 4px; flex: 1; }
.ms-tag {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 6px;
  font-size: 11.5px; font-weight: 600; line-height: 16px; border-radius: 5px;
  background: color-mix(in srgb, var(--c-accent) 14%, transparent);
  color: var(--c-accent);
  border: 1px solid color-mix(in srgb, var(--c-accent) 30%, transparent);
}
.ms-x {
  display: inline-flex; align-items: center; justify-content: center;
  width: 13px; height: 13px; border-radius: 3px; cursor: pointer;
  color: inherit; opacity: 0.65; font-style: normal;
}
.ms-x:hover { opacity: 1; background: color-mix(in srgb, var(--c-accent) 26%, transparent); }
.ms-more { align-self: center; font-size: 11.5px; color: var(--c-secondary); padding: 0 2px; }
.ms-chevron {
  position: absolute; right: 9px; top: 50%; transform: translateY(-50%);
  color: var(--c-secondary); transition: transform 160ms; pointer-events: none;
}
.ms.is-open .ms-chevron { transform: translateY(-50%) rotate(180deg); }
</style>

<!-- 面板渲染在 body 下，需用非 scoped 样式（ms- 前缀隔离） -->
<style>
.ms-panel {
  position: fixed; z-index: 1000;
  background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  box-shadow: 0 14px 38px rgba(0, 0, 0, 0.35); overflow: hidden;
  animation: ms-drop-in 130ms ease-out;
}
.ms-panel.is-up { animation-name: ms-drop-in-up; }
@keyframes ms-drop-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes ms-drop-in-up {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.ms-search {
  display: flex; align-items: center; gap: 7px; padding: 8px 10px;
  border-bottom: 1px solid var(--c-border); color: var(--c-secondary);
}
.ms-search input {
  flex: 1; border: none; outline: none; background: transparent;
  color: var(--c-fg); font-size: 12.5px; font-family: var(--font);
}
.ms-bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 10px; border-bottom: 1px solid var(--c-border); background: var(--c-muted);
}
.ms-count { font-size: 11.5px; color: var(--c-secondary); }
.ms-actions { display: flex; gap: 4px; }
.ms-actions button {
  border: none; background: transparent; color: var(--c-accent); cursor: pointer;
  font-size: 11.5px; font-family: var(--font); padding: 1px 5px; border-radius: 4px;
}
.ms-actions button:hover { background: color-mix(in srgb, var(--c-accent) 12%, transparent); }
.ms-actions button:disabled { color: var(--c-secondary); cursor: not-allowed; background: none; }
.ms-list { max-height: 216px; overflow: auto; padding: 4px; }
.ms-opt {
  display: flex; align-items: center; gap: 8px; padding: 6px 8px;
  border-radius: 5px; cursor: pointer; font-size: 12.5px; color: var(--c-fg);
}
.ms-opt:hover { background: var(--c-muted); }
.ms-opt.on { background: color-mix(in srgb, var(--c-accent) 10%, transparent); }
.ms-opt input { position: absolute; opacity: 0; pointer-events: none; }
.ms-check {
  display: inline-flex; align-items: center; justify-content: center; flex: none;
  width: 14px; height: 14px; border-radius: 4px;
  border: 1.5px solid var(--c-border); color: transparent; background: var(--c-bg);
  transition: all 120ms;
}
.ms-opt.on .ms-check {
  background: var(--c-accent); border-color: var(--c-accent); color: #fff;
}
.ms-text { display: flex; flex-direction: column; line-height: 1.35; min-width: 0; }
.ms-text small { font-size: 11px; color: var(--c-secondary); }
.ms-empty { padding: 18px; text-align: center; font-size: 12px; color: var(--c-secondary); }
</style>
