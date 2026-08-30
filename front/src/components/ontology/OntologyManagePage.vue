<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import {
  fetchOntologyCategories,
  getOntologyCategoryDetail,
  createOntologyCategory,
  updateOntologyCategory,
  deleteOntologyCategory,
  downloadOntologyTemplate,
  exportOntologyExcel,
  importOntologyExcel,
  triggerDownload,
} from '../../api'
import OntologyEditor from './OntologyEditor.vue'
import ModalDialog from '../common/ModalDialog.vue'

const search = ref('')
const categories = ref([])
const loadingList = ref(false)

const selectedId = ref('')
const detail = ref(null)
const loadingDetail = ref(false)
const loadError = ref('')

// 基本信息 编辑
const editingInfo = ref(false)
const infoName = ref('')
const infoDesc = ref('')
const savingInfo = ref(false)

// 新建弹窗
const showCreate = ref(false)
const createName = ref('')
const createDesc = ref('')
const creating = ref(false)

// 编辑弹窗
const showEdit = ref(false)
const editId = ref('')
const editName = ref('')
const editDesc = ref('')
const saving = ref(false)

// 删除确认
const showDelete = ref(false)
const deleteTarget = ref(null)
const deleting = ref(false)

// ===== Excel 导入 / 导出 =====
const fileInput = ref(null)
const importing = ref(false)
const exporting = ref(false)
const importReport = ref(null)
const showImportResult = ref(false)
const importError = ref('')

async function onImportFileChange(event) {
  const file = event.target.files?.[0]
  if (!file) return
  importing.value = true
  importError.value = ''
  try {
    // 本体管理页只导入 ontologies 范围（类别/本体/属性/模板绑定）
    const dry = await importOntologyExcel(file, { scope: 'ontologies', dryRun: true })
    if (dry.failed > 0 || dry.total === 0) {
      importReport.value = { ...dry, _dry: true, _file: file }
      showImportResult.value = true
      return
    }
    const real = await importOntologyExcel(file, { scope: 'ontologies', dryRun: false })
    importReport.value = { ...real, _dry: false, _file: file }
    showImportResult.value = true
    await loadCategories()
  } catch (e) {
    importError.value = e.message || '导入失败'
  } finally {
    importing.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

// 干跑通过后，用户点「确认导入」执行真实写入
async function confirmImport() {
  const file = importReport.value?._file
  if (!file) return
  importing.value = true
  try {
    const real = await importOntologyExcel(file, { scope: 'ontologies', dryRun: false })
    importReport.value = { ...real, _dry: false, _file: file }
    await loadCategories()
  } catch (e) {
    importError.value = e.message || '导入失败'
  } finally {
    importing.value = false
  }
}

async function onDownloadTemplate() {
  try {
    const blob = await downloadOntologyTemplate({ scope: 'ontologies', withExample: true })
    triggerDownload(blob, '本体导入模板-本体管理.xlsx')
  } catch (e) {
    importError.value = e.message || '下载模板失败'
  }
}

async function onExportCategory(cat) {
  if (exporting.value) return
  exporting.value = true
  try {
    const blob = await exportOntologyExcel({ scope: 'ontologies', categoryId: cat?.id || null })
    triggerDownload(blob, `本体导出-本体管理-${cat?.name || '全部类别'}.xlsx`)
  } catch (e) {
    importError.value = e.message || '导出失败'
  } finally {
    exporting.value = false
  }
}

function closeImportResult() {
  showImportResult.value = false
  importReport.value = null
}

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return categories.value
  return categories.value.filter(c =>
    c.name.toLowerCase().includes(q) || (c.description || '').toLowerCase().includes(q)
  )
})

async function loadCategories() {
  loadingList.value = true
  try {
    categories.value = await fetchOntologyCategories()
    // 默认选中第一个
    if (!selectedId.value && categories.value.length) {
      selectCategory(categories.value[0].id)
    }
  } catch {
    categories.value = []
  } finally {
    loadingList.value = false
  }
}

