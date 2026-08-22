<script setup>
import { computed, onMounted, onBeforeUnmount, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchConfig } from './api'
import { pendingSuggestionCount, refreshPendingSuggestionCount } from './stores/suggestions'
import ToastContainer from './components/ToastContainer.vue'

const router = useRouter()
const route = useRoute()
const isSidebarCollapsed = ref(false)
const SIDEBAR_STORAGE_KEY = 'knowsource.sidebar.collapsed'
const THEME_STORAGE_KEY = 'knowsource.theme'
// 主题列表：右上角皮肤下拉菜单（首项为系统默认）
const THEMES = [
  { key: 'platform-dark', label: '深蓝', swatch: '#2DD4BF' },
  { key: 'light', label: '浅色', swatch: '#A16207' },
  { key: 'dark', label: '深色', swatch: '#E0A84E' },
]
const theme = ref('platform-dark')
const vectorProvider = ref('')
const graphProvider = ref('')

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
function onGroupClick(item) {
  // 带 to 的分组（如"图谱"）：点击即由 router-link 导航；纯分组才切换悬浮卡片
  if (item.to) return
  toggleGroupMenu(item.key)
}
function onSidePointerDown(e) {
  if (openGroup.value !== null && !e.target.closest('.side-group')) openGroup.value = null
  if (themeMenuOpen.value && !e.target.closest('.theme-menu')) themeMenuOpen.value = false
}
function onSideKeydown(e) {
  if (e.key === 'Escape') {
    openGroup.value = null
    themeMenuOpen.value = false
  }
}

// 路由变化时收起卡片
watch(() => route.path, () => { openGroup.value = null })

// 判断分组是否处于当前路由下（用于高亮分组标题）
function isGroupActive(item) {
  if (item.to && (route.path === item.to || route.path.startsWith(item.to + '/'))) return true
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
  {
    key: 'graph',
    label: '图谱',
    hint: '图谱',
    to: '/graph',
    children: [
      { to: '/graph-cleanup', key: 'graph-cleanup', label: '图谱清洗', icon: 'cleanup' },
    ],
  },
  {
    key: 'agent',
    label: '智能体',
    hint: '本体增强问答',
    to: '/agent',
    children: [
      { to: '/agent/skills', key: 'agent-skills', label: '技能管理', icon: 'skills' },
    ],
  },
  {
    key: 'data',
    label: '数据',
    hint: '检索与数据',
    children: [
      { to: '/query', key: 'query', label: '知识库检索', icon: 'query' },
      { to: '/vectors', key: 'vectors', label: '向量', icon: 'vector' },
    ],
  },
  {
    key: 'config',
    label: '配置',
    hint: '系统配置',
    children: [
      { to: '/config/models', key: 'models', label: '模型配置', icon: 'models' },
    ],
  },
])

