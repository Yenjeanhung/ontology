<script setup>
// 评测集管理：列表 + 条目表格（行内编辑/启停/来源标记）+ 上传导入 + 导出 + 发起评测
import { onMounted, onActivated, ref, watch } from 'vue'
import { fetchKbs } from '../../api'
import { useToast } from '../../composables/useToast'
import ModalDialog from '../common/ModalDialog.vue'
import Pagination from '../common/Pagination.vue'
import {
  fetchTestsets, createTestset, updateTestset, deleteTestset,
  fetchTestsetItems, addTestsetItem, updateTestsetItem, deleteTestsetItem,
  importTestsetItems, exportTestsetItems, downloadBlob,
} from '../../api/eval'

const emit = defineEmits(['start-eval'])
const toast = useToast()

const sets = ref([])
const loading = ref(true)
const kbs = ref([])
const current = ref(null)          // 当前打开的评测集
const items = ref([])
const itemsTotal = ref(0)
const page = ref(1)
const pageSize = 50
const keyword = ref('')
const originFilter = ref('')
const itemLoading = ref(false)
const fileInput = ref(null)

const editDialog = ref({ visible: false, id: null, name: '', description: '', default_kb_id: '', loading: false })
const itemDialog = ref({ visible: false, id: null, question: '', reference: '', kb_id: '', loading: false })
const deleteDialog = ref({ visible: false, id: null, name: '', loading: false })
const editing = ref(null)          // 行内编辑：{ id, field, value }

const ORIGIN_LABEL = { manual: '手工', upload: '导入', synthesized: '合成', badcase: '⚡Badcase回流' }

onMounted(loadAll)
onActivated(loadAll)
watch([keyword, originFilter], () => { page.value = 1; loadItems() })
watch(page, loadItems)

async function loadAll() {
  loading.value = true
  try {
    sets.value = await fetchTestsets()
    if (!kbs.value.length) kbs.value = await fetchKbs().catch(() => [])
    if (current.value) {
      const found = sets.value.find(s => s.id === current.value?.id)
      if (found) current.value = found
    }
  } catch (e) { toast.error(e.message) }
  loading.value = false
}

async function openSet(s) {
  current.value = s
  keyword.value = ''
  originFilter.value = ''
  page.value = 1
  await loadItems()
}

async function loadItems() {
  if (!current.value) return
  itemLoading.value = true
  try {
    const res = await fetchTestsetItems(current.value.id, {
      page: page.value, page_size: pageSize,
      keyword: keyword.value.trim(), origin: originFilter.value,
    })
    items.value = res.items
    itemsTotal.value = res.total
  } catch (e) { toast.error(e.message) }
  itemLoading.value = false
}

// ── 评测集 CRUD ──
function openCreate() {
  editDialog.value = { visible: true, id: null, name: '', description: '', default_kb_id: '', loading: false }
}
function openEdit(s) {
  editDialog.value = { visible: true, id: s.id, name: s.name, description: s.description, default_kb_id: s.default_kb_id, loading: false }
}
async function saveEdit() {
  const d = editDialog.value
  if (!d.name.trim()) { toast.error('名称不能为空'); return }
  d.loading = true
  try {
    const payload = { name: d.name.trim(), description: d.description, default_kb_id: d.default_kb_id }
    if (d.id) await updateTestset(d.id, payload)
    else await createTestset(payload)
    toast.success(d.id ? '已保存' : '已创建')
    d.visible = false
    await loadAll()
    if (!d.id) {
      const created = sets.value.find(s => s.name === payload.name)
      if (created) await openSet(created)
    }
  } catch (e) { toast.error(e.message) }
  d.loading = false
}
function askDelete(s) { deleteDialog.value = { visible: true, id: s.id, name: s.name, loading: false } }
async function doDelete() {
  const d = deleteDialog.value
  d.loading = true
  try {
    await deleteTestset(d.id)
    toast.success('已删除')
    d.visible = false
    if (current.value?.id === d.id) current.value = null
    await loadAll()
  } catch (e) { toast.error(e.message) }
  d.loading = false
}

// ── 条目操作 ──
function openAddItem() {
  itemDialog.value = { visible: true, id: null, question: '', reference: '', kb_id: '', loading: false }
}
function openEditItem(it) {
  itemDialog.value = { visible: true, id: it.id, question: it.question, reference: it.reference, kb_id: it.kb_id, loading: false }
}
async function saveItem() {
  const d = itemDialog.value
  if (!d.question.trim()) { toast.error('问题不能为空'); return }
  d.loading = true
  try {
    const payload = { question: d.question.trim(), reference: d.reference, kb_id: d.kb_id }
    if (d.id) await updateTestsetItem(current.value.id, d.id, payload)
    else await addTestsetItem(current.value.id, payload)
    toast.success('已保存')
    d.visible = false
    await loadItems()
    loadAll()
  } catch (e) { toast.error(e.message) }
  d.loading = false
}

