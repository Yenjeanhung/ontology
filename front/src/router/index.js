import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../components/HomeView.vue'
import KbList from '../components/KbList.vue'
import KbDetail from '../components/KbDetail.vue'
import FileLibrary from '../components/FileLibrary.vue'
import QueryView from '../components/QueryView.vue'
import VectorDataView from '../components/VectorDataView.vue'
import GraphView from '../components/GraphView.vue'
import OntologyCategoryList from '../components/ontology/OntologyCategoryList.vue'
import OntologyCategoryDetail from '../components/ontology/OntologyCategoryDetail.vue'
import AttributeTemplateList from '../components/ontology/AttributeTemplateList.vue'
import EntityListPage from '../components/entity/EntityListPage.vue'
import EntityDetailPage from '../components/entity/EntityDetailPage.vue'
import RelationListPage from '../components/entity/RelationListPage.vue'

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/files', name: 'files', component: FileLibrary, meta: { keepAlive: true } },
  { path: '/kb', name: 'kb', component: KbList, meta: { keepAlive: true } },
  { path: '/kb/:kbId', name: 'kb-detail', component: KbDetail, props: true, meta: { keepAlive: true } },
  { path: '/query', name: 'query', component: QueryView, meta: { keepAlive: true } },
  { path: '/vectors', name: 'vectors', component: VectorDataView, meta: { keepAlive: true } },
  { path: '/graph', name: 'graph', component: GraphView, meta: { keepAlive: true } },
  { path: '/ontology-categories', name: 'ontology-categories', component: OntologyCategoryList, meta: { keepAlive: true } },
  { path: '/ontology-categories/:categoryId', name: 'ontology-category-detail', component: OntologyCategoryDetail, props: true },
  { path: '/attribute-templates', name: 'attribute-templates', component: AttributeTemplateList, meta: { keepAlive: true } },
  { path: '/entities', name: 'entities', component: EntityListPage, meta: { keepAlive: true } },
  { path: '/entities/:entityId', name: 'entity-detail', component: EntityDetailPage, props: true },
  { path: '/relations', name: 'relations', component: RelationListPage, meta: { keepAlive: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