const subIcons = {
  template: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="17.5" height="5" rx="1.25"/><rect x="3.25" y="10.75" width="7.5" height="10" rx="1.25"/><rect x="11.25" y="10.75" width="9.5" height="10" rx="1.25"/></svg>',
  ontology: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 6 7v5c0 3.5 2.5 6.5 6 8 3.5-1.5 6-4.5 6-8V7L12 3.5Z"/></svg>',
  dict: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v16H6.5A2.5 2.5 0 0 0 4 20.5"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20"/><path d="M8 7h6"/><path d="M8 10.5h4"/></svg>',
  triple: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.8 7.5 11 16"/></svg>',
  suggestion: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>',
  cleanup: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.75 4.75h16.5l-6 7v6.5l-4.5 2.5v-9z"/><path d="M3.75 4.75 9 11.75"/></svg>',
  vector: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="8" r="1.6"/><circle cx="12" cy="5" r="1.6"/><circle cx="18" cy="9" r="1.6"/><circle cx="9" cy="16" r="1.6"/><circle cx="16" cy="18" r="1.6"/></svg>',
  query: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16.65" y2="16.65"/></svg>',
  models: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>',
  skills: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
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

// 皮肤下拉菜单
const themeMenuOpen = ref(false)

function toggleThemeMenu() {
  themeMenuOpen.value = !themeMenuOpen.value
}

function selectTheme(key) {
  applyTheme(key)
  themeMenuOpen.value = false
}

// 顶栏面包屑：根据当前路由解析 分组/页面 名（子路由优先于父级匹配）
const EXTRA_ROUTE_TITLES = [
  ['/entities/relations', '实体', '关系列表'],
]
const routeTitle = computed(() => {
  const p = route.path
  if (p === '/') return { group: '', label: '首页' }
  for (const [prefix, group, title] of EXTRA_ROUTE_TITLES) {
    if (p === prefix || p.startsWith(prefix + '/')) return { group, label: title }
  }
  for (const item of menuItems.value) {
    for (const c of item.children || []) {
      if (p === c.to || p.startsWith(c.to + '/')) return { group: item.label, label: c.label }
    }
    if (item.to && (p === item.to || p.startsWith(item.to + '/'))) return { group: '', label: item.label }
  }
  return { group: '', label: '' }
})

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
  if (THEMES.some(t => t.key === savedTheme)) {
    applyTheme(savedTheme)
  } else {
    // 默认使用数据平台深色
    applyTheme('platform-dark')
  }

  fetchConfig().then(cfg => {
    vectorProvider.value = cfg.vector_provider || ''
    graphProvider.value = cfg.graph_provider || ''
  }).catch(() => {})

  // 全局检查待审核本体建议数（后续由审核页在增删改后主动刷新）
  refreshPendingSuggestionCount()

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
      </div>

      <nav class="side-nav">
        <template v-for="item in menuItems" :key="item.key">
          <!-- 有子菜单的分组 -->
          <div v-if="item.children"
               class="side-group"
               :class="{ 'is-open': openGroup === item.key, 'is-active': isGroupActive(item) }"
               @mouseenter="openGroupMenu(item.key)"
               @mouseleave="closeGroupMenu()">
            <component
              :is="item.to ? 'router-link' : 'button'"
              :to="item.to"
              class="side-item side-group-toggle"
              :class="{ 'is-active': isGroupActive(item) }"
              type="button"
              :aria-expanded="openGroup === item.key"
              @click="onGroupClick(item)"
            >
              <span class="side-icon-wrap" aria-hidden="true">
                <svg v-if="item.key === 'ontology'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="3.25" y="3.25" width="6" height="6" rx="1.5" />
                  <rect x="14.75" y="3.25" width="6" height="6" rx="1.5" />
                  <rect x="9" y="14.75" width="6" height="6" rx="1.5" />
                  <path d="M6.25 9.25v1.75a1.5 1.5 0 0 0 1.5 1.5h1.25" />
                  <path d="M17.75 9.25v1.75a1.5 1.5 0 0 1-1.5 1.5H15.25" />
                </svg>
                <svg v-else-if="item.key === 'config'" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
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
                  <ellipse cx="12" cy="5.5" rx="7.5" ry="2.8" />
                  <path d="M4.5 5.5v6c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8v-6" />
                  <path d="M4.5 11.5v6c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8v-6" />
                </svg>
              </span>
              <span class="side-label">{{ item.label }}</span>
              <span class="side-chev" aria-hidden="true">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>
              </span>
            </component>

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
      <header class="topbar">
        <nav class="topbar-crumb" aria-label="面包屑">
          <router-link to="/" class="crumb-link">首页</router-link>
          <template v-if="routeTitle.group">
            <span class="crumb-sep">/</span>
            <span class="crumb-group">{{ routeTitle.group }}</span>
          </template>
          <span class="crumb-sep">/</span>
          <span class="crumb-current">{{ routeTitle.label || 'KnowSource' }}</span>
        </nav>
        <div class="theme-menu" :class="{ 'is-open': themeMenuOpen }">
          <button
            class="theme-menu-btn"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="themeMenuOpen"
            aria-label="切换皮肤"
            title="切换皮肤"
            @click="toggleThemeMenu"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z" />
            </svg>
          </button>
          <div class="theme-dropdown" role="menu" aria-label="选择皮肤">
            <div class="td-title">皮肤</div>
            <button
              v-for="t in THEMES"
              :key="t.key"
              class="td-item"
              type="button"
              role="menuitemradio"
              :aria-checked="theme === t.key"
              :class="{ 'is-active': theme === t.key }"
              @click="selectTheme(t.key)"
            >
              <span class="td-swatch" :style="{ background: t.swatch }"></span>
              <span class="td-label">{{ t.label }}</span>
              <svg v-if="theme === t.key" class="td-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            </button>
          </div>
        </div>
      </header>

      <div class="main-content">
        <router-view v-slot="{ Component, route }">
          <KeepAlive>
            <component v-if="route.meta.keepAlive" :is="Component" :key="route.params.kbId || route.path" />
          </KeepAlive>
          <component v-if="!route.meta.keepAlive" :is="Component" :key="route.path" />
        </router-view>
      </div>
    </main>

    <ToastContainer />
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

/* 数据平台深色：去除暖色调阴影 */
:root[data-theme='platform-dark'] .side-hint,
:root[data-theme='platform-dark'] .theme-dropdown {
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.5);
}

