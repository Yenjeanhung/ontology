<script setup>
import { computed, onMounted, onBeforeUnmount, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchConfig } from './api'
import { notifications, refreshNotifications } from './stores/notifications'
import ToastContainer from './components/ToastContainer.vue'

const router = useRouter()
const route = useRoute()
const isSidebarCollapsed = ref(false)
const SIDEBAR_STORAGE_KEY = 'knowsource.sidebar.collapsed'
const THEME_STORAGE_KEY = 'knowsource.theme'
// 主题列表：右上角皮肤下拉菜单（首项为系统默认）。label 与 swatch 圆点均对应各主题的强调色
const THEMES = [
  { key: 'platform-dark', label: '青蓝', swatch: '#2DD4BF' },
  { key: 'light', label: '琥珀', swatch: '#A16207' },
  { key: 'dark', label: '金色', swatch: '#E0A84E' },
]
const theme = ref('platform-dark')
const vectorProvider = ref('')
const graphProvider = ref('')

provide('vectorProvider', vectorProvider)
provide('graphProvider', graphProvider)

/* ── 消息通知（侧栏红点 + 顶栏小喇叭） ── */
const bellOpen = ref(false)

const notifyItems = computed(() => notifications.value.items || [])
const notifyTotal = computed(() => notifications.value.total || 0)

function fmtBadge(n) {
  return n > 99 ? '99+' : String(n)
}

function badgeByKey(key) {
  if (key === 'suggestions') return notifications.value.suggestions || 0
  if (key === 'files') return (notifications.value.files_failed || 0) + (notifications.value.files_processing || 0)
  return 0
}

function itemBadgeCount(item) {
  let n = item.badgeKey ? badgeByKey(item.badgeKey) : 0
  for (const c of item.children || []) {
    if (c.badgeKey) n += badgeByKey(c.badgeKey)
  }
  return n
}

function openNotify(to) {
  bellOpen.value = false
  router.push(to)
}

/* ── 主题 ── */
function applyTheme(nextTheme) {
  theme.value = nextTheme
  document.documentElement.dataset.theme = nextTheme
  localStorage.setItem(THEME_STORAGE_KEY, nextTheme)
}

const themeMenuOpen = ref(false)

function selectTheme(key) {
  applyTheme(key)
  themeMenuOpen.value = false
}

/* ── 侧栏菜单 ── */
const menuIcons = {
  ontology: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="8.5" y="14" width="7" height="7" rx="1.5"/><path d="M6.5 10v1.5h4M17.5 10v1.5h-4"/></svg>',
  entities: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8.25" r="4.25"/><path d="M4.75 20.25a7.25 7.25 0 0 1 14.5 0"/></svg>',
  graph: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="6.5" cy="6.5" r="2.25"/><circle cx="17.5" cy="6.5" r="2.25"/><circle cx="12" cy="17.5" r="2.25"/><path d="M8.75 6.5h6.5M8.2 8.1l2.1 7.1m5.5-7.1-2.1 7.1"/></svg>',
  files: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.75 7.25A2.25 2.25 0 0 1 6 5h4.25c.57 0 1.12.22 1.54.62l1.14 1.1c.42.4.97.63 1.55.63H18A2.25 2.25 0 0 1 20.25 9.6v7.15A2.25 2.25 0 0 1 18 19H6a2.25 2.25 0 0 1-2.25-2.25Z"/><path d="M3.75 9.25h16.5"/></svg>',
  kb: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4.25" y="4.25" width="15.5" height="15.5" rx="2.25"/><path d="M8 8.5h8M8 12h8M8 15.5h5"/></svg>',
  agent: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.7 4.8L18.5 9.5l-4.8 1.7L12 16l-1.7-4.8L5.5 9.5l4.8-1.7L12 3z"/><path d="M18.5 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z"/></svg>',
  data: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.35-3.35"/></svg>',
  config: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82 2 2 0 1 1-2.83 2.83 1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0 1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33 2 2 0 1 1-2.83-2.83A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82 2 2 0 1 1 2.83-2.83A1.65 1.65 0 0 0 9 4.6 1.65 1.65 0 0 0 10 3.09V3a2 2 0 0 1 4 0 1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33 2 2 0 1 1 2.83 2.83A1.65 1.65 0 0 0 19.4 9c.14.35.4.64.74.83"/></svg>',
}

