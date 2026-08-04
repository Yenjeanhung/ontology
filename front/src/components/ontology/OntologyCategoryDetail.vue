<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getOntologyCategoryDetail, updateOntologyCategory } from '../../api'
import TabNav from '../common/TabNav.vue'
import OntologyEditor from './OntologyEditor.vue'
import RelationDictEditor from './RelationDictEditor.vue'
import ConstraintEditor from './ConstraintEditor.vue'

const props = defineProps({
  categoryId: { type: String, default: '' },
})

const router = useRouter()
const detail = ref(null)
const loading = ref(false)
const loadError = ref('')
const activeTab = ref('info')

// 基本信息 编辑
const editingInfo = ref(false)
const infoName = ref('')
const infoDesc = ref('')
const savingInfo = ref(false)

const tabs = computed(() => {
  if (!detail.value) return []
  const ontCount = detail.value.ontologies?.length || 0
  const relCount = detail.value.relations?.length || 0
  const conCount = detail.value.constraints?.length || 0
  return [
    { key: 'info', label: '基本信息' },
    { key: 'ontology', label: '本体', badge: ontCount || '' },
    { key: 'relation', label: '关系字典', badge: relCount || '' },
    { key: 'constraint', label: '三元组约束', badge: conCount || '' },
  ]
})

async function load() {
  if (!props.categoryId) return
  loading.value = true
  loadError.value = ''
  try {
    const data = await getOntologyCategoryDetail(props.categoryId)
    if (!data) {
      loadError.value = '未找到该本体类别'
      detail.value = null
    } else {
      detail.value = data
      infoName.value = data.name
      infoDesc.value = data.description || ''
    }
  } catch (e) {
    loadError.value = '加载失败：' + e.message
    detail.value = null
  } finally {
    loading.value = false
  }
}

function startEditInfo() {
  infoName.value = detail.value.name
  infoDesc.value = detail.value.description || ''
  editingInfo.value = true
}

async function saveInfo() {
  if (!infoName.value.trim()) return
  savingInfo.value = true
  try {
    await updateOntologyCategory(props.categoryId, {
      name: infoName.value.trim(),
      description: infoDesc.value.trim(),
    })
    if (detail.value) {
      detail.value.name = infoName.value.trim()
      detail.value.description = infoDesc.value.trim()
    }
    editingInfo.value = false
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    savingInfo.value = false
  }
}

function cancelEditInfo() {
  editingInfo.value = false
  infoName.value = detail.value?.name || ''
  infoDesc.value = detail.value?.description || ''
}

// 子组件变更后刷新详情
function onSubChanged() {
  load()
}

