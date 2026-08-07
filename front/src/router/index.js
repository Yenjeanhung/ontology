import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../components/HomeView.vue'
import KbList from '../components/KbList.vue'
import KbDetail from '../components/KbDetail.vue'
import FileLibrary from '../components/FileLibrary.vue'
import QueryView from '../components/QueryView.vue'
import AgentView from '../components/AgentView.vue'
import VectorDataView from '../components/VectorDataView.vue'
import GraphView from '../components/GraphView.vue'
import AttributeTemplateList from '../components/ontology/AttributeTemplateList.vue'
import OntologyManagePage from '../components/ontology/OntologyManagePage.vue'
import RelationDictPage from '../components/ontology/RelationDictPage.vue'
import ConstraintPage from '../components/ontology/ConstraintPage.vue'
import SuggestionListPage from '../components/ontology/SuggestionListPage.vue'
import EntityListPage from '../components/entity/EntityListPage.vue'
import EntityDetailPage from '../components/entity/EntityDetailPage.vue'
import RelationListPage from '../components/entity/RelationListPage.vue'

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
  // 知识库
  { path: '/kb', name: 'kb', component: KbList, meta: { keepAlive: true } },
  { path: '/kb/:kbId', name: 'kb-detail', component: KbDetail, props: true, meta: { keepAlive: true } },
  // 其他
  { path: '/files', name: 'files', component: FileLibrary, meta: { keepAlive: true } },
  { path: '/query', name: 'query', component: QueryView, meta: { keepAlive: true } },
  { path: '/agent', name: 'agent', component: AgentView, meta: { keepAlive: true } },
  { path: '/vectors', name: 'vectors', component: VectorDataView, meta: { keepAlive: true } },
  { path: '/graph', name: 'graph', component: GraphView, meta: { keepAlive: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
