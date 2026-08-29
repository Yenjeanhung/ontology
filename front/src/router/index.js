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
import OntologyManagePage from '../components/ontology/OntologyManagePage.vue'
import RelationDictPage from '../components/ontology/RelationDictPage.vue'
import ConstraintPage from '../components/ontology/ConstraintPage.vue'
import SuggestionListPage from '../components/ontology/SuggestionListPage.vue'
import ModelConfigPage from '../components/config/ModelConfigPage.vue'
import MonitorPage from '../components/monitor/MonitorPage.vue'
import EntityListPage from '../components/entity/EntityListPage.vue'
import EntityDetailPage from '../components/entity/EntityDetailPage.vue'
import RelationListPage from '../components/entity/RelationListPage.vue'
import GraphCleanupPage from '../components/entity/GraphCleanupPage.vue'
import WorkflowListPage from '../components/workflow/WorkflowListPage.vue'
import WorkflowEditorPage from '../components/workflow/WorkflowEditorPage.vue'
import ServiceEditorPage from '../components/ontology/ServiceEditorPage.vue'
import ScheduleListPage from '../components/scheduler/ScheduleListPage.vue'
import ScheduleEditorPage from '../components/scheduler/ScheduleEditorPage.vue'
import HumanTaskCenterPage from '../components/workflow/HumanTaskCenter.vue'

const routes = [
  { path: '/', name: 'home', component: HomeView },
  // 本体管理（大菜单）
  { path: '/ontology/templates', name: 'ontology-templates', component: AttributeTemplateList, meta: { keepAlive: true } },
  { path: '/ontology/ontologies', name: 'ontology-ontologies', component: OntologyManagePage, meta: { keepAlive: true } },
  { path: '/ontology/relations-dict', name: 'ontology-relations-dict', component: RelationDictPage, meta: { keepAlive: true } },
  { path: '/ontology/constraints', name: 'ontology-constraints', component: ConstraintPage, meta: { keepAlive: true } },
  { path: '/ontology/suggestions', name: 'ontology-suggestions', component: SuggestionListPage, meta: { keepAlive: true } },
  // 实体管理
  { path: '/entities', name: 'entities', component: EntityListPage, meta: { keepAlive: true } },
  { path: '/entities/:entityId', name: 'entity-detail', component: EntityDetailPage, props: true },
  { path: '/entities/relations', name: 'entity-relations', component: RelationListPage, meta: { keepAlive: true } },
  { path: '/graph-cleanup', name: 'graph-cleanup', component: GraphCleanupPage, meta: { keepAlive: true } },
  // 知识库
  { path: '/kb', name: 'kb', component: KbList, meta: { keepAlive: true } },
  { path: '/kb/:kbId', name: 'kb-detail', component: KbDetail, props: true, meta: { keepAlive: true } },
  // 其他
  { path: '/files', name: 'files', component: FileLibrary, meta: { keepAlive: true } },
  { path: '/query', name: 'query', component: QueryView, meta: { keepAlive: true } },
  { path: '/agent', name: 'agent', component: AgentView, meta: { keepAlive: true } },
  { path: '/agent/configs', name: 'agent-configs', component: AgentListPage, meta: { keepAlive: true } },
  { path: '/agent/skills', name: 'agent-skills', component: SkillListPage, meta: { keepAlive: true } },
  { path: '/vectors', name: 'vectors', component: VectorDataView, meta: { keepAlive: true } },
  { path: '/vectors/:fileId', name: 'vector-file-detail', component: VectorFileDetail, props: true },
  { path: '/graph', name: 'graph', component: GraphView, meta: { keepAlive: true } },
  // 工作流
  { path: '/workflows', name: 'workflows', component: WorkflowListPage, meta: { keepAlive: true } },
  { path: '/workflows/:workflowId', name: 'workflow-editor', component: WorkflowEditorPage, props: true },
  // 人工节点待办中心
  { path: '/human-tasks', name: 'human-tasks', component: HumanTaskCenterPage, meta: { keepAlive: true } },
  // 定时管理
  { path: '/schedules', name: 'schedules', component: ScheduleListPage, meta: { keepAlive: true } },
  { path: '/schedules/:scheduleId', name: 'schedule-editor', component: ScheduleEditorPage, props: true, meta: { keepAlive: false } },
  // 服务编辑器（本体服务 / 实体自定义动作，独立大页面）
  { path: '/ontology-services/new', name: 'ontology-service-new', component: ServiceEditorPage, meta: { keepAlive: false, fullscreen: true } },
  { path: '/ontology-services/:serviceId/edit', name: 'ontology-service-edit', component: ServiceEditorPage, props: true, meta: { keepAlive: false, fullscreen: true } },
  { path: '/entity-services/new', name: 'entity-service-new', component: ServiceEditorPage, meta: { keepAlive: false, fullscreen: true } },
  { path: '/entity-services/:serviceId/edit', name: 'entity-service-edit', component: ServiceEditorPage, props: true, meta: { keepAlive: false, fullscreen: true } },
  // 配置
  { path: '/config/models', name: 'config-models', component: ModelConfigPage, meta: { keepAlive: true } },
  { path: '/config/monitor', name: 'config-monitor', component: MonitorPage, meta: { keepAlive: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