async function loadDetail() {
  if (!selectedId.value) return
  loadingDetail.value = true
  loadError.value = ''
  try {
    const data = await getOntologyCategoryDetail(selectedId.value)
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
    loadingDetail.value = false
  }
}

function selectCategory(id) {
  selectedId.value = id
  loadDetail()
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
    await updateOntologyCategory(selectedId.value, {
      name: infoName.value.trim(),
      description: infoDesc.value.trim(),
    })
    if (detail.value) {
      detail.value.name = infoName.value.trim()
      detail.value.description = infoDesc.value.trim()
    }
    editingInfo.value = false
    await loadCategories()
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

function onSubChanged() {
  loadDetail()
}

// 新建类别
function openCreate() {
  createName.value = ''
  createDesc.value = ''
  showCreate.value = true
}

async function submitCreate() {
  const n = createName.value.trim()
  if (!n) return
  creating.value = true
  try {
    const cat = await createOntologyCategory({ name: n, description: createDesc.value.trim() })
    showCreate.value = false
    await loadCategories()
    selectCategory(cat.id)
  } catch (e) {
    alert('创建失败：' + e.message)
  } finally {
    creating.value = false
  }
}

// 编辑类别
function openEdit(cat) {
  editId.value = cat.id
  editName.value = cat.name
  editDesc.value = cat.description || ''
  showEdit.value = true
}

async function submitEdit() {
  const n = editName.value.trim()
  if (!n) return
  saving.value = true
  try {
    await updateOntologyCategory(editId.value, { name: n, description: editDesc.value.trim() })
    showEdit.value = false
    await loadCategories()
    if (editId.value === selectedId.value) loadDetail()
  } catch (e) {
    alert('保存失败：' + e.message)
  } finally {
    saving.value = false
  }
}

// 删除类别
function askDelete(cat) {
  if (cat.is_system) {
    alert('系统内置类别不可删除')
    return
  }
  deleteTarget.value = cat
  showDelete.value = true
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await deleteOntologyCategory(deleteTarget.value.id)
    showDelete.value = false
    if (deleteTarget.value.id === selectedId.value) {
      selectedId.value = ''
      detail.value = null
    }
    deleteTarget.value = null
    await loadCategories()
  } catch (e) {
    alert('删除失败：' + e.message)
  } finally {
    deleting.value = false
  }
}

onMounted(loadCategories)
</script>

