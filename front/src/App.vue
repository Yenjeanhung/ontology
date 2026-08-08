<script setup>
import { computed, onMounted, onBeforeUnmount, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchConfig, fetchOntologySuggestions } from './api'

const router = useRouter()
const route = useRoute()
const isSidebarCollapsed = ref(false)
const SIDEBAR_STORAGE_KEY = 'knowsource.sidebar.collapsed'
const THEME_STORAGE_KEY = 'knowsource.theme'
const theme = ref('dark')
const vectorProvider = ref('')
const graphProvider = ref('')
const pendingSuggestionCount = ref(0) // 全局待审核建议数

provide('vectorProvider', vectorProvider)
provide('graphProvider', graphProvider)

// 悬浮卡片子菜单：当前展开的分组 key（null = 全部收起）
const openGroup = ref(null)
let openTimer = null
let closeTimer = null

function openGroupMenu(key) {
  clearTimeout(closeTimer)
  clearTimeout(openTimer)
  openTimer = setTimeout(() => { openGroup.value = key }, 70)
}
function closeGroupMenu() {
  clearTimeout(openTimer)
  closeTimer = setTimeout(() => { openGroup.value = null }, 120)
}
function toggleGroupMenu(key) {
  clearTimeout(openTimer)
  clearTimeout(closeTimer)
  openGroup.value = openGroup.value === key ? null : key
}
function onSidePointerDown(e) {
  if (openGroup.value !== null && !e.target.closest('.side-group')) openGroup.value = null
}
function onSideKeydown(e) {
  if (e.key === 'Escape') openGroup.value = null
}

// 路由变化时收起卡片
watch(() => route.path, () => { openGroup.value = null })

// 判断分组是否处于当前路由下（用于高亮分组标题）
function isGroupActive(item) {
  return (item.children || []).some(c => c.to && (route.path === c.to || route.path.startsWith(c.to + '/')))
}

const menuItems = computed(() => [
  {
    key: 'ontology',
    label: '本体',
    hint: '本体管理',
    children: [
      { to: '/ontology/templates', key: 'templates', label: '本体模板', icon: 'template' },
      { to: '/ontology/ontologies', key: 'ontologies', label: '本体管理', icon: 'ontology' },
      { to: '/ontology/relations-dict', key: 'relations-dict', label: '关系字典', icon: 'dict' },
      { to: '/ontology/constraints', key: 'constraints', label: '本体关系', icon: 'triple' },
      { to: '/ontology/suggestions', key: 'suggestions', label: '本体建议', icon: 'suggestion' },
    ],
  },
  { to: '/files', key: 'files', label: '文件', exact: false, hint: '文件管理' },
  { to: '/kb', key: 'kb', label: '知识库', exact: false, hint: '知识库' },
  { to: '/entities', key: 'entities', label: '实体', exact: false, hint: '实体管理' },
  { to: '/graph', key: 'graph', label: '图谱', exact: false, hint: '图谱' },
  { to: '/agent', key: 'agent', label: '智能体', exact: false, hint: '本体增强问答' },
  {
    key: 'data',
    label: '数据',
    hint: '检索与数据',
    children: [
      { to: '/query', key: 'query', label: '知识库检索', icon: 'query' },
      { to: '/vectors', key: 'vectors', label: '向量', icon: 'vector' },
    ],
  },
])

const subIcons = {
  template: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="17.5" height="5" rx="1.25"/><rect x="3.25" y="10.75" width="7.5" height="10" rx="1.25"/><rect x="11.25" y="10.75" width="9.5" height="10" rx="1.25"/></svg>',
  ontology: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 6 7v5c0 3.5 2.5 6.5 6 8 3.5-1.5 6-4.5 6-8V7L12 3.5Z"/></svg>',
  dict: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v16H6.5A2.5 2.5 0 0 0 4 20.5"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20"/><path d="M8 7h6"/><path d="M8 10.5h4"/></svg>',
  triple: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.8 7.5 11 16"/></svg>',
  suggestion: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>',
  vector: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="8" r="1.6"/><circle cx="12" cy="5" r="1.6"/><circle cx="18" cy="9" r="1.6"/><circle cx="9" cy="16" r="1.6"/><circle cx="16" cy="18" r="1.6"/></svg>',
  query: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16.65" y2="16.65"/></svg>',
}