.main-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  z-index: 1;
}

/* ─── 顶栏 ─── */
.topbar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 16px;
  height: 52px;
  padding: 0 24px;
  background: var(--c-panel-elevated);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--c-border);
}

.topbar-crumb {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
}

.crumb-link {
  color: var(--c-secondary);
  text-decoration: none;
  transition: color 120ms;
}
.crumb-link:hover { color: var(--c-fg); }

.crumb-sep { color: var(--c-border); font-size: 12px; user-select: none; }

.crumb-group { color: var(--c-secondary); }

.crumb-current {
  color: var(--c-fg);
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 皮肤切换（右上角调色板图标 + 下拉） */
.theme-menu {
  position: relative;
  margin-left: auto;
  flex-shrink: 0;
}

.theme-menu-btn {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--c-border);
  border-radius: 9px;
  background: var(--c-panel);
  color: var(--c-secondary);
  cursor: pointer;
  transition: background 150ms, color 150ms, border-color 150ms;
}

.theme-menu-btn:hover {
  background: var(--c-muted);
  color: var(--c-fg);
  border-color: var(--c-secondary);
}

.theme-menu.is-open .theme-menu-btn {
  color: var(--c-accent);
  border-color: var(--c-accent);
}

.theme-dropdown {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  min-width: 152px;
  padding: 6px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-panel-elevated);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.18);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transform: translateY(-4px);
  transition: opacity 150ms ease, transform 150ms ease, visibility 150ms ease;
  z-index: 60;
}

.theme-menu.is-open .theme-dropdown {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
  transform: translateY(0);
}

.td-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--c-secondary);
  padding: 6px 10px 7px;
  letter-spacing: 0.3px;
}

.td-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--c-fg);
  font-family: var(--font);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 120ms;
}

.td-item:hover { background: var(--c-muted); }
.td-item.is-active { color: var(--c-accent); font-weight: 600; }

.td-swatch {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.15);
}

.td-label { flex: 1; min-width: 0; }

.td-check { color: var(--c-accent); flex-shrink: 0; }

.main-content {
  flex: 1;
  padding: 28px 32px 48px;
}

.main-area.home-main .main-content {
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

  .main-content {
    padding: 20px 16px 40px;
  }

  .main-area.home-main .main-content {
    padding-bottom: 0;
  }

  .topbar {
    padding: 0 14px;
  }
}
</style>