<template>
  <div class="page-shell">
    <div class="page-head">
      <div class="page-title-row">
        <h2 class="page-title">本体管理</h2>
        <span class="page-subtitle">管理本体类别与本体定义，支持本体分类</span>
      </div>
      <div class="head-actions">
        <input
          ref="fileInput"
          type="file"
          accept=".xlsx,.xlsm"
          style="display: none"
          @change="onImportFileChange"
        >
        <button class="btn" @click="onDownloadTemplate" title="下载 Excel 导入模板">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          模板
        </button>
        <button class="btn" @click="fileInput?.click()" :disabled="importing" title="导入 Excel">
          <span v-if="importing" class="spinner xs"></span>
          <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          {{ importing ? '导入中...' : '导入' }}
        </button>
        <button class="btn" @click="onExportCategory(null)" :disabled="exporting" title="导出全部">
          <span v-if="exporting" class="spinner xs"></span>
          <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          {{ exporting ? '导出中...' : '导出' }}
        </button>
      </div>
    </div>

    <div v-if="importError" class="import-error-bar">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      <span>{{ importError }}</span>
      <button class="rm-btn xs" @click="importError = ''">关闭</button>
    </div>

    <div class="split-layout">
      <!-- 左侧：本体类别列表 -->
      <div class="cat-panel">
        <div class="cat-panel-toolbar">
          <div class="search-wrap">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" v-model="search" placeholder="搜索类别...">
          </div>
          <button class="icon-btn sm" @click="openCreate" title="新建类别">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          </button>
        </div>

        <div v-if="loadingList && !categories.length" class="loading-state sm"><span class="spinner"></span></div>

        <div class="cat-scroll" v-else-if="filtered.length">
          <div
            v-for="cat in filtered"
            :key="cat.id"
            class="cat-item"
            :class="{ active: cat.id === selectedId }"
            @click="selectCategory(cat.id)"
          >
            <div class="cat-item-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="6" height="6" rx="1.5"/><rect x="14.75" y="3.25" width="6" height="6" rx="1.5"/><rect x="9" y="14.75" width="6" height="6" rx="1.5"/></svg>
            </div>
            <div class="cat-item-body">
              <div class="cat-item-title">
                {{ cat.name }}
                <span v-if="cat.is_system" class="tag system">系统</span>
              </div>
              <div class="cat-item-meta">{{ cat.ontology_count }} 个本体</div>
            </div>
            <div class="cat-item-actions" @click.stop>
              <button class="rm-btn xs" @click="onExportCategory(cat)" :disabled="exporting" title="导出该类别">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              </button>
              <button class="rm-btn xs" @click="openEdit(cat)" title="编辑">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
              </button>
              <button class="rm-btn xs" @click="askDelete(cat)" title="删除" :disabled="cat.is_system">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </div>
          </div>
        </div>

        <div class="empty-sm" v-else>
          <div class="empty-sm-text">{{ search ? '无匹配类别' : '暂无类别' }}</div>
        </div>
      </div>

      <!-- 右侧：本体编辑区 -->
      <div class="detail-panel">
        <div v-if="loadingDetail" class="loading-state"><span class="spinner"></span> 加载中...</div>
        <div v-else-if="loadError" class="error-state">{{ loadError }}</div>
        <div v-else-if="!selectedId" class="empty-state">
          <div class="empty-icon">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3.25" y="3.25" width="6" height="6" rx="1.5"/><rect x="14.75" y="3.25" width="6" height="6" rx="1.5"/><rect x="9" y="14.75" width="6" height="6" rx="1.5"/></svg>
          </div>
          <div class="empty-title">请选择左侧的本体类别</div>
          <div class="empty-desc">选择类别后可编辑本体定义与属性</div>
        </div>

        <template v-else-if="detail">
          <!-- 基本信息 -->
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
                <textarea v-if="editingInfo" v-model="infoDesc" rows="2" class="info-input"></textarea>
                <span v-else>{{ detail.description || '—' }}</span>
              </div>
            </div>
            <div class="info-row">
              <span class="info-label">类型</span>
              <span class="info-value">
                <span v-if="detail.is_system" class="tag system">系统内置</span>
                <span v-else class="tag custom">自定义</span>
              </span>
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

          <!-- 本体编辑器（列表+详情按需加载，自身管理数据） -->
          <OntologyEditor :category-id="selectedId" @changed="onSubChanged" />
        </template>
      </div>
    </div>

    <!-- 新建弹窗 -->
    <ModalDialog
      v-model="showCreate"
      title="新建本体类别"
      size="sm"
      :confirm-text="creating ? '创建中...' : '创建'"
      :confirm-loading="creating"
      :confirm-disabled="!createName.trim()"
      @confirm="submitCreate"
    >
      <div class="field">
        <label>名称</label>
        <input type="text" v-model="createName" placeholder="例如：金融领域本体" @keydown.enter="submitCreate">
      </div>
      <div class="field">
        <label>描述（可选）</label>
        <textarea v-model="createDesc" rows="3" placeholder="该本体类别覆盖的业务场景..."></textarea>
      </div>
    </ModalDialog>

    <!-- 编辑弹窗 -->
    <ModalDialog
      v-model="showEdit"
      title="编辑本体类别"
      size="sm"
      :confirm-text="saving ? '保存中...' : '保存'"
      :confirm-loading="saving"
      :confirm-disabled="!editName.trim()"
      @confirm="submitEdit"
    >
      <div class="field">
        <label>名称</label>
        <input type="text" v-model="editName" placeholder="类别名称">
      </div>
      <div class="field">
        <label>描述（可选）</label>
        <textarea v-model="editDesc" rows="3" placeholder="该本体类别覆盖的业务场景..."></textarea>
      </div>
    </ModalDialog>

    <!-- 删除确认 -->
    <ModalDialog
      v-model="showDelete"
      title="删除本体类别"
      size="sm"
      confirm-text="删除"
      confirm-variant="danger"
      :confirm-loading="deleting"
      @confirm="confirmDelete"
    >
      <p class="confirm-text">
        确认要删除「<strong>{{ deleteTarget?.name }}</strong>」吗？
      </p>
      <p class="confirm-warn">该操作将级联删除其下所有本体、属性、关系、三元组约束及知识库绑定，且不可恢复。</p>
    </ModalDialog>

    <!-- Excel 导入结果 -->
    <ModalDialog
      v-model="showImportResult"
      :title="importReport?._dry ? '导入校验结果（未写入）' : '导入结果'"
      size="lg"
      :confirm-text="importReport?._dry ? '确认导入' : '完成'"
      :confirm-loading="importing"
      :confirm-disabled="importReport?._dry ? (importReport?.failed || 0) > 0 || (importReport?.total || 0) === 0 : false"
      @confirm="importReport?._dry ? confirmImport() : closeImportResult()"
      @close="closeImportResult"
    >
      <div v-if="importReport" class="import-report">
        <!-- 汇总卡片 -->
        <div class="report-cards">
          <div class="report-card">
            <div class="report-card-num">{{ importReport.total }}</div>
            <div class="report-card-label">总行数</div>
          </div>
          <div class="report-card ok">
            <div class="report-card-num">{{ importReport.success }}</div>
            <div class="report-card-label">成功</div>
          </div>
          <div class="report-card" :class="{ bad: importReport.failed > 0 }">
            <div class="report-card-num">{{ importReport.failed }}</div>
            <div class="report-card-label">失败</div>
          </div>
          <div class="report-card">
            <div class="report-card-num">{{ importReport.created }}</div>
            <div class="report-card-label">新建</div>
          </div>
          <div class="report-card">
            <div class="report-card-num">{{ importReport.updated }}</div>
            <div class="report-card-label">覆盖更新</div>
          </div>
          <div class="report-card">
            <div class="report-card-num">{{ importReport.skipped }}</div>
            <div class="report-card-label">已存在跳过</div>
          </div>
        </div>

        <p v-if="importReport._dry && importReport.failed === 0 && importReport.total > 0" class="report-tip">
          校验通过，共 {{ importReport.total }} 行（新建 {{ importReport.created }} / 覆盖 {{ importReport.updated }}）。点「确认导入」写入数据库。
        </p>
        <p v-else-if="importReport._dry && importReport.failed > 0" class="report-tip bad">
          存在 {{ importReport.failed }} 行非法数据，未写入任何内容。请修正后重新导入（合法行会在修正后一并写入）。
        </p>
        <p v-else-if="importReport.total === 0" class="report-tip bad">
          未从文件中读取到任何有效数据行，请检查 sheet 名称与表头是否与模板一致。
        </p>

        <!-- 分表统计 -->
        <div v-if="Object.keys(importReport.sheets || {}).length" class="report-sheets">
          <table class="report-table">
            <thead>
              <tr><th>Sheet</th><th>总行</th><th>成功</th><th>失败</th><th>新建</th><th>覆盖</th><th>跳过</th></tr>
            </thead>
            <tbody>
              <tr v-for="(st, name) in importReport.sheets" :key="name">
                <td>{{ name }}</td>
                <td>{{ st.total }}</td>
                <td class="ok">{{ st.success }}</td>
                <td :class="{ bad: st.failed > 0 }">{{ st.failed }}</td>
                <td>{{ st.created }}</td>
                <td>{{ st.updated }}</td>
                <td>{{ st.skipped }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 失败明细 -->
        <div v-if="importReport.errors?.length" class="report-errors">
          <div class="report-subtitle">失败明细（{{ importReport.errors.length }} 条）</div>
          <div class="err-scroll">
            <table class="report-table">
              <thead><tr><th style="width:88px">Sheet</th><th style="width:52px">行号</th><th style="width:180px">数据</th><th>失败原因</th></tr></thead>
              <tbody>
                <tr v-for="(e, i) in importReport.errors" :key="i">
                  <td>{{ e.sheet }}</td>
                  <td>{{ e.row }}</td>
                  <td class="err-key">{{ e.key || '—' }}</td>
                  <td class="err-reason">{{ e.reason }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- 警告 -->
        <div v-if="importReport.warnings?.length" class="report-errors">
          <div class="report-subtitle warn">提示（{{ importReport.warnings.length }} 条）</div>
          <div class="err-scroll">
            <table class="report-table">
              <thead><tr><th style="width:88px">Sheet</th><th style="width:52px">行号</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="(w, i) in importReport.warnings" :key="i">
                  <td>{{ w.sheet }}</td>
                  <td>{{ w.row }}</td>
                  <td class="err-reason">{{ w.message }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page-shell { display: flex; flex-direction: column; gap: 16px; height: 100%; }
.page-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--c-border); }
.page-title-row { display: flex; flex-direction: column; gap: 2px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--c-fg); }
.page-subtitle { font-size: 12px; color: var(--c-secondary); }
.head-actions { display: flex; align-items: center; gap: 8px; }
.head-actions .btn { padding: 5px 12px; font-size: 12px; display: inline-flex; align-items: center; gap: 5px; }
.head-actions .btn:disabled { opacity: 0.6; cursor: not-allowed; }