function toggleSidebar() {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
  localStorage.setItem(SIDEBAR_STORAGE_KEY, String(isSidebarCollapsed.value))
}

function applyTheme(nextTheme) {
  theme.value = nextTheme
  document.documentElement.dataset.theme = nextTheme
  localStorage.setItem(THEME_STORAGE_KEY, nextTheme)
}

function toggleTheme() {
  applyTheme(theme.value === 'dark' ? 'light' : 'dark')
}

function isExact(item) {
  return item.exact ? 'is-active' : undefined
}

function goHome() {
  router.push('/')
}

onMounted(() => {
  const saved = localStorage.getItem(SIDEBAR_STORAGE_KEY)
  if (saved !== null) {
    isSidebarCollapsed.value = saved === 'true'
  }

  const savedTheme = localStorage.getItem(THEME_STORAGE_KEY)
  if (savedTheme === 'light' || savedTheme === 'dark') {
    applyTheme(savedTheme)
  } else {
    // 默认使用深色模式
    applyTheme('dark')
  }

  fetchConfig().then(cfg => {
    vectorProvider.value = cfg.vector_provider || ''
    graphProvider.value = cfg.graph_provider || ''
  }).catch(() => {})

  // 全局检查待审核本体建议数
  fetchOntologySuggestions({ status: 'ready' }).then(list => {
    pendingSuggestionCount.value = list.length
  }).catch(() => {})

  window.addEventListener('pointerdown', onSidePointerDown)
  window.addEventListener('keydown', onSideKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', onSidePointerDown)
  window.removeEventListener('keydown', onSideKeydown)
})
</script>

<template>
  <div class="app-shell" :class="{ 'is-collapsed': isSidebarCollapsed }">
    <aside class="sidebar">
      <div class="sidebar-top">
        <button
          class="sidebar-toggle"
          type="button"
          :aria-label="isSidebarCollapsed ? '展开菜单' : '收起菜单'"
          @click="toggleSidebar"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="3.75" y="5" width="16.5" height="14" rx="3" />
            <path d="M9 5v14" />
          </svg>
        </button>
        <button
          v-if="!isSidebarCollapsed"
          class="sidebar-toggle theme-toggle"
          type="button"
          :aria-label="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
          :title="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
          @click="toggleTheme"
        >
          <svg
            v-if="theme === 'dark'"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.85"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="4.25" />
            <path d="M12 2.75v2.1" />
            <path d="M12 19.15v2.1" />
            <path d="m4.93 4.93 1.49 1.49" />
            <path d="m17.58 17.58 1.49 1.49" />
            <path d="M2.75 12h2.1" />
            <path d="M19.15 12h2.1" />
            <path d="m4.93 19.07 1.49-1.49" />
            <path d="m17.58 6.42 1.49-1.49" />
          </svg>
          <svg
            v-else
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.85"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M20.25 14.2A8.25 8.25 0 1 1 9.8 3.75a6.6 6.6 0 0 0 10.45 10.45Z" />
          </svg>
        </button>
      </div>

      <nav class="side-nav">
        <template v-for="item in menuItems" :key="item.key">
          <!-- 有子菜单的分组 -->
          <div v-if="item.children"
               class="side-group"
               :class="{ 'is-open': openGroup === item.key, 'is-active': isGroupActive(item) }"
               @mouseenter="openGroupMenu(item.key)"
               @mouseleave="closeGroupMenu()">
            <button
              class="side-item side-group-toggle"
              :class="{ 'is-active': isGroupActive(item) }"
              type="button"
              :aria-expanded="openGroup === item.key"
              @click="toggleGroupMenu(item.key)"
            >
              <span class="side-icon-wrap" aria-hidden="true">
                <svg v-if="item.key === 'ontology'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="3.25" y="3.25" width="6" height="6" rx="1.5" />
                  <rect x="14.75" y="3.25" width="6" height="6" rx="1.5" />
                  <rect x="9" y="14.75" width="6" height="6" rx="1.5" />
                  <path d="M6.25 9.25v1.75a1.5 1.5 0 0 0 1.5 1.5h1.25" />
                  <path d="M17.75 9.25v1.75a1.5 1.5 0 0 1-1.5 1.5H15.25" />
                </svg>
                <svg v-else width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <ellipse cx="12" cy="5.5" rx="7.5" ry="2.8" />
                  <path d="M4.5 5.5v6c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8v-6" />
                  <path d="M4.5 11.5v6c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8v-6" />
                </svg>
              </span>
              <span class="side-label">{{ item.label }}</span>
              <span class="side-chev" aria-hidden="true">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>
              </span>
            </button>

            <!-- 悬浮卡片子菜单 -->
            <div class="side-flyout">
              <div class="flyout-title">{{ item.hint }}</div>
              <router-link
                v-for="child in item.children"
                :key="child.key"
                :to="child.to"
                class="flyout-item"
                active-class="is-active"
              >
                <span class="flyout-icon" v-html="subIcons[child.icon] || ''"></span>
                <span class="flyout-label">{{ child.label }}</span>
                <span v-if="child.key === 'suggestions' && pendingSuggestionCount > 0" class="nav-badge flyout-badge">{{ pendingSuggestionCount }}</span>
              </router-link>
            </div>
          </div>

          <!-- 普通菜单项 -->
          <router-link
            v-else
            :to="item.to"
            class="side-item"
            active-class="is-active"
            :exact-active-class="isExact(item)"
          >
            <span class="side-icon-wrap" aria-hidden="true">
              <svg v-if="item.key === 'entities'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="8.25" r="4.25" />
                <path d="M4.75 20.25a7.25 7.25 0 0 1 14.5 0" />
              </svg>
              <svg v-else-if="item.key === 'kb'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M4.25 6.75A2.5 2.5 0 0 1 6.75 4.25h10.5a2.5 2.5 0 0 1 2.5 2.5v10.5a2.5 2.5 0 0 1-2.5 2.5H6.75a2.5 2.5 0 0 1-2.5-2.5Z" />
                <path d="M8 8.75h8" />
                <path d="M8 12h8" />
                <path d="M8 15.25h5" />
              </svg>
              <svg v-else-if="item.key === 'files'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3.75 7.25A2.25 2.25 0 0 1 6 5h4.25c.57 0 1.12.22 1.54.62l1.14 1.1c.42.4.97.63 1.55.63H18A2.25 2.25 0 0 1 20.25 9.6v7.15A2.25 2.25 0 0 1 18 19H6a2.25 2.25 0 0 1-2.25-2.25Z" />
                <path d="M3.75 9.25h16.5" />
              </svg>
              <svg v-else-if="item.key === 'query'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 3.25 18.75 7v7.75L12 18.5l-6.75-3.75V7L12 3.25Z" />
                <path d="M9 9.25h6" />
                <path d="M9 12.75h3.5" />
              </svg>
              <svg v-else-if="item.key === 'agent'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 3l1.7 4.8L18.5 9.5l-4.8 1.7L12 16l-1.7-4.8L5.5 9.5l4.8-1.7L12 3z" />
                <path d="M18.5 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z" />
              </svg>
              <svg v-else-if="item.key === 'graph'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="6.5" cy="6.5" r="2.25" />
                <circle cx="17.5" cy="6.5" r="2.25" />
                <circle cx="12" cy="17.5" r="2.25" />
                <path d="M8.75 6.5h6.5" />
                <path d="M8.2 8.1 10.3 15.2" />
                <path d="m15.8 8.1-2.1 7.1" />
              </svg>
              <svg v-else width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <rect x="4.25" y="4.25" width="15.5" height="15.5" rx="2.25" />
                <path d="M8 8.5h8" />
                <path d="M8 12h8" />
                <path d="M8 15.5h5" />
              </svg>
            </span>
            <span class="side-label">{{ item.label }}</span>
            <span class="side-hint">{{ item.hint }}</span>
          </router-link>
        </template>
      </nav>

      <button
        class="side-brand"
        :class="{ 'is-active': route.path === '/' }"
        type="button"
        aria-label="返回首页"
        @click="goHome"
      >
        <span class="side-icon-wrap brand-icon" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2.75 18.5 8.25 12 21.25 5.5 8.25 12 2.75Z" />
            <path d="M12 2.75v18.5" stroke-width="1.1" opacity="0.45" />
            <path d="M8.75 10.25h6.5" stroke-width="1.1" opacity="0.45" />
          </svg>
        </span>
        <span class="brand-text">KnowSource</span>
        <span class="side-hint">首页</span>
      </button>
    </aside>

    <main class="main-area" :class="{ 'home-main': route.path === '/' }">
      <router-view v-slot="{ Component, route }">
        <KeepAlive>
          <component v-if="route.meta.keepAlive" :is="Component" :key="route.params.kbId || route.path" />
        </KeepAlive>
        <component v-if="!route.meta.keepAlive" :is="Component" :key="route.path" />
      </router-view>
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100dvh;
  width: 100%;
  max-width: 100%;
  margin: 0;
  overflow: visible;
}

