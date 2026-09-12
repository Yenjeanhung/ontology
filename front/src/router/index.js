import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../components/HomeView.vue'
import KbList from '../components/KbList.vue'
import KbDetail from '../components/KbDetail.vue'
import FileLibrary from '../components/FileLibrary.vue'
import QueryView from '../components/QueryView.vue'
import AgentView from '../components/AgentView.vue'
import AgentListPage from '../components/agent/AgentListPage.vue'
import SkillListPage from '../components/SkillListPage.vue'
import VectorDataView from '../components/VectorDataView.vue'
import VectorFileDetail from '../components/VectorFileDetail.vue'
import GraphView from '../components/GraphView.vue'
import AttributeTemplateList from '../components/ontology/AttributeTemplateList.vue'
import SharedPropertyList from '../components/ontology/SharedPropertyList.vue'
import OntologyManagePage from '../components/ontology/OntologyManagePage.vue'
import RelationDictPage from '../components/ontology/RelationDictPage.vue'
import ConstraintPage from '../components/ontology/ConstraintPage.vue'
import SuggestionListPage from '../components/ontology/SuggestionListPage.vue'
import FunctionEditorPage from '../components/ontology/FunctionEditorPage.vue'
import ModelConfigPage from '../components/config/ModelConfigPage.vue'
import ApiDocsPage from '../components/config/ApiDocsPage.vue'
import MonitorPage from '../components/monitor/MonitorPage.vue'
import EntityListPage from '../components/entity/EntityListPage.vue'
import EntityDetailPage from '../components/entity/EntityDetailPage.vue'
import GraphCleanupPage from '../components/entity/GraphCleanupPage.vue'
import GraphAnalysisPage from '../components/graph/GraphAnalysisPage.vue'
import WorkflowListPage from '../components/workflow/WorkflowListPage.vue'
import WorkflowEditorPage from '../components/workflow/WorkflowEditorPage.vue'
import ServiceEditorPage from '../components/ontology/ServiceEditorPage.vue'
import ScheduleListPage from '../components/scheduler/ScheduleListPage.vue'
import ScheduleEditorPage from '../components/scheduler/ScheduleEditorPage.vue'
import HumanTaskCenterPage from '../components/workflow/HumanTaskCenter.vue'
// 用户与权限
import LoginView from '../components/LoginView.vue'
import UserListView from '../components/system/UserListView.vue'
import RoleListView from '../components/system/RoleListView.vue'
import SessionListView from '../components/system/SessionListView.vue'
import AuditLogView from '../components/system/AuditLogView.vue'
import { auth, hasPerm, isAuthed, loadAuthStatus, loadMe } from '../stores/auth'
import { clearTokens, isLoggedIn } from '../api/auth'