watch(() => props.categoryId, load)
onMounted(load)
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="title-area">
        <button class="back-btn" @click="router.push('/ontology-categories')" title="返回">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
        </button>
        <div class="title-text">
          <h2 class="page-title">{{ detail?.name || '本体类别详情' }}</h2>
          <span class="page-subtitle" v-if="detail">{{ detail.ontologies?.length || 0 }} 个本体 · {{ detail.relations?.length || 0 }} 个关系 · {{ detail.constraints?.length || 0 }} 个三元组</span>
        </div>
      </div>
    </div>

    <div v-if="loading" class="loading-state"><span class="spinner"></span> 加载中...</div>
    <div v-else-if="loadError" class="error-state">{{ loadError }}</div>

    <template v-else-if="detail">
      <TabNav :tabs="tabs" v-model="activeTab" />

      <!-- Tab1 基本信息 -->
      <div v-if="activeTab === 'info'" class="tab-panel">
        <div class="info-card">
          <div class="info-row">
            <span class="info-label">类别名称</span>
            <div class="info-value">
              <input v-if="editingInfo" type="text" v-model="infoName" class="info-input">
              <span v-else>{{ detail.name }}</span>
            </div>
          </div>
          <div class="info-row">
            <span class="info-label">描述</span>
            <div class="info-value">
              <textarea v-if="editingInfo" v-model="infoDesc" rows="3" class="info-input"></textarea>
              <span v-else>{{ detail.description || '—' }}</span>
            </div>
          </div>
          <div class="info-row">
            <span class="info-label">类别 ID</span>
            <span class="info-value mono">{{ detail.id }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">类型</span>
            <span class="info-value">
              <span v-if="detail.is_system" class="tag system">系统内置</span>
              <span v-else class="tag custom">自定义</span>
            </span>
          </div>
          <div class="info-row" v-if="detail.kb_bindings?.length">
            <span class="info-label">绑定知识库</span>
            <span class="info-value">{{ detail.kb_bindings.length }} 个</span>
          </div>

          <div class="info-actions">
            <template v-if="editingInfo">
              <button class="btn" @click="cancelEditInfo">取消</button>
              <button class="btn primary" @click="saveInfo" :disabled="savingInfo || !infoName.trim()">
                <span v-if="savingInfo" class="spinner"></span> 保存
              </button>
            </template>
            <button v-else class="btn" @click="startEditInfo">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
              编辑
            </button>
          </div>
        </div>
      </div>

      <!-- Tab2 本体编辑 -->
      <div v-else-if="activeTab === 'ontology'" class="tab-panel">
        <OntologyEditor :category-id="categoryId" :detail="detail" @changed="onSubChanged" />
      </div>

      <!-- Tab3 关系字典 -->
      <div v-else-if="activeTab === 'relation'" class="tab-panel">
        <RelationDictEditor :category-id="categoryId" :relations="detail.relations" @changed="onSubChanged" />
      </div>

      <!-- Tab4 三元组约束 -->
      <div v-else-if="activeTab === 'constraint'" class="tab-panel">
        <ConstraintEditor
          :category-id="categoryId"
          :ontologies="detail.ontologies"
          :relations="detail.relations"
          :constraints="detail.constraints"
          @changed="onSubChanged"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; }
.page-head {
  display: flex; align-items: flex-end; justify-content: space-between; gap: 12px;
  padding-bottom: 12px; border-bottom: 1px solid var(--c-border);
}
.title-area { display: flex; align-items: center; gap: 12px; }
.back-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 34px; height: 34px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-secondary); cursor: pointer; flex-shrink: 0;
  transition: background 150ms, color 150ms;
}
.back-btn:hover { background: var(--c-muted); color: var(--c-fg); }
.title-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }

.loading-state, .error-state { padding: 40px; text-align: center; color: var(--c-secondary); font-size: 14px; }
.error-state { color: var(--c-danger); }

.tab-panel { padding-top: 4px; }

.info-card {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel); padding: 4px 20px; max-width: 680px;
}
.info-row {
  display: flex; align-items: flex-start; gap: 16px;
  padding: 14px 0; border-bottom: 1px solid var(--c-border);
}
.info-row:last-child { border-bottom: 0; }
.info-label { flex: 0 0 100px; font-size: 13px; font-weight: 600; color: var(--c-secondary); padding-top: 2px; }
.info-value { flex: 1; min-width: 0; font-size: 14px; color: var(--c-fg); word-break: break-word; }
.info-value.mono { font-family: ui-monospace, 'SF Mono', Consolas, monospace; font-size: 12px; color: var(--c-secondary); }
.info-input {
  width: 100%; padding: 7px 11px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-fg); font-size: 14px; font-family: var(--font); outline: none;
}
.info-input:focus { border-color: var(--c-fg); }
.info-input[type='text'] { width: 100%; }
.tag { display: inline-flex; padding: 2px 9px; border-radius: 10px; font-size: 12px; font-weight: 600; }
.tag.system { background: rgba(161, 98, 7, 0.12); color: var(--c-accent); }
.tag.custom { background: var(--c-muted); color: var(--c-secondary); }
.info-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 14px 0; }
</style>
