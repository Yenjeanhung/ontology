<template>
  <div class="ep-root" ref="rootRef">
    <input
      class="ep-input"
      type="text"
      v-model="q"
      :placeholder="placeholder"
      @focus="open = true"
      @input="open = true"
      @keydown.down.prevent="move(1)"
      @keydown.up.prevent="move(-1)"
      @keydown.enter.prevent="pick(hl)"
      @keydown.esc="open = false"
    />
    <ul v-if="open && options.length" class="ep-list">
      <li
        v-for="(o, i) in options" :key="o.id"
        :class="{ hl: i === hl }"
        @mousedown.prevent="pick(i)"
        @mousemove="hl = i"
      >
        <span class="ep-name">{{ o.name }}</span>
        <span class="ep-type">{{ o.entity_type }}</span>
      </li>
    </ul>
  </div>
</template>

<script setup>
// 远程搜索实体选择器：数据源为分析图（迁入后的 Neo4j），按类别圈定。
import { ref, watch, onBeforeUnmount, onMounted } from 'vue'
import { searchGraphEntities } from '../../api'

const props = defineProps({
  categoryId: { type: String, default: '' },
  modelValue: { type: String, default: '' },   // 选中实体 id
  placeholder: { type: String, default: '输入名称搜索实体…' },
})
const emit = defineEmits(['update:modelValue', 'change'])

const q = ref('')
const open = ref(false)
const hl = ref(0)
const options = ref([])
const rootRef = ref(null)
let timer = null

watch(() => props.categoryId, () => { options.value = []; q.value = ''; open.value = false })

watch(q, (v) => {
  clearTimeout(timer)
  timer = setTimeout(async () => {
    if (!props.categoryId) return
    try {
      options.value = await searchGraphEntities(props.categoryId, (v || '').trim())
      hl.value = 0
    } catch { options.value = [] }
  }, 300)
})

function move(d) {
  if (!options.value.length) return
  hl.value = (hl.value + d + options.value.length) % options.value.length
}

function pick(i) {
  const o = options.value[i]
  if (!o) return
  q.value = o.name
  open.value = false
  emit('update:modelValue', o.id)
  emit('change', o)
}

function onDoc(e) {
  if (rootRef.value && !rootRef.value.contains(e.target)) open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDoc))
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDoc)
  clearTimeout(timer)
})
</script>

<style scoped>
.ep-root { position: relative; display: inline-block; }
.ep-input {
  width: 240px; padding: 5px 10px; font-size: 12.5px; font-family: var(--font);
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg);
}
.ep-input:focus { outline: none; border-color: var(--c-accent); }
.ep-list {
  position: absolute; z-index: 30; top: calc(100% + 4px); left: 0;
  min-width: 280px; max-height: 260px; overflow-y: auto; margin: 0; padding: 4px;
  list-style: none; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); box-shadow: 0 8px 24px rgba(0, 0, 0, .25);
}
.ep-list li {
  display: flex; justify-content: space-between; gap: 10px; align-items: center;
  padding: 6px 8px; border-radius: 4px; cursor: pointer; font-size: 12.5px;
}
.ep-list li.hl { background: rgba(56, 189, 248, .12); }
.ep-name { color: var(--c-fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ep-type { color: var(--c-secondary); font-size: 11px; flex-shrink: 0; }
</style>
