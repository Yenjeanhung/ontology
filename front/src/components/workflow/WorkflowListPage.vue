<script setup>
import { onMounted, onActivated, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchWorkflows, createWorkflow, deleteWorkflow } from '../../api'
import { useToast } from '../../composables/useToast'
import ModalDialog from '../common/ModalDialog.vue'

const router = useRouter()
const toast = useToast()

const workflows = ref([])
const loading = ref(true)

const createDialog = ref({ visible: false, name: '', loading: false })
const deleteDialog = ref({ visible: false, id: null, name: '', loading: false })

onMounted(loadAll)
// keepAlive 缓存页：从编辑页返回时重新拉取，保证新建/删除后列表是最新的
onActivated(loadAll)

async function loadAll() {
  loading.value = true
  try { workflows.value = await fetchWorkflows() } catch { toast.error('加载工作流失败') }
  loading.value = false
}

function openCreate() {
  createDialog.value = { visible: true, name: '', loading: false }
}

async function doCreate() {
  const name = createDialog.value.name.trim()
  if (!name) { toast.error('名称不能为空'); return }
  createDialog.value.loading = true
  try {
    const created = await createWorkflow({ name })
    toast.success('工作流已创建')
    createDialog.value.visible = false
    await loadAll()
    router.push(`/workflows/${created.id}`)
  } catch (err) {
    toast.error(`创建失败: ${err.message}`)
  }
  createDialog.value.loading = false
}

function askDelete(w) {
  deleteDialog.value = { visible: true, id: w.id, name: w.name, loading: false }
}

async function doDelete() {
  deleteDialog.value.loading = true
  try {
    await deleteWorkflow(deleteDialog.value.id)
    toast.success('已删除')
    deleteDialog.value.visible = false
    await loadAll()
  } catch (err) {
    toast.error(`删除失败: ${err.message}`)
  }
  deleteDialog.value.loading = false
}
</script>

<template>
  <div class="wf-page">
    <div class="page-head">
      <div>
        <h3>工作流</h3>
        <p class="desc">把智能体、实体服务等拖到画布上连线组装，保存后一键执行。</p>
      </div>
      <div class="head-actions">
        <button class="btn primary" @click="openCreate">＋ 新建工作流</button>
      </div>
    </div>

    <div class="wf-grid">
      <div v-for="w in workflows" :key="w.id" class="wf-card" @click="router.push(`/workflows/${w.id}`)">
        <div class="wf-card-top">
          <span class="wf-card-name">{{ w.name }}</span>
          <button class="btn sm" @click.stop="askDelete(w)">删除</button>
        </div>
        <div class="wf-card-desc" v-if="w.description">{{ w.description }}</div>
        <div class="wf-card-meta">
          <span class="meta-tag">节点 {{ w.node_count }}</span>
          <span class="meta-tag">连线 {{ w.edge_count }}</span>
          <span class="meta-tag time">{{ w.updated_at || w.created_at }}</span>
        </div>
      </div>

      <div class="wf-empty" v-if="!loading && !workflows.length">
        暂无工作流，点击右上角「新建工作流」开始编排
      </div>
      <div class="wf-empty" v-if="loading">加载中...</div>
    </div>

    <ModalDialog
      v-model="createDialog.visible"
      title="新建工作流"
      confirm-text="创建"
      :confirm-loading="createDialog.loading"
      @confirm="doCreate"
    >
      <div class="form">
        <div class="field">
          <label>名称 <span class="req">*</span></label>
          <input type="text" v-model="createDialog.name" placeholder="如：财务智能分析" @keydown.enter="doCreate">
        </div>
      </div>
    </ModalDialog>

    <ModalDialog
      v-model="deleteDialog.visible"
      title="删除工作流"
      confirm-text="删除"
      confirm-variant="danger"
      :confirm-loading="deleteDialog.loading"
      @confirm="doDelete"
    >
      <div class="del-body">确定删除工作流「{{ deleteDialog.name }}」吗？该操作不可撤销。</div>
    </ModalDialog>
  </div>
</template>

<style scoped>
.wf-page { display: flex; flex-direction: column; gap: 20px; max-width: 1160px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-head h3 { font-size: 18px; font-weight: 700; color: var(--c-fg); margin: 0 0 4px; }
.desc { font-size: 13px; color: var(--c-secondary); margin: 0; }
.head-actions { display: flex; gap: 8px; }

.wf-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.wf-card {
  padding: 16px; border: 1px solid var(--c-border); border-radius: 14px; background: var(--c-panel);
  cursor: pointer; transition: border-color 150ms, box-shadow 150ms;
}
.wf-card:hover { border-color: var(--c-accent); box-shadow: 0 4px 14px rgba(0,0,0,.06); }
.wf-card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.wf-card-name { font-size: 15px; font-weight: 700; color: var(--c-fg); }
.wf-card-desc { font-size: 12px; color: var(--c-secondary); margin-top: 6px; line-height: 1.5; }
.wf-card-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 12px; }
.meta-tag { font-size: 11px; color: var(--c-secondary); background: var(--c-muted); padding: 2px 8px; border-radius: 4px; }
.meta-tag.time { color: var(--c-secondary); opacity: .8; }
.wf-empty { grid-column: 1 / -1; text-align: center; padding: 60px 16px; color: var(--c-secondary); font-size: 13px; }

.form { padding: 4px 0; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.field input { padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px); font-size: 13px; font-family: var(--font); outline: none; background: var(--c-bg); color: var(--c-fg); }
.field input:focus { border-color: var(--c-accent); }
.req { color: var(--c-danger); }
.del-body { font-size: 13px; color: var(--c-fg); line-height: 1.6; }
</style>
