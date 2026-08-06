<script setup>
import { computed, onMounted, provide, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchConfig } from './api'

const router = useRouter()
const route = useRoute()
const isSidebarCollapsed = ref(false)
const SIDEBAR_STORAGE_KEY = 'knowsource.sidebar.collapsed'
const THEME_STORAGE_KEY = 'knowsource.theme'
const theme = ref('dark')
const vectorProvider = ref('')
const graphProvider = ref('')

provide('vectorProvider', vectorProvider)
provide('graphProvider', graphProvider)

const expandedGroups = ref(new Set(['/ontology']))

function isGroupExpanded(groupKey) {
  return expandedGroups.value.has(groupKey)
}

const hasExpandedGroup = computed(() => expandedGroups.value.size > 0)

function toggleGroup(groupKey) {
  if (expandedGroups.value.has(groupKey)) {
    expandedGroups.value.delete(groupKey)
  } else {
    expandedGroups.value.add(groupKey)
  }
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
    ],
  },
  { to: '/entities', key: 'entities', label: '实体', exact: false, hint: '实体管理' },
  { to: '/kb', key: 'kb', label: '知识库', exact: false, hint: '知识库' },
  { to: '/files', key: 'files', label: '文件', exact: false, hint: '文件管理' },
  { to: '/query', key: 'query', label: '问答', exact: false, hint: '问答' },
  { to: '/graph', key: 'graph', label: '图谱', exact: false, hint: '图谱' },
  { to: '/vectors', key: 'vectors', label: '向量', exact: false, hint: '向量' },
])

const subIcons = {
  template: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="17.5" height="5" rx="1.25"/><rect x="3.25" y="10.75" width="7.5" height="10" rx="1.25"/><rect x="11.25" y="10.75" width="9.5" height="10" rx="1.25"/></svg>',
  ontology: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 6 7v5c0 3.5 2.5 6.5 6 8 3.5-1.5 6-4.5 6-8V7L12 3.5Z"/></svg>',
  dict: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v16H6.5A2.5 2.5 0 0 0 4 20.5"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20"/><path d="M8 7h6"/><path d="M8 10.5h4"/></svg>',
  triple: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.8 7.5 11 16"/></svg>',
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
})
</script>

<template>
  <div class="app-shell" :class="{ 'is-collapsed': isSidebarCollapsed, 'has-submenu': hasExpandedGroup && !isSidebarCollapsed }">
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
          <div v-if="item.children" class="side-group" :class="{ expanded: isGroupExpanded(item.key) }">
            <button
              class="side-item side-group-toggle"
              :class="{ 'is-active': route.path.startsWith('/ontology') }"
              @click="toggleGroup(item.key)"
            >
              <span class="side-icon-wrap" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="3.25" y="3.25" width="6" height="6" rx="1.5" />
                  <rect x="14.75" y="3.25" width="6" height="6" rx="1.5" />
                  <rect x="9" y="14.75" width="6" height="6" rx="1.5" />
                  <path d="M6.25 9.25v1.75a1.5 1.5 0 0 0 1.5 1.5h1.25" />
                  <path d="M17.75 9.25v1.75a1.5 1.5 0 0 1-1.5 1.5H15.25" />
                </svg>
              </span>
              <span class="side-label">{{ item.label }}</span>
              <span class="side-hint">{{ item.hint }}</span>
            </button>

          <!-- 展开的子菜单（侧边栏未折叠时） -->
            <div v-if="isGroupExpanded(item.key) && !isSidebarCollapsed" class="side-submenu">
              <router-link
                v-for="child in item.children"
                :key="child.key"
                :to="child.to"
                class="side-item side-sub-item"
                active-class="is-active"
              >
                <span class="side-sub-icon" v-html="subIcons[child.icon] || ''"></span>
                <span class="side-label">{{ child.label }}</span>
              </router-link>
            </div>

            <!-- 折叠时的飞出子菜单 -->
            <div v-if="isSidebarCollapsed" class="side-flyout">
              <div class="flyout-title">{{ item.hint }}</div>
              <router-link
                v-for="child in item.children"
                :key="child.key"
                :to="child.to"
                class="flyout-item"
                active-class="is-active"
              >
                {{ child.label }}
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
        <KeepAlive v-if="route.meta.keepAlive">
          <component :is="Component" :key="route.params.kbId || route.path" />
        </KeepAlive>
        <component :is="Component" v-else />
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

.app-shell.has-submenu .sidebar {
  width: 132px;
  padding: 12px 8px;
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

/* 二级菜单 */
.side-group { display: flex; flex-direction: column; gap: 2px; }

.side-group-toggle { position: relative; width: 100%; }
.side-group.expanded .side-group-toggle {
  background: var(--c-muted);
  color: var(--c-fg);
}

.side-submenu { display: flex; flex-direction: column; gap: 2px; padding: 4px 4px 8px; }
.side-sub-item {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 5px 6px;
  border-radius: 8px;
  position: relative;
  overflow: hidden;
}
.side-sub-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--c-secondary);
  transition: color 150ms ease;
}
.side-sub-item .side-label {
  font-size: 11px;
  font-weight: 500;
  text-align: left;
  color: var(--c-secondary);
  transition: color 150ms ease;
}
.side-sub-item.is-active {
  background: var(--c-muted);
  color: var(--c-fg);
  font-weight: 600;
}
.side-sub-item.is-active .side-sub-icon { color: var(--c-accent); }
.side-sub-item.is-active .side-label { color: var(--c-fg); }
.side-sub-item:hover:not(.is-active) {
  background: var(--c-muted);
  color: var(--c-fg);
}
.side-sub-item:hover:not(.is-active) .side-sub-icon { color: var(--c-fg); }
.side-sub-item:hover:not(.is-active) .side-label { color: var(--c-fg); }

/* 折叠时的飞出子菜单 */
.side-flyout {
  position: absolute;
  left: calc(100% + 8px);
  top: 0;
  min-width: 160px;
  padding: 6px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-panel-elevated);
  box-shadow: 0 12px 28px rgba(92, 78, 58, 0.12);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity 140ms ease, visibility 140ms ease;
  z-index: 9999;
}
.side-group:hover .side-flyout {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
}
.flyout-title {
  font-size: 11px; font-weight: 700; color: var(--c-secondary);
  padding: 4px 10px 6px; text-transform: uppercase; letter-spacing: 0.3px;
}
.flyout-item {
  display: block; padding: 7px 10px; border-radius: 8px;
  font-size: 13px; color: var(--c-fg); text-decoration: none;
  transition: background 120ms;
}
.flyout-item:hover { background: var(--c-muted); }
.flyout-item.is-active { background: var(--c-muted); font-weight: 600; }

.is-collapsed .side-group { position: relative; }
.is-collapsed .side-submenu { display: none; }

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