// 分组标题图标（侧栏分区：定义 / 生产 / 应用）
const groupIcons = {
  groupDef: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 6.5C10.2 5.1 7.8 4.5 5 4.5v13c2.8 0 5.2.6 7 2 1.8-1.4 4.2-2 7-2v-13c-2.8 0-5.2.6-7 2Z"/><path d="M12 6.5v13"/></svg>',
  groupProd: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2.5 5 13.5h5L9 21.5l8-11h-5l1-8Z"/></svg>',
  groupApp: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/></svg>',
}

const menuItems = [
  { type: 'group', label: '知识定义', icon: 'groupDef' },
  {
    key: 'ontology', label: '本体管理', icon: 'ontology',
    children: [
      { to: '/ontology/templates', label: '本体模板' },
      { to: '/ontology/ontologies', label: '本体管理' },
      { to: '/ontology/relations-dict', label: '关系字典' },
      { to: '/ontology/constraints', label: '本体关系' },
      { to: '/ontology/suggestions', label: '本体建议', badgeKey: 'suggestions' },
    ],
  },
  { key: 'entities', label: '实体', icon: 'entities', to: '/entities' },
  {
    key: 'graph', label: '知识图谱', icon: 'graph',
    children: [
      { to: '/graph', label: '图谱浏览' },
      { to: '/graph-cleanup', label: '图谱清洗' },
    ],
  },
  { type: 'group', label: '知识生产', icon: 'groupProd' },
  { key: 'files', label: '文件管理', icon: 'files', to: '/files', badgeKey: 'files' },
  {
    key: 'kb', label: '知识库', icon: 'kb',
    children: [
      { to: '/kb', label: '知识库列表' },
      { to: '/query', label: '知识库检索' },
    ],
  },
  { type: 'group', label: '应用', icon: 'groupApp' },
  {
    key: 'agent', label: '智能体', icon: 'agent',
    children: [
      { to: '/agent', label: '本体问答' },
      { to: '/agent/skills', label: '技能管理' },
    ],
  },
  { key: 'data', label: '向量数据', icon: 'data', to: '/vectors' },
  {
    key: 'config', label: '系统配置', icon: 'config',
    children: [
      { to: '/config/models', label: '模型配置' },
    ],
  },
]

// 手风琴展开状态（展开态用）
const openKeys = ref([])
// 悬浮子菜单（收起态用）
const flyoutKey = ref(null)
let flyoutTimer = null

function isChildActive(child) {
  return route.path === child.to || route.path.startsWith(child.to + '/')
}

function isItemActive(item) {
  if (item.to) return route.path === item.to || route.path.startsWith(item.to + '/')
  return (item.children || []).some(isChildActive)
}

function toggleAccordion(key) {
  if (isSidebarCollapsed.value) return
  openKeys.value = openKeys.value.includes(key)
    ? openKeys.value.filter(k => k !== key)
    : [...openKeys.value, key]
}

// 当前路由变化：自动展开所属父级
watch(() => route.path, () => {
  for (const item of menuItems) {
    if (item.children?.some(isChildActive) && !openKeys.value.includes(item.key)) {
      openKeys.value = [...openKeys.value, item.key]
    }
  }
}, { immediate: true })

// 收起态 hover 悬浮子菜单
function openFlyout(key) {
  if (!isSidebarCollapsed.value) return
  clearTimeout(flyoutTimer)
  flyoutKey.value = key
}
function scheduleFlyoutClose() {
  clearTimeout(flyoutTimer)
  flyoutTimer = setTimeout(() => { flyoutKey.value = null }, 150)
}

function toggleSidebar() {
  isSidebarCollapsed.value = !isSidebarCollapsed.value
  localStorage.setItem(SIDEBAR_STORAGE_KEY, String(isSidebarCollapsed.value))
}

function onPointerDown(e) {
  if (bellOpen.value && !e.target.closest('.bell-menu')) bellOpen.value = false
  if (themeMenuOpen.value && !e.target.closest('.theme-menu')) themeMenuOpen.value = false
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    bellOpen.value = false
    themeMenuOpen.value = false
  }
}

/* ── 顶栏面包屑 ── */
const EXTRA_ROUTE_TITLES = [
  ['/entities/relations', '实体', '关系列表'],
]
const routeTitle = computed(() => {
  const p = route.path
  if (p === '/') return { group: '', label: '首页' }
  for (const [prefix, group, title] of EXTRA_ROUTE_TITLES) {
    if (p === prefix || p.startsWith(prefix + '/')) return { group, label: title }
  }
  for (const item of menuItems) {
    for (const c of item.children || []) {
      if (p === c.to || p.startsWith(c.to + '/')) return { group: item.label, label: c.label }
    }
    if (item.to && (p === item.to || p.startsWith(item.to + '/'))) return { group: '', label: item.label }
  }
  return { group: '', label: '' }
})