const routes = [
  { path: '/login', name: 'login', component: LoginView, meta: { public: true, fullscreen: true } },
  { path: '/', name: 'home', component: HomeView },
  // 本体管理（大菜单）
  { path: '/ontology/templates', name: 'ontology-templates', component: AttributeTemplateList, meta: { keepAlive: true, perm: 'ontology:view' } },
  { path: '/ontology/shared-properties', name: 'ontology-shared-properties', component: SharedPropertyList, meta: { keepAlive: true, perm: 'ontology:view' } },
  { path: '/ontology/ontologies', name: 'ontology-ontologies', component: OntologyManagePage, meta: { keepAlive: true, perm: 'ontology:view' } },
  { path: '/ontology/relations-dict', name: 'ontology-relations-dict', component: RelationDictPage, meta: { keepAlive: true, perm: 'ontology:view' } },
  { path: '/ontology/constraints', name: 'ontology-constraints', component: ConstraintPage, meta: { keepAlive: true, perm: 'ontology:view' } },
  { path: '/ontology/suggestions', name: 'ontology-suggestions', component: SuggestionListPage, meta: { keepAlive: true, perm: 'ontology:view' } },
  { path: '/ontology/functions', name: 'ontology-functions', component: FunctionEditorPage, meta: { keepAlive: true, perm: 'ontology:view' } },
  // 实体管理
  { path: '/entities', name: 'entities', component: EntityListPage, meta: { keepAlive: true, perm: 'entity:view' } },
  { path: '/entities/:entityId', name: 'entity-detail', component: EntityDetailPage, props: true, meta: { perm: 'entity:view' } },
  { path: '/graph-cleanup', name: 'graph-cleanup', component: GraphCleanupPage, meta: { keepAlive: true, perm: 'entity:view' } },
  // 图分析（迁入 / 图计算 / 图推理）
  { path: '/graph-analysis', name: 'graph-analysis', component: GraphAnalysisPage, meta: { keepAlive: true, perm: 'graph:view' } },
  // 知识库
  { path: '/kb', name: 'kb', component: KbList, meta: { keepAlive: true, perm: 'kb:view' } },
  { path: '/kb/:kbId', name: 'kb-detail', component: KbDetail, props: true, meta: { keepAlive: true, perm: 'kb:view' } },
  // 其他
  { path: '/files', name: 'files', component: FileLibrary, meta: { keepAlive: true, perm: 'file:view' } },
  { path: '/query', name: 'query', component: QueryView, meta: { keepAlive: true, perm: 'kb:query' } },
  { path: '/agent', name: 'agent', component: AgentView, meta: { keepAlive: true, perm: 'agent:view' } },
  { path: '/agent/configs', name: 'agent-configs', component: AgentListPage, meta: { keepAlive: true, perm: 'agent:view' } },
  { path: '/agent/skills', name: 'agent-skills', component: SkillListPage, meta: { keepAlive: true, perm: 'agent:view' } },
  { path: '/vectors', name: 'vectors', component: VectorDataView, meta: { keepAlive: true, perm: 'vector:view' } },
  { path: '/vectors/:fileId', name: 'vector-file-detail', component: VectorFileDetail, props: true, meta: { perm: 'vector:view' } },
  { path: '/graph', name: 'graph', component: GraphView, meta: { keepAlive: true, perm: 'graph:view' } },
  // 工作流
  { path: '/workflows', name: 'workflows', component: WorkflowListPage, meta: { keepAlive: true, perm: 'workflow:view' } },
  { path: '/workflows/:workflowId', name: 'workflow-editor', component: WorkflowEditorPage, props: true, meta: { perm: 'workflow:view' } },
  // 人工节点待办中心
  { path: '/human-tasks', name: 'human-tasks', component: HumanTaskCenterPage, meta: { keepAlive: true, perm: 'workflow:view' } },
  // 定时管理
  { path: '/schedules', name: 'schedules', component: ScheduleListPage, meta: { keepAlive: true, perm: 'schedule:view' } },
  { path: '/schedules/:scheduleId', name: 'schedule-editor', component: ScheduleEditorPage, props: true, meta: { keepAlive: false, perm: 'schedule:view' } },
  // 服务编辑器（本体服务 / 实体自定义动作，独立大页面）
  { path: '/ontology-services/new', name: 'ontology-service-new', component: ServiceEditorPage, meta: { keepAlive: false, fullscreen: true, perm: 'ontology:view' } },
  { path: '/ontology-services/:serviceId/edit', name: 'ontology-service-edit', component: ServiceEditorPage, props: true, meta: { keepAlive: false, fullscreen: true, perm: 'ontology:view' } },
  { path: '/entity-services/new', name: 'entity-service-new', component: ServiceEditorPage, meta: { keepAlive: false, fullscreen: true, perm: 'entity:view' } },
  { path: '/entity-services/:serviceId/edit', name: 'entity-service-edit', component: ServiceEditorPage, props: true, meta: { keepAlive: false, fullscreen: true, perm: 'entity:view' } },
  // 配置
  { path: '/config/models', name: 'config-models', component: ModelConfigPage, meta: { keepAlive: true, perm: 'config:view' } },
  { path: '/config/monitor', name: 'config-monitor', component: MonitorPage, meta: { keepAlive: true, perm: 'config:view' } },
  { path: '/config/api-docs', name: 'config-api-docs', component: ApiDocsPage, meta: { keepAlive: true, perm: 'config:view' } },
  // 系统管理（用户与权限）
  { path: '/system/users', name: 'system-users', component: UserListView, meta: { perm: 'system:user:manage' } },
  { path: '/system/roles', name: 'system-roles', component: RoleListView, meta: { perm: 'system:role:manage' } },
  { path: '/system/sessions', name: 'system-sessions', component: SessionListView, meta: { perm: 'system:session:manage' } },
  { path: '/system/audit', name: 'system-audit', component: AuditLogView, meta: { perm: 'system:audit:view' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (!auth.statusLoaded) await loadAuthStatus()

  // 后端关闭鉴权时不做任何拦截（本地调试模式）
  if (!auth.enabled) return to.path === '/login' ? '/' : true

  if (to.path === '/login') return true

  if (!isLoggedIn()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (!auth.loaded) {
    try {
      await loadMe()
    } catch {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }
  const perm = to.meta?.perm
  if (perm && !hasPerm(perm)) return '/'
  return true
})

// 令牌失效 / 被强制下线：统一跳登录页并给出原因
window.addEventListener('ks-auth-expired', (e) => {
  clearTokens()
  auth.user = null
  auth.permissions = []
  auth.roles = []
  auth.loaded = false
  const msg = e?.detail?.message || '登录已失效，请重新登录'
  sessionStorage.setItem('ks.login.reason', msg)
  if (router.currentRoute.value.path !== '/login') {
    router.replace('/login')
  }
})

export default router
export { isAuthed }