async function toggleItem(it) {
  try {
    await updateTestsetItem(current.value.id, it.id, { enabled: !it.enabled })
    it.enabled = !it.enabled
  } catch (e) { toast.error(e.message) }
}

async function removeItem(it) {
  try {
    await deleteTestsetItem(current.value.id, it.id)
    toast.success('已删除')
    await loadItems()
    loadAll()
  } catch (e) { toast.error(e.message) }
}

// ── 导入 / 导出 ──
function pickFile() { fileInput.value?.click() }
async function onFilePicked(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file || !current.value) return
  try {
    const res = await importTestsetItems(current.value.id, file)
    toast.success(`导入完成：新增 ${res.added}，重复跳过 ${res.duplicates}，无效 ${res.invalid}`)
    await loadItems()
    loadAll()
  } catch (err) { toast.error(err.message) }
}

async function doExport(format) {
  try {
    const blob = await exportTestsetItems(current.value.id, format)
    downloadBlob(blob, `${current.value.name}.${format}`)
  } catch (e) { toast.error(e.message) }
}

function kbName(id) { return kbs.value.find(k => k.id === id)?.name || id }
function fmtTime(ts) {
  if (!ts) return ''
  return String(ts).replace('T', ' ').slice(5, 16)
}
</script>

<template>
  <div class="ts-wrap">
    <!-- 评测集列表 -->
    <div class="ts-list card" v-if="!current">
      <div class="bar">
        <button class="btn primary" @click="openCreate">+ 新建评测集</button>
        <span class="hint">支持 JSONL / CSV / XLSX 导入；列名兼容 question/user_input、reference/ground_truth</span>
      </div>
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!sets.length" class="empty">暂无评测集，点击「新建评测集」或创建后导入现有 golden.jsonl</div>
      <table v-else class="tbl">
        <thead>
          <tr><th>名称</th><th>条目数</th><th>来源</th><th>最近评测得分</th><th>更新时间</th><th class="op">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="s in sets" :key="s.id">
            <td>
              <a class="link" @click="openSet(s)">{{ s.name }}</a>
              <div class="sub" v-if="s.description">{{ s.description }}</div>
            </td>
            <td>{{ s.item_count }}</td>
            <td><span class="tag">{{ ORIGIN_LABEL[s.source] || s.source }}</span></td>
            <td>
              <template v-if="s.last_run">
                <span v-for="(v, k) in s.last_run.metrics" :key="k" class="score-chip">{{ k }} {{ v }}</span>
              </template>
              <span v-else class="dim">—</span>
            </td>
            <td class="dim">{{ fmtTime(s.updated_at) }}</td>
            <td>
              <button class="btn sm" @click="openSet(s)">条目</button>
              <button class="btn sm" @click="emit('start-eval', s.id)">发起评测</button>
              <button class="btn sm" @click="openEdit(s)">编辑</button>
              <button class="btn sm danger" @click="askDelete(s)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 条目管理 -->
    <div class="ts-items card" v-else>
      <div class="bar">
        <button class="btn" @click="current = null">← 返回列表</button>
        <strong>{{ current.name }}</strong>
        <span class="dim">{{ itemsTotal }} 条</span>
        <span class="spacer"></span>
        <input v-model="keyword" class="ipt" placeholder="搜索问题…" />
        <select v-model="originFilter" class="ipt sel">
          <option value="">全部来源</option>
          <option value="manual">手工</option>
          <option value="upload">导入</option>
          <option value="synthesized">合成</option>
          <option value="badcase">Badcase回流</option>
        </select>
        <button class="btn" @click="pickFile">上传导入</button>
        <input ref="fileInput" type="file" accept=".jsonl,.json,.csv,.xlsx,.xls" hidden @change="onFilePicked" />
        <button class="btn" @click="doExport('jsonl')">导出JSONL</button>
        <button class="btn" @click="doExport('csv')">导出CSV</button>
        <button class="btn primary" @click="emit('start-eval', current.id)">发起评测</button>
      </div>

      <div class="bar">
        <button class="btn primary" @click="openAddItem">+ 新增条目</button>
      </div>

      <div v-if="itemLoading" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">暂无条目</div>
      <table v-else class="tbl">
        <thead>
          <tr><th style="width:45%">问题</th><th style="width:30%">标准答案</th><th>知识库</th><th>来源</th><th>启用</th><th style="width:150px">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="it in items" :key="it.id">
            <td class="q-cell" :title="it.question"><span class="clamp">{{ it.question }}</span></td>
            <td class="q-cell dim" :title="it.reference"><span class="clamp">{{ it.reference || '—' }}</span></td>
            <td class="dim">{{ it.kb_id ? kbName(it.kb_id) : '默认' }}</td>
            <td>
              <span class="tag" :class="{ flow: it.origin === 'badcase' }">{{ ORIGIN_LABEL[it.origin] || it.origin }}</span>
            </td>
            <td>
              <label class="switch">
                <input type="checkbox" :checked="it.enabled" @change="toggleItem(it)" />
                <span></span>
              </label>
            </td>
            <td>
              <button class="btn sm" @click="openEditItem(it)">编辑</button>
              <button class="btn sm danger" @click="removeItem(it)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <Pagination v-model:page="page" :total="itemsTotal" :page-size="pageSize" />
    </div>

    <!-- 评测集编辑弹窗 -->
    <ModalDialog v-model="editDialog.visible" :title="editDialog.id ? '编辑评测集' : '新建评测集'" size="md">
      <div class="form">
        <label>名称 <em>*</em></label>
        <input v-model="editDialog.name" class="ipt" placeholder="如：航班运行核心评测集" />
        <label>描述</label>
        <textarea v-model="editDialog.description" class="ipt" rows="2" />
        <label>默认知识库</label>
        <select v-model="editDialog.default_kb_id" class="ipt">
          <option value="">（发起评测时再选）</option>
          <option v-for="k in kbs" :key="k.id" :value="k.id">{{ k.name }}</option>
        </select>
      </div>
      <template #footer>
        <button class="btn" @click="editDialog.visible = false">取消</button>
        <button class="btn primary" :disabled="editDialog.loading" @click="saveEdit">保存</button>
      </template>
    </ModalDialog>

    <!-- 条目编辑弹窗 -->
    <ModalDialog v-model="itemDialog.visible" :title="itemDialog.id ? '编辑条目' : '新增条目'" size="lg">
      <div class="form">
        <label>问题 <em>*</em></label>
        <textarea v-model="itemDialog.question" class="ipt" rows="2" placeholder="用户提问" />
        <label>标准答案（reference）</label>
        <textarea v-model="itemDialog.reference" class="ipt" rows="4" placeholder="评测集缺标注时，需 reference 的指标会被跳过" />
        <label>知识库覆盖</label>
        <select v-model="itemDialog.kb_id" class="ipt">
          <option value="">用评测集默认知识库</option>
          <option v-for="k in kbs" :key="k.id" :value="k.id">{{ k.name }}</option>
        </select>
      </div>
      <template #footer>
        <button class="btn" @click="itemDialog.visible = false">取消</button>
        <button class="btn primary" :disabled="itemDialog.loading" @click="saveItem">保存</button>
      </template>
    </ModalDialog>

    <!-- 删除确认 -->
    <ModalDialog v-model="deleteDialog.visible" title="删除评测集" size="sm">
      <p>确定删除评测集「{{ deleteDialog.name }}」及其全部条目？</p>
      <template #footer>
        <button class="btn" @click="deleteDialog.visible = false">取消</button>
        <button class="btn danger" :disabled="deleteDialog.loading" @click="doDelete">删除</button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.ts-wrap { display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.card { background: var(--c-panel); border: 1px solid var(--c-border); border-radius: var(--radius); padding: 12px; overflow: auto; }
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.spacer { flex: 1; }
.hint { font-size: 12px; color: var(--c-secondary); }
.dim { color: var(--c-secondary); font-size: 12px; }
.sub { font-size: 12px; color: var(--c-secondary); }
.empty { padding: 40px 0; text-align: center; color: var(--c-secondary); }
.link { color: var(--c-accent); cursor: pointer; font-weight: 500; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; padding: 8px; border-bottom: 1px solid var(--c-border); color: var(--c-secondary); font-weight: 500; white-space: nowrap; }
.tbl td { padding: 8px; border-bottom: 1px solid var(--c-border); vertical-align: top; }
.q-cell { max-width: 420px; }
.clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tbl th.op, .tbl td.op { white-space: nowrap; }
.tbl td:last-child { white-space: nowrap; }
.btn.sm { padding: 3px 8px; margin-right: 4px; }
.btn.danger { color: var(--c-danger) !important; }
.ipt { padding: 5px 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); font-size: 12px; background: var(--c-panel); color: var(--c-fg); }
.ipt.sel { min-width: 110px; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; background: var(--c-muted); color: var(--c-secondary); }
.tag.flow { background: rgba(240, 160, 20, 0.15); color: #d48806; }
.score-chip { display: inline-block; font-size: 11px; margin-right: 6px; padding: 1px 6px; border-radius: 4px; background: color-mix(in srgb, var(--c-accent) 12%, transparent); color: var(--c-accent); }
.switch { position: relative; display: inline-block; width: 34px; height: 18px; }
.switch input { opacity: 0; width: 0; height: 0; }
.switch span { position: absolute; inset: 0; background: var(--c-border); border-radius: 10px; cursor: pointer; transition: 0.2s; }
.switch span::before { content: ''; position: absolute; width: 14px; height: 14px; left: 2px; top: 2px; background: #fff; border-radius: 50%; transition: 0.2s; }
.switch input:checked + span { background: var(--c-accent); }
.switch input:checked + span::before { transform: translateX(16px); }
.form { display: flex; flex-direction: column; gap: 6px; }
.form label { font-size: 12px; color: var(--c-secondary); margin-top: 6px; }
.form label em { color: var(--c-danger); font-style: normal; }
</style>