function goHome() {
  router.push('/')
}

let pollTimer = null

onMounted(() => {
  const saved = localStorage.getItem(SIDEBAR_STORAGE_KEY)
  if (saved !== null) {
    isSidebarCollapsed.value = saved === 'true'
  }

  const savedTheme = localStorage.getItem(THEME_STORAGE_KEY)
  if (THEMES.some(t => t.key === savedTheme)) {
    applyTheme(savedTheme)
  } else {
    applyTheme('platform-dark')
  }

  fetchConfig().then(cfg => {
    vectorProvider.value = cfg.vector_provider || ''
    graphProvider.value = cfg.graph_provider || ''
  }).catch(() => {})

  // 消息计数：首屏 + 每 30s 轮询（处理中/失败文件数会随任务推进变化）
  refreshNotifications()
  pollTimer = setInterval(refreshNotifications, 30000)

  window.addEventListener('pointerdown', onPointerDown)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  clearInterval(pollTimer)
  window.removeEventListener('pointerdown', onPointerDown)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="app-shell" :class="{ 'is-collapsed': isSidebarCollapsed }">
    <aside class="sidebar">
      <div class="sidebar-head">
        <button class="brand" type="button" aria-label="返回首页" @click="goHome">
          <span class="brand-mark">K</span>
          <span class="brand-name">KnowSource</span>
          <span class="brand-tag">v1.4</span>
        </button>
        <button
          class="sidebar-toggle"
          type="button"
          :aria-label="isSidebarCollapsed ? '展开菜单' : '收起菜单'"
          @click="toggleSidebar"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.85" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="3.75" y="5" width="16.5" height="14" rx="3" />
            <path d="M9 5v14" opacity="0.55" />
            <path class="toggle-chev" d="m14.25 9.25 2.5 2.5-2.5 2.5" />
          </svg>
        </button>
      </div>

      <nav class="nav">
        <template v-for="item in menuItems" :key="item.key || item.label">
          <div v-if="item.type === 'group'" class="nav-group-label">
            <span class="nav-group-icon" v-html="groupIcons[item.icon]"></span>
            <span class="nav-group-text">{{ item.label }}</span>
          </div>

          <!-- 带子菜单 -->
          <div
            v-else-if="item.children"
            class="nav-branch"
            :class="{
              'is-open': !isSidebarCollapsed && openKeys.includes(item.key),
              'is-flyout': isSidebarCollapsed && flyoutKey === item.key,
              'is-active': isItemActive(item),
            }"
            @mouseenter="openFlyout(item.key)"
            @mouseleave="scheduleFlyoutClose()"
          >
            <button
              class="nav-item"
              type="button"
              :class="{ 'is-active': isItemActive(item) }"
              :aria-expanded="isSidebarCollapsed ? flyoutKey === item.key : openKeys.includes(item.key)"
              @click="isSidebarCollapsed ? openFlyout(item.key) : toggleAccordion(item.key)"
            >
              <span class="nav-icon" v-html="menuIcons[item.icon]"></span>
              <span class="nav-text">{{ item.label }}</span>
              <span v-if="itemBadgeCount(item)" class="nav-badge">{{ fmtBadge(itemBadgeCount(item)) }}</span>
              <svg class="nav-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 6 15 12 9 18"/></svg>
              <span class="nav-tip">{{ item.label }}<template v-if="itemBadgeCount(item)"> · {{ itemBadgeCount(item) }}</template></span>
            </button>

            <!-- 展开态：手风琴子项 -->
            <div v-if="!isSidebarCollapsed" v-show="openKeys.includes(item.key)" class="sub-menu">
              <router-link
                v-for="c in item.children"
                :key="c.to"
                :to="c.to"
                class="sub-item"
                :class="{ 'is-active': isChildActive(c) }"
              >
                <span>{{ c.label }}</span>
                <span v-if="c.badgeKey && badgeByKey(c.badgeKey)" class="nav-badge">{{ fmtBadge(badgeByKey(c.badgeKey)) }}</span>
              </router-link>
            </div>

            <!-- 收起态：hover 悬浮子菜单 -->
            <div v-else class="nav-flyout">
              <div class="flyout-title">{{ item.label }}</div>
              <router-link
                v-for="c in item.children"
                :key="c.to"
                :to="c.to"
                class="flyout-item"
                :class="{ 'is-active': isChildActive(c) }"
              >
                <span>{{ c.label }}</span>
                <span v-if="c.badgeKey && badgeByKey(c.badgeKey)" class="nav-badge">{{ fmtBadge(badgeByKey(c.badgeKey)) }}</span>
              </router-link>
            </div>
          </div>

          <!-- 普通菜单项 -->
          <router-link v-else :to="item.to" class="nav-item" :class="{ 'is-active': isItemActive(item) }">
            <span class="nav-icon" v-html="menuIcons[item.icon]"></span>
            <span class="nav-text">{{ item.label }}</span>
            <span v-if="itemBadgeCount(item)" class="nav-badge">{{ fmtBadge(itemBadgeCount(item)) }}</span>
            <span class="nav-tip">{{ item.label }}<template v-if="itemBadgeCount(item)"> · {{ itemBadgeCount(item) }}</template></span>
          </router-link>
        </template>
      </nav>

      <!-- 收起态：底部品牌 K 字（与展开态一致） -->
      <button class="rail-home" type="button" aria-label="返回首页" @click="goHome">
        <span class="brand-mark">K</span>
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

        <!-- 消息小喇叭 -->
        <div class="bell-menu" :class="{ 'is-open': bellOpen }">
          <button
            class="topbar-icon-btn"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="bellOpen"
            aria-label="消息通知"
            title="消息通知"
            @click="bellOpen = !bellOpen"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.7 21a2 2 0 0 1-3.4 0" />
            </svg>
            <span v-if="notifyTotal > 0" class="bell-badge">{{ fmtBadge(notifyTotal) }}</span>
          </button>
          <div class="bell-dropdown" role="menu" aria-label="消息通知">
            <div class="bd-head">
              <span class="bd-title">消息通知</span>
              <span v-if="notifyTotal > 0" class="bd-total">{{ fmtBadge(notifyTotal) }}</span>
            </div>
            <div v-if="!notifyItems.length" class="bd-empty">暂无待处理消息</div>
            <button
              v-for="n in notifyItems"
              :key="n.key"
              class="bd-item"
              type="button"
              role="menuitem"
              @click="openNotify(n.to)"
            >
              <span class="bd-dot" :class="n.key === 'files_failed' ? 'err' : n.key === 'suggestions' ? 'warn' : ''"></span>
              <span class="bd-label">{{ n.label }}</span>
              <span class="bd-count">{{ fmtBadge(n.count) }}</span>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" class="bd-arrow"><polyline points="9 6 15 12 9 18"/></svg>
            </button>
          </div>
        </div>

        <!-- 皮肤切换 -->
        <div class="theme-menu" :class="{ 'is-open': themeMenuOpen }">
          <button
            class="theme-menu-btn topbar-icon-btn"
            type="button"
            aria-haspopup="menu"
            :aria-expanded="themeMenuOpen"
            aria-label="切换皮肤"
            title="切换皮肤"
            @click="themeMenuOpen = !themeMenuOpen"
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

<style>
/* 主题级激活底色（供侧栏激活项使用） */
:root { --c-accent-weak: rgba(161, 98, 7, 0.10); }
:root[data-theme='dark'] { --c-accent-weak: rgba(224, 168, 78, 0.13); }
:root[data-theme='platform-dark'] { --c-accent-weak: rgba(45, 212, 191, 0.10); }
</style>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100dvh;
  width: 100%;
  max-width: 100%;
  margin: 0;
  overflow: visible;
}

