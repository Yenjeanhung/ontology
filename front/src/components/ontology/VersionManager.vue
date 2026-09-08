<script setup>
import { ref, onMounted, watch } from 'vue'
import { fetchVersions, createVersion, rollbackVersion, deleteVersion } from '../../api'
import { useToast } from '../../composables/useToast'

const props = defineProps({
  categoryId: { type: String, required: true },
})

const toast = useToast()
const versions = ref([])
const loading = ref(false)
const creating = ref(false)
const note = ref('')
const rollbackingId = ref('')
const deletingId = ref('')
const pendingDelete = ref(null)

async function load() {
  if (!props.categoryId) return
  loading.value = true
  try {
    versions.value = await fetchVersions(props.categoryId)
  } catch {
    versions.value = []
  } finally {
    loading.value = false
  }
}

async function create() {
  creating.value = true
  try {
    await createVersion(props.categoryId, { note: note.value, source: 'manual' })
    note.value = ''
    toast.success('版本已发布')
    await load()
  } catch (e) {
    toast.error('发布版本失败：' + e.message)
  } finally {
    creating.value = false
  }
}

async function rollback(v) {
  if (!confirm(`确认回滚到版本 v${v.version_no}？\n将把该版本快照还原到当前定义层（保留实体引用），操作不可撤销（但可再次发布新版本）。`)) return
  rollbackingId.value = v.id
  try {
    await rollbackVersion(props.categoryId, v.id)
    toast.success('已回滚到 v' + v.version_no)
    await load()
  } catch (e) {
    toast.error('回滚失败：' + e.message)
  } finally {
    rollbackingId.value = ''
  }
}

function askDelete(v) {
  pendingDelete.value = v
}

function cancelDelete() {
  pendingDelete.value = null
}

async function confirmDelete() {
  const v = pendingDelete.value
  if (!v) return
  deletingId.value = v.id
  try {
    await deleteVersion(props.categoryId, v.id)
    toast.success(`已删除版本 v${v.version_no}`)
    pendingDelete.value = null
    await load()
  } catch (e) {
    toast.error('删除版本失败：' + e.message)
  } finally {
    deletingId.value = ''
  }
}

watch(() => props.categoryId, load)
onMounted(load)
</script>

<template>
  <div class="vm-root">
    <div class="vm-head">
      <span class="vm-tip">版本快照：发布的是整个「本体类别定义层」（含本体/属性/关系、接口、对象视图、服务/函数等）的不可变快照，并非单个对象视图的发布版本；可随时回滚，回滚只还原定义层、保留已录入的实体数据。</span>
      <div class="vm-create">
        <input type="text" v-model="note" placeholder="版本说明（可选）" @keydown.enter="create">
        <button class="btn primary sm" :disabled="creating || !categoryId" @click="create">
          <span v-if="creating" class="spinner"></span> 发布版本
        </button>
      </div>
    </div>

    <div v-if="loading" class="vm-hint">加载中...</div>
    <div v-else-if="!versions.length" class="vm-empty">暂无版本。点击「发布版本」保存当前整个本体类别定义层的快照。</div>

    <div v-else class="vm-list">
      <div v-for="v in versions" :key="v.id" class="vm-card">
        <div class="vm-card-main">
          <div class="vm-title">v{{ v.version_no }} <span class="vm-src">{{ v.source }}</span></div>
          <div class="vm-meta">
            <span v-if="v.note">{{ v.note }}</span>
            <span class="vm-time">{{ (v.created_at || '').replace('T', ' ') }}</span>
          </div>
          <div class="vm-stats" v-if="v.counts">
            <span class="vm-stats-label">包含：</span>
            <span v-for="(n, k) in v.counts" :key="k" class="vm-stat">{{ k }}: {{ n }}</span>
          </div>
        </div>
        <div class="vm-actions">
          <template v-if="pendingDelete?.id === v.id">
            <span class="vm-del-ask">确认删除该版本？</span>
            <button class="btn sm danger" :disabled="deletingId === v.id" @click="confirmDelete">
              <span v-if="deletingId === v.id" class="spinner"></span> 删除
            </button>
            <button class="btn sm" @click="cancelDelete">取消</button>
          </template>
          <template v-else>
            <button class="btn sm" :disabled="rollbackingId === v.id" @click="rollback(v)">
              <span v-if="rollbackingId === v.id" class="spinner"></span> 回滚到此版本
            </button>
            <button class="btn sm danger outline" :disabled="deletingId === v.id" @click="askDelete(v)" title="删除该版本快照（不影响已录入的实体数据）">删除</button>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vm-root { display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; overflow: hidden; }
.vm-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; flex-shrink: 0; }
.vm-tip { font-size: 12px; color: var(--c-secondary); }
.vm-create { display: flex; gap: 8px; }
.vm-create input { width: 220px; padding: 6px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 13px; outline: none; }
.vm-create input:focus { border-color: var(--c-fg); }
.btn.sm { padding: 5px 11px; font-size: 12px; }
.btn.sm.danger.outline { background: transparent; color: var(--c-danger); border: 1px solid rgba(220, 38, 38, 0.4); }
.btn.sm.danger.outline:hover { background: var(--c-danger); color: #fff; }
.vm-hint { font-size: 12px; color: var(--c-secondary); }
.vm-empty { padding: 28px; text-align: center; color: var(--c-secondary); font-size: 13px; border: 1px dashed var(--c-border); border-radius: var(--radius-sm); }
.vm-list { display: flex; flex-direction: column; gap: 6px; flex: 1; min-height: 0; overflow-y: auto; padding-right: 4px; }
.vm-card { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); flex-shrink: 0; }
.vm-card-main { flex: 1; min-width: 0; }
.vm-title { font-size: 14px; font-weight: 700; color: var(--c-fg); }
.vm-src { font-size: 11px; font-weight: 500; color: var(--c-secondary); background: var(--c-muted); padding: 1px 7px; border-radius: 9px; }
.vm-meta { display: flex; gap: 12px; font-size: 12px; color: var(--c-secondary); margin-top: 3px; flex-wrap: wrap; }
.vm-time { font-family: ui-monospace, Consolas, monospace; }
.vm-stats { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 5px; }
.vm-stats-label { font-size: 11px; color: var(--c-primary); font-weight: 600; }
.vm-stat { font-size: 11px; color: var(--c-secondary); background: var(--c-muted); padding: 1px 7px; border-radius: 9px; }
.vm-actions { flex-shrink: 0; display: flex; gap: 6px; align-items: center; }
.vm-del-ask { font-size: 12px; color: var(--c-danger); margin-right: 2px; }
</style>
