<script setup>
import { ref } from 'vue'

const props = defineProps({
  node: { type: Object, required: true },
  expanded: { type: Set, required: true },
  selectedId: { type: String, default: '' },
  showActions: { type: Boolean, default: true },
  draggingNodeId: { type: String, default: '' },
  dragOverNodeId: { type: String, default: '' }
})

const emit = defineEmits(['toggle', 'select', 'edit', 'delete', 'dragstart', 'dragend', 'dragover', 'dragleave', 'drop'])
const showMenu = ref(false)
const menuPosition = ref({ x: 0, y: 0 })

function handleDragStart(e) {
  e.dataTransfer.setData('application/json', JSON.stringify({ id: props.node.id, name: props.node.name }))
  e.dataTransfer.effectAllowed = 'move'
  emit('dragstart', props.node)
}

function handleDragEnd() {
  emit('dragend')
}

function handleDragOver(e) {
  e.preventDefault()
  e.stopPropagation()
  e.dataTransfer.dropEffect = 'move'
  emit('dragover', props.node)
}

function handleDragLeave() {
  emit('dragleave', props.node)
}

function handleDrop(e) {
  e.preventDefault()
  e.stopPropagation()
  emit('drop', props.node)
}

function handleContextMenu(e) {
  if (!props.showActions) return
  e.preventDefault()
  menuPosition.value = { x: e.clientX, y: e.clientY }
  showMenu.value = true
  document.addEventListener('click', closeMenu)
}

function closeMenu() {
  showMenu.value = false
  document.removeEventListener('click', closeMenu)
}

function handleEdit() {
  emit('edit', props.node)
  closeMenu()
}

function handleDelete() {
  emit('delete', props.node)
  closeMenu()
}
</script>

<template>
  <div class="folder-tree-node">
    <div
      :class="['folder-item', { active: selectedId === node.id, dragging: draggingNodeId === node.id, 'drag-over': dragOverNodeId === node.id }]"
      @click="emit('select', node.id)"
      @contextmenu="handleContextMenu"
      :draggable="showActions"
      @dragstart="handleDragStart"
      @dragend="handleDragEnd"
      @dragover="handleDragOver"
      @dragleave="handleDragLeave"
      @drop="handleDrop"
    >
      <span class="active-marker" aria-hidden="true"></span>
      <button
        v-if="node.children && node.children.length > 0"
        class="expand-toggle"
        @click.stop="emit('toggle', node.id)"
        :class="{ expanded: expanded.has(node.id) }"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="9 18 15 12 9 6"/>
        </svg>
      </button>
      <span v-else class="expand-placeholder"></span>
      
      <div class="icon-wrapper">
        <svg class="item-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        </svg>
        <span v-if="(node.file_count || 0) > 0" class="file-badge">{{ node.file_count }}</span>
      </div>
      
      <span class="item-name" :title="node.name">{{ node.name }}</span>
      <span v-if="selectedId === node.id" class="selected-badge">当前</span>
      
      <div v-if="showActions" class="item-actions">
        <button class="action-btn edit-btn" @click.stop="emit('edit', node)" title="编辑">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
        <button class="action-btn delete-btn" @click.stop="emit('delete', node)" title="删除">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 6h18"/>
            <path d="M8 6V4h8v2"/>
            <path d="M19 6l-1 14H6L5 6"/>
          </svg>
        </button>
      </div>
    </div>
    
    <div v-if="node.children && node.children.length > 0 && expanded.has(node.id)" class="children-container">
      <FolderTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :show-actions="showActions"
        :expanded="expanded"
        :selected-id="selectedId"
        :dragging-node-id="draggingNodeId"
        :drag-over-node-id="dragOverNodeId"
        @toggle="emit('toggle', $event)"
        @select="emit('select', $event)"
        @edit="emit('edit', $event)"
        @delete="emit('delete', $event)"
        @dragstart="emit('dragstart', $event)"
        @dragend="emit('dragend')"
        @dragover="emit('dragover', $event)"
        @dragleave="emit('dragleave', $event)"
        @drop="emit('drop', $event)"
      />
    </div>
    
    <!-- 右键菜单 -->
    <Teleport to="body">
      <div
        v-if="showMenu"
        class="context-menu"
        :style="{ left: `${menuPosition.x}px`, top: `${menuPosition.y}px` }"
      >
        <button class="menu-item" @click="handleEdit">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
          编辑文件夹
        </button>
        <button class="menu-item danger" @click="handleDelete">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 6h18"/>
            <path d="M8 6V4h8v2"/>
            <path d="M19 6l-1 14H6L5 6"/>
          </svg>
          删除文件夹
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.folder-tree-node {
  display: flex;
  flex-direction: column;
}

.folder-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 10px;
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
  position: relative;
  color: var(--c-secondary);
}

.folder-item:hover {
  background-color: var(--c-muted);
  color: var(--c-fg);
  transform: translateX(2px);
}

.folder-item.active {
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.18), rgba(245, 158, 11, 0.08));
  border-color: rgba(245, 158, 11, 0.28);
  box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.08);
  color: #fff3d6;
}

.active-marker {
  width: 3px;
  align-self: stretch;
  border-radius: 999px;
  background: transparent;
  flex-shrink: 0;
}

.folder-item.active .active-marker {
  background: linear-gradient(180deg, #f59e0b, #fcd34d);
  box-shadow: 0 0 12px rgba(245, 158, 11, 0.45);
}

.expand-toggle {
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  color: var(--c-secondary);
  transition: all 0.15s ease;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
}

.expand-toggle:hover {
  background-color: var(--c-muted);
}

.expand-toggle svg {
  transition: transform 0.2s ease;
}

.expand-toggle.expanded svg {
  transform: rotate(90deg);
}

.expand-placeholder {
  width: 20px;
  flex-shrink: 0;
}

.icon-wrapper {
  position: relative;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.item-icon {
  width: 16px;
  height: 16px;
  color: #f59e0b;
}

.file-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 14px;
  height: 14px;
  padding: 0 4px;
  background-color: #374151;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  border: 1px solid #4b5563;
}

.item-name {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
}

.selected-badge {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: #fde68a;
  font-size: 11px;
  font-weight: 700;
}

.item-actions {
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.folder-item:hover .item-actions {
  opacity: 1;
}

.action-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  color: var(--c-secondary);
  transition: all 0.15s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.action-btn:hover {
  background-color: var(--c-border);
  color: var(--c-fg);
}

.delete-btn:hover {
  background-color: rgba(239, 68, 68, 0.1);
  color: var(--c-danger);
}

.children-container {
  margin-left: 16px;
}

.context-menu {
  position: fixed;
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 8px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
  padding: 4px;
  z-index: 1000;
  min-width: 160px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: none;
  cursor: pointer;
  border-radius: 4px;
  color: var(--c-fg);
  font-size: 13px;
  transition: background-color 0.15s ease;
  text-align: left;
}

.menu-item:hover {
  background-color: var(--c-muted);
}

.menu-item.danger {
  color: var(--c-danger);
}

.menu-item.danger:hover {
  background-color: rgba(239, 68, 68, 0.1);
}

/* Drag and Drop styles */
.folder-item[draggable="true"] {
  cursor: grab;
}

.folder-item[draggable="true"]:active {
  cursor: grabbing;
}

.folder-item.dragging {
  opacity: 0.5;
  background-color: var(--c-muted);
}

.folder-item.drag-over {
  background-color: var(--c-accent-muted);
  border-left: 2px solid var(--c-accent);
}

.folder-item.drag-over .item-icon {
  color: var(--c-accent);
}
</style>