.sidebar {
  width: 80px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 12px 6px;
  position: sticky;
  top: 0;
  height: 100dvh;
  border-right: 1px solid var(--c-border);
  background: var(--c-panel);
  transition: width 180ms ease, padding 180ms ease;
  overflow: visible;
  z-index: 40;
}

.sidebar-top {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  padding-bottom: 10px;
}

.sidebar-toggle {
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--c-secondary);
  cursor: pointer;
  transition: background 150ms ease, color 150ms ease;
}

.sidebar-toggle:hover {
  background: var(--c-muted);
  color: var(--c-fg);
}

.theme-toggle {
  color: var(--c-accent);
}

.side-nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 8px;
}

.side-item,
.side-brand {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-height: 48px;
  padding: 8px 6px;
  border: 0;
  border-radius: 14px;
  background: transparent;
  color: var(--c-secondary);
  font-family: var(--font);
  text-decoration: none;
  transition: background 150ms ease, color 150ms ease;
}

.side-item:hover,
.side-brand:hover {
  background: var(--c-muted);
  color: var(--c-fg);
}

.side-item.is-active,
.side-brand.is-active {
  background: var(--c-muted);
  color: var(--c-fg);
  font-weight: 600;
}

.side-icon-wrap {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: currentColor;
}

.brand-icon {
  color: var(--c-fg);
}