/* ─── 侧栏（展开态：宽侧栏专业样式） ─── */
.sidebar {
  width: 216px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 14px 10px 12px;
  position: sticky;
  top: 0;
  height: 100dvh;
  border-right: 1px solid var(--c-border);
  background: var(--c-panel);
  transition: width 180ms ease, padding 180ms ease;
  z-index: 40;
}

/* 顶部：品牌 + 展开/收起按钮（两态统一用一个图标） */
.sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 2px 0 14px;
}
.sidebar-toggle {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--c-secondary);
  cursor: pointer;
  transition: background 150ms ease, color 150ms ease;
}
.sidebar-toggle:hover { background: var(--c-muted); color: var(--c-fg); }
.sidebar-toggle .toggle-chev {
  transform-box: fill-box;
  transform-origin: center;
  transform: rotate(180deg);
  transition: transform 220ms ease;
}
.is-collapsed .sidebar-toggle .toggle-chev { transform: rotate(0deg); }

.rail-home {
  display: none;
  position: relative;
  width: 40px;
  height: 40px;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--c-secondary);
  cursor: pointer;
  transition: background 150ms ease, color 150ms ease;
}
.rail-home:hover { background: var(--c-muted); color: var(--c-fg); }
.rail-home:hover .nav-tip,
.rail-home:focus-visible .nav-tip {
  opacity: 1;
  visibility: visible;
  transform: translateY(-50%) translateX(0);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
  padding: 2px 4px;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: var(--font);
  text-align: left;
}
.brand-mark {
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--c-accent);
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  flex-shrink: 0;
}
:global([data-theme='platform-dark']) .brand-mark { color: #0b0c0f; }
.brand-name {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.2px;
  color: var(--c-fg);
  white-space: nowrap;
}
.brand-tag {
  font-size: 10px;
  color: var(--c-secondary);
  border: 1px solid var(--c-border);
  border-radius: 3px;
  padding: 1px 5px;
  margin-left: auto;
  white-space: nowrap;
}

.nav {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 2px 4px 0;
  margin-right: -2px;
  scrollbar-width: thin;
}

.nav-group-label {
  display: flex;
  align-items: center;
  font-size: 11px;
  font-weight: 700;
  color: var(--c-fg);
  letter-spacing: 1px;
  padding: 14px 10px 8px;
  white-space: nowrap;
}
.nav-group-label:first-child { padding-top: 4px; }
.nav-group-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  margin-right: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--c-accent);
}
.nav-group-icon svg { width: 16px; height: 16px; }
.nav-group-text { flex: none; }
.nav-group-label::after {
  content: '';
  flex: 1;
  height: 1px;
  margin-left: 12px;
  background: var(--c-border);
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px 8px 24px;
  border: 0;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-family: var(--font);
  color: var(--c-secondary);
  text-decoration: none;
  cursor: pointer;
  margin-bottom: 1px;
  background: transparent;
  transition: background 130ms, color 130ms;
  white-space: nowrap;
}
.nav-item:hover { background: var(--c-muted); color: var(--c-fg); }
.nav-item.is-active { background: var(--c-accent-weak); color: var(--c-accent); font-weight: 600; }