.split-layout { display: flex; gap: 16px; flex: 1; min-height: 0; }

/* 左侧类别面板 */
.cat-panel { flex: 0 0 280px; display: flex; flex-direction: column; gap: 10px; border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 12px; overflow: hidden; }
.cat-panel-toolbar { display: flex; align-items: center; gap: 8px; }
.search-wrap { flex: 1; display: flex; align-items: center; gap: 6px; padding: 0 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); height: 34px; }
.search-wrap:focus-within { border-color: var(--c-fg); }
.search-icon { color: var(--c-secondary); flex-shrink: 0; }
.search-wrap input { flex: 1; min-width: 0; border: 0; outline: none; background: transparent; color: var(--c-fg); font-size: 13px; font-family: var(--font); }
.search-wrap input::placeholder { color: var(--c-secondary); opacity: 0.7; }
.icon-btn.sm { display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-secondary); cursor: pointer; transition: background 150ms, color 150ms; flex-shrink: 0; }
.icon-btn.sm:hover { background: var(--c-muted); color: var(--c-fg); }

.cat-scroll { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.cat-item { display: flex; align-items: center; gap: 10px; padding: 10px 10px; border-radius: var(--radius-sm); cursor: pointer; transition: background 120ms; }
.cat-item:hover { background: var(--c-muted); }
.cat-item.active { background: var(--c-muted); }
.cat-item.active .cat-item-title { color: var(--c-fg); font-weight: 700; }
.cat-item-icon { flex-shrink: 0; width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; border-radius: var(--radius-sm); background: var(--c-muted); color: var(--c-accent); }
.cat-item-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.cat-item-title { font-size: 13px; font-weight: 600; color: var(--c-fg); display: flex; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cat-item-meta { font-size: 11px; color: var(--c-secondary); }
.cat-item-actions { display: flex; gap: 2px; opacity: 0; transition: opacity 150ms; }
.cat-item:hover .cat-item-actions { opacity: 1; }
.rm-btn.xs { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border: 0; border-radius: 6px; background: transparent; color: var(--c-secondary); cursor: pointer; }
.rm-btn.xs:hover { background: var(--c-muted-hover); color: var(--c-fg); }
.rm-btn.xs:disabled { opacity: 0.3; cursor: not-allowed; }

.empty-sm { padding: 24px 12px; text-align: center; }
.empty-sm-text { font-size: 13px; color: var(--c-secondary); }

.tag { display: inline-flex; padding: 1px 7px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.tag.system { background: var(--c-muted-hover); color: var(--c-secondary); }
.tag.custom { background: var(--c-muted); color: var(--c-secondary); }

/* 右侧详情面板 */
.detail-panel { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }

.info-card { border: 1px solid var(--c-border); border-radius: var(--radius); background: var(--c-panel); padding: 4px 20px; max-width: 720px; }
.info-row { display: flex; align-items: flex-start; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--c-border); }
.info-row:last-child { border-bottom: 0; }
.info-label { flex: 0 0 90px; font-size: 13px; font-weight: 600; color: var(--c-secondary); padding-top: 2px; }
.info-value { flex: 1; min-width: 0; font-size: 14px; color: var(--c-fg); word-break: break-word; }
.info-input { width: 100%; padding: 7px 11px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 14px; font-family: var(--font); outline: none; resize: vertical; }
.info-input:focus { border-color: var(--c-fg); }
.info-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 0; }

.loading-state { padding: 40px; text-align: center; color: var(--c-secondary); font-size: 14px; }
.loading-state.sm { padding: 20px; }
.error-state { padding: 40px; text-align: center; color: var(--c-danger); font-size: 14px; }
.empty-state { text-align: center; padding: 48px 20px; color: var(--c-secondary); }
.empty-state .empty-icon { margin-bottom: 12px; color: var(--c-border); }
.empty-state .empty-title { font-size: 15px; font-weight: 700; color: var(--c-fg); margin-bottom: 4px; }
.empty-state .empty-desc { font-size: 13px; }

.field { margin-bottom: 14px; }
.field:last-child { margin-bottom: 0; }
.field label { display: block; font-size: 13px; font-weight: 600; color: var(--c-secondary); margin-bottom: 6px; }
.field input, .field textarea { width: 100%; padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: var(--c-panel); color: var(--c-fg); font-size: 14px; font-family: var(--font); outline: none; transition: border-color 150ms; resize: vertical; }
.field input:focus, .field textarea:focus { border-color: var(--c-fg); }
.confirm-text { font-size: 14px; color: var(--c-fg); margin-bottom: 8px; }
.confirm-warn { font-size: 12px; color: var(--c-danger); }

/* ===== Excel 导入 / 导出 ===== */
.import-error-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; border-radius: 8px;
  background: rgba(220, 38, 38, .08);
  border: 1px solid rgba(220, 38, 38, .28);
  color: var(--c-danger); font-size: 12px;
}
.import-error-bar span { flex: 1; }
.spinner.xs { width: 12px; height: 12px; border-width: 2px; }