.side-label,
.brand-text {
  font-size: 11px;
  font-weight: inherit;
  white-space: nowrap;
  text-align: center;
}

.side-brand {
  margin-top: 8px;
  padding-top: 14px;
  cursor: pointer;
  user-select: none;
  border-top: 1px solid var(--c-border);
}

.brand-text {
  font-size: 10px;
  font-weight: 700;
  color: var(--c-fg);
}

.side-hint {
  position: absolute;
  left: calc(100% + 8px);
  top: 50%;
  transform: translateY(-50%) translateX(-4px);
  padding: 7px 10px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-panel-elevated);
  color: var(--c-fg);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  box-shadow: 0 12px 28px rgba(92, 78, 58, 0.12);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity 140ms ease, transform 140ms ease, visibility 140ms ease;
  z-index: 9999;
}

.side-hint::before {
  content: '';
  position: absolute;
  left: -6px;
  top: 50%;
  width: 10px;
  height: 10px;
  border-left: 1px solid var(--c-border);
  border-bottom: 1px solid var(--c-border);
  background: var(--c-panel-elevated);
  transform: translateY(-50%) rotate(45deg);
}

.main-area {
  flex: 1;
  min-width: 0;
  padding: 28px 32px 48px;
  position: relative;
  z-index: 1;
}

.main-area.home-main {
  padding-bottom: 0;
}

.is-collapsed .sidebar {
  width: 64px;
  padding-left: 6px;
  padding-right: 6px;
}