.nav-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  opacity: 0.85;
}
.nav-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; text-align: left; }

.nav-chev {
  color: var(--c-secondary);
  opacity: 0.55;
  flex-shrink: 0;
  transition: transform 180ms ease, opacity 150ms;
  pointer-events: none;
}
.nav-branch.is-open .nav-chev { transform: rotate(90deg); opacity: 1; color: var(--c-accent); }

/* 红点徽标 */
.nav-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  margin-left: auto;
  flex-shrink: 0;
  border-radius: 8px;
  background: #e54545;
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
}
.sub-item .nav-badge, .flyout-item .nav-badge { margin-left: 8px; }

/* 展开态：手风琴子菜单 */
.sub-menu {
  padding: 2px 0 4px 41px;
  display: flex;
  flex-direction: column;
}
.sub-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6.5px 10px;
  border-radius: var(--radius-sm);
  font-size: 12.5px;
  color: var(--c-secondary);
  text-decoration: none;
  transition: background 120ms, color 120ms;
}
.sub-item::before {
  content: '';
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.4;
  flex-shrink: 0;
}
.sub-item:hover { background: var(--c-muted); color: var(--c-fg); }
.sub-item.is-active { color: var(--c-accent); font-weight: 600; }
.sub-item span:first-of-type { flex: 1; }

/* 收起态：hover 悬浮子菜单 */
.nav-branch { position: relative; }
.nav-flyout {
  position: absolute;
  left: calc(100% + 8px);
  top: -4px;
  min-width: 176px;
  padding: 6px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  background: var(--c-panel-elevated);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.18);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transform: translateX(-6px);
  transition: opacity 150ms ease, transform 150ms ease, visibility 150ms ease;
  z-index: 9999;
}
.nav-flyout::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 0;
  bottom: 0;
  width: 8px;
}
.nav-branch.is-flyout .nav-flyout {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
  transform: translateX(0);
}
.flyout-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--c-secondary);
  padding: 5px 10px 7px;
  letter-spacing: 0.3px;
}
.flyout-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7.5px 10px;
  border-radius: var(--radius-sm);
  font-size: 12.5px;
  color: var(--c-fg);
  text-decoration: none;
  transition: background 120ms, color 120ms;
}
.flyout-item span:first-of-type { flex: 1; }
.flyout-item:hover { background: var(--c-muted); }
.flyout-item.is-active { color: var(--c-accent); font-weight: 600; background: var(--c-accent-weak); }