.import-report { display: flex; flex-direction: column; gap: 14px; }
.report-cards { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
.report-card {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  padding: 10px 6px; border-radius: 8px;
  background: var(--c-panel); border: 1px solid var(--c-border);
}
.report-card.ok { border-color: rgba(34, 197, 94, .45); }
.report-card.bad { border-color: rgba(220, 38, 38, .45); }
.report-card-num { font-size: 18px; font-weight: 700; color: var(--c-fg); }
.report-card.ok .report-card-num { color: #16a34a; }
.report-card.bad .report-card-num { color: var(--c-danger); }
.report-card-label { font-size: 11px; color: var(--c-secondary); }

.report-tip { font-size: 12px; color: var(--c-secondary); line-height: 1.6; }
.report-tip.bad { color: var(--c-danger); }

.report-subtitle { font-size: 12px; font-weight: 600; color: var(--c-fg); margin-bottom: 6px; }
.report-subtitle.warn { color: #b45309; }

.report-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.report-table th {
  text-align: left; padding: 6px 8px; font-weight: 600;
  color: var(--c-secondary); border-bottom: 1px solid var(--c-border);
  background: var(--c-panel); position: sticky; top: 0;
}
.report-table td {
  padding: 6px 8px; border-bottom: 1px solid var(--c-border);
  color: var(--c-fg); vertical-align: top;
}
.report-table td.ok { color: #16a34a; }
.report-table td.bad { color: var(--c-danger); }
.err-key { color: var(--c-secondary); word-break: break-all; }
.err-reason { color: var(--c-danger); }
.err-scroll { max-height: 240px; overflow-y: auto; border: 1px solid var(--c-border); border-radius: 8px; }
</style>
