<script setup>
// 技能分组递归树节点：结构复刻 FolderTreeNode，去掉拖拽 / 右键菜单 / Teleport，
// hover 操作改为 ＋（建子分组）✎（重命名/移动）🗑（删除）。
defineProps({
  node: { type: Object, required: true },   // { id, name, count, children }
  selectedKey: { type: String, default: '' },
  expanded: { type: Set, required: true },
})

const emit = defineEmits(['select', 'toggle', 'create-child', 'rename', 'remove'])
</script>

<template>
  <div class="sgt-node">
    <div
      class="sgt-item"
      :class="{ active: selectedKey === node.id }"
      @click="emit('select', node.id)"
    >
      <button
        v-if="node.children && node.children.length"
        class="sgt-toggle"
        :class="{ expanded: expanded.has(node.id) }"
        @click.stop="emit('toggle', node.id)"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
      </button>
      <span v-else class="sgt-toggle-placeholder"></span>

      <span class="sgt-name" :title="node.name">{{ node.name }}</span>
      <span class="sgt-count">{{ node.count }}</span>

      <div class="sgt-actions">
        <button class="sgt-action" @click.stop="emit('create-child', node)" title="新建子分组">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
        </button>
        <button class="sgt-action" @click.stop="emit('rename', node)" title="重命名 / 移动">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="sgt-action danger" @click.stop="emit('remove', node)" title="删除分组">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>
        </button>
      </div>
    </div>

    <div v-if="node.children && node.children.length && expanded.has(node.id)" class="sgt-children">
      <SkillGroupTree
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :selected-key="selectedKey"
        :expanded="expanded"
        @select="emit('select', $event)"
        @toggle="emit('toggle', $event)"
        @create-child="emit('create-child', $event)"
        @rename="emit('rename', $event)"
        @remove="emit('remove', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.sgt-node {
  display: flex;
  flex-direction: column;
}

.sgt-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 8px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--c-secondary);
  transition: background-color 120ms, color 120ms;
}

.sgt-item:hover {
  background: var(--c-muted);
  color: var(--c-fg);
}

.sgt-item.active {
  background: color-mix(in srgb, var(--c-accent) 14%, var(--c-panel));
  color: var(--c-fg);
}

.sgt-toggle {
  background: none;
  border: none;
  cursor: pointer;
  padding: 1px;
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  color: var(--c-secondary);
  flex-shrink: 0;
}

.sgt-toggle:hover {
  background: var(--c-border);
}

.sgt-toggle svg {
  transition: transform 0.15s ease;
}

.sgt-toggle.expanded svg {
  transform: rotate(90deg);
}

.sgt-toggle-placeholder {
  width: 16px;
  flex-shrink: 0;
}

.sgt-name {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
}

.sgt-count {
  flex-shrink: 0;
  min-width: 16px;
  padding: 0 5px;
  font-size: 10px;
  font-weight: 600;
  line-height: 16px;
  text-align: center;
  border-radius: 8px;
  background: var(--c-muted);
  color: var(--c-secondary);
}

.sgt-item.active .sgt-count {
  background: color-mix(in srgb, var(--c-accent) 22%, transparent);
  color: var(--c-accent);
}

.sgt-actions {
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 120ms;
}

.sgt-item:hover .sgt-actions {
  opacity: 1;
}

.sgt-action {
  background: none;
  border: none;
  cursor: pointer;
  padding: 3px;
  border-radius: 4px;
  color: var(--c-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 120ms;
}

.sgt-action:hover {
  background: var(--c-border);
  color: var(--c-fg);
}

.sgt-action.danger:hover {
  background: rgba(239, 68, 68, 0.1);
  color: var(--c-danger);
}

.sgt-children {
  margin-left: 16px;
}
</style>