/* 收起态文字提示 */
.nav-tip {
  position: absolute;
  left: calc(100% + 10px);
  top: 50%;
  transform: translateY(-50%) translateX(-4px);
  padding: 6px 10px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: var(--c-panel-elevated);
  color: var(--c-fg);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.18);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity 140ms ease, transform 140ms ease, visibility 140ms ease;
  z-index: 9999;
}
/* 气泡小箭头（还原旧版 side-hint 样式） */
.nav-tip::before {
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
.is-collapsed .nav-item .nav-tip { display: none; }
.is-collapsed .nav-branch.is-flyout .nav-tip { display: none; }

/* ─── 收起态（窄栏：图标 + 下方文字） ─── */
.is-collapsed .sidebar { width: 80px; padding-left: 8px; padding-right: 8px; }
.is-collapsed .sidebar-head { justify-content: center; padding-bottom: 10px; }
.is-collapsed .brand { display: none; }
.is-collapsed .nav { gap: 8px; padding-top: 8px; overflow: visible; }
.is-collapsed .nav-group-label { display: none; }
.is-collapsed .nav-item {
  flex-direction: column;
  justify-content: center;
  gap: 5px;
  min-height: 54px;
  padding: 8px 4px;
  border-radius: 14px;
}
.is-collapsed .nav-chev { display: none; }
.is-collapsed .nav-text {
  display: block;
  flex: none;
  width: 100%;
  text-align: center;
  font-size: 10.5px;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.is-collapsed .nav-icon { width: 20px; height: 20px; opacity: 1; }
.is-collapsed .nav-icon svg { width: 20px; height: 20px; }
.is-collapsed .nav-badge {
  position: absolute;
  top: 4px;
  right: 8px;
  margin: 0;
  min-width: 15px;
  height: 15px;
  font-size: 9px;
  padding: 0 3px;
}
.is-collapsed .rail-home { display: inline-flex; align-self: center; margin-bottom: 4px; }

/* ─── 主区 / 顶栏 ─── */
.main-area {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  z-index: 1;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 10px;
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
  flex: 1;
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

/* 顶栏图标按钮（小喇叭 / 皮肤） */
.bell-menu { position: relative; margin-left: auto; flex-shrink: 0; display: flex; }
.theme-menu { position: relative; flex-shrink: 0; display: flex; }

.topbar-icon-btn {
  position: relative;
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
.topbar-icon-btn:hover { background: var(--c-muted); color: var(--c-fg); border-color: var(--c-secondary); }

.bell-menu.is-open .topbar-icon-btn,
.theme-menu.is-open .topbar-icon-btn { color: var(--c-accent); border-color: var(--c-accent); }

.bell-badge {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: #e54545;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  box-shadow: 0 0 0 2px var(--c-panel-elevated);
}

/* 消息下拉 */
.bell-dropdown {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  min-width: 236px;
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
.bell-menu.is-open .bell-dropdown {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
  transform: translateY(0);
}

.bd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px 7px;
}
.bd-title { font-size: 11px; font-weight: 700; color: var(--c-secondary); letter-spacing: 0.3px; }
.bd-total {
  min-width: 18px;
  height: 16px;
  padding: 0 5px;
  border-radius: 8px;
  background: #e54545;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.bd-empty { padding: 16px 10px; text-align: center; font-size: 12.5px; color: var(--c-secondary); }

.bd-item {
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
.bd-item:hover { background: var(--c-muted); }

.bd-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--c-accent); flex-shrink: 0; }
.bd-dot.err { background: #e54545; }
.bd-dot.warn { background: #d97706; }
.bd-label { flex: 1; min-width: 0; }
.bd-count {
  min-width: 20px;
  padding: 1px 6px;
  border-radius: 8px;
  background: var(--c-muted);
  color: var(--c-secondary);
  font-size: 11px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.bd-arrow { color: var(--c-secondary); opacity: 0; flex-shrink: 0; transition: opacity 120ms, transform 120ms; }
.bd-item:hover .bd-arrow { opacity: 1; transform: translateX(2px); }

/* 皮肤下拉 */
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

@media (max-width: 640px) {
  .main-content { padding: 20px 16px 40px; }
  .main-area.home-main .main-content { padding-bottom: 0; }
  .topbar { padding: 0 14px; }
}
</style>