.is-collapsed .sidebar-top {
  justify-content: center;
}

.is-collapsed .side-item,
.is-collapsed .side-brand {
  justify-content: center;
  padding-left: 0;
  padding-right: 0;
}

.is-collapsed .side-label,
.is-collapsed .brand-text {
  display: none;
}

.is-collapsed .side-item:hover .side-hint,
.is-collapsed .side-item:focus-visible .side-hint,
.is-collapsed .side-brand:hover .side-hint,
.is-collapsed .side-brand:focus-visible .side-hint {
  opacity: 1;
  visibility: visible;
  transform: translateY(-50%) translateX(0);
}

/* 二级菜单（悬浮卡片） */
.side-group { position: relative; display: flex; flex-direction: column; }

.side-group-toggle { position: relative; width: 100%; }
.side-group.is-active .side-group-toggle { color: var(--c-fg); }
.side-group.is-active .side-group-toggle::before {
  content: '';
  position: absolute;
  left: 0; top: 50%;
  transform: translateY(-50%);
  width: 3px; height: 20px;
  border-radius: 0 3px 3px 0;
  background: var(--c-accent);
}
.side-chev {
  position: absolute;
  right: 4px; top: 50%;
  transform: translateY(-50%);
  color: var(--c-secondary);
  opacity: 0.5;
  transition: transform 180ms ease, opacity 150ms, color 150ms;
  pointer-events: none;
}
.side-group.is-open .side-chev { transform: translateY(-50%) rotate(90deg); opacity: 1; color: var(--c-accent); }

/* 悬浮卡片 */
.side-flyout {
  position: absolute;
  left: calc(100% + 10px);
  top: -6px;
  min-width: 188px;
  padding: 8px;
  border: 1px solid var(--c-border);
  border-radius: 14px;
  background: var(--c-panel-elevated);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.18);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transform: translateX(-6px);
  transition: opacity 150ms ease, transform 150ms ease, visibility 150ms ease;
  z-index: 9999;
}
/* 悬停桥：避免鼠标穿过缝隙时卡片消失 */
.side-flyout::before {
  content: '';
  position: absolute;
  left: -10px; top: 0; bottom: 0;
  width: 10px;
}
.side-group.is-open .side-flyout {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
  transform: translateX(0);
}
.flyout-title {
  font-size: 11px; font-weight: 700; color: var(--c-secondary);
  padding: 6px 10px 8px; text-transform: uppercase; letter-spacing: 0.3px;
}
.flyout-item {
  position: relative;
  display: flex; align-items: center; gap: 10px;
  padding: 9px 10px; border-radius: 10px;
  font-size: 13px; color: var(--c-fg); text-decoration: none;
  transition: background 120ms, color 120ms;
}
.flyout-item:hover { background: var(--c-muted); }
.flyout-item.is-active { background: var(--c-muted); font-weight: 600; color: var(--c-accent); }
.flyout-icon {
  width: 18px; height: 18px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--c-secondary);
}
.flyout-item.is-active .flyout-icon { color: var(--c-accent); }
.flyout-label { flex: 1; min-width: 0; }

/* 导航徽章 */
.nav-badge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 5px;
  border-radius: 9px; background: #e74c3c; color: #fff;
  font-size: 11px; font-weight: 700; line-height: 1;
  margin-left: auto; flex-shrink: 0;
  animation: badge-pulse 2s ease-in-out infinite;
}
.flyout-badge { position: static; transform: none; }
@keyframes badge-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.is-collapsed .side-chev { display: none; }

@media (max-width: 640px) {
  .sidebar {
    width: 64px;
    padding-left: 6px;
    padding-right: 6px;
  }

  .sidebar-top {
    justify-content: center;
  }

  .side-item,
  .side-brand {
    justify-content: center;
    padding-left: 0;
    padding-right: 0;
  }

  .side-label,
  .brand-text {
    display: none;
  }

  .main-area {
    padding: 20px 16px 40px;
  }

  .main-area.home-main {
    padding-bottom: 0;
  }
}
</style>
