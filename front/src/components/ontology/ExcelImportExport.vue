<script setup>
import { ref, computed } from 'vue'
import {
  downloadOntologyTemplate,
  exportOntologyExcel,
  importOntologyExcel,
  triggerDownload,
} from '../../api'

const props = defineProps({
  scope: { type: String, required: true }, // full / ontologies / relations / constraints / templates
  categoryId: { type: String, default: null },
})

const emit = defineEmits(['success'])

const fileInput = ref(null)
const importing = ref(false)
const exporting = ref(false)
const report = ref(null)
const showResult = ref(false)
const error = ref('')

const labels = {
  full: '完整',
  ontologies: '本体管理',
  relations: '关系字典',
  constraints: '本体关系',
  templates: '本体模板',
}
const label = computed(() => labels[props.scope] || props.scope)

function scopeTitle(s) { return labels[s] || s }

async function onDownloadTemplate() {
  error.value = ''
  try {
    const blob = await downloadOntologyTemplate({ scope: props.scope, withExample: true })
    triggerDownload(blob, `本体导入模板-${scopeTitle(props.scope)}.xlsx`)
  } catch (e) {
    error.value = e.message || '下载模板失败'
  }
}

async function onExport() {
  error.value = ''
  exporting.value = true
  try {
    const blob = await exportOntologyExcel({ scope: props.scope, categoryId: props.categoryId })
    const catSuffix = props.categoryId ? '当前类别' : '全部类别'
    triggerDownload(blob, `本体导出-${scopeTitle(props.scope)}-${catSuffix}.xlsx`)
  } catch (e) {
    error.value = e.message || '导出失败'
  } finally {
    exporting.value = false
  }
}

async function onFileChange(event) {
  const file = event.target.files?.[0]
  if (!file) return
  importing.value = true
  error.value = ''
  try {
    const dry = await importOntologyExcel(file, { scope: props.scope, dryRun: true })
    if (dry.failed > 0 || dry.total === 0) {
      report.value = { ...dry, _dry: true, _file: file }
      showResult.value = true
      return
    }
    const real = await importOntologyExcel(file, { scope: props.scope, dryRun: false })
    report.value = { ...real, _dry: false, _file: file }
    showResult.value = true
    emit('success')
  } catch (e) {
    error.value = e.message || '导入失败'
  } finally {
    importing.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function confirmImport() {
  const file = report.value?._file
  if (!file) return
  importing.value = true
  try {
    const real = await importOntologyExcel(file, { scope: props.scope, dryRun: false })
    report.value = { ...real, _dry: false, _file: file }
    emit('success')
  } catch (e) {
    error.value = e.message || '导入失败'
  } finally {
    importing.value = false
  }
}

function closeResult() {
  showResult.value = false
  report.value = null
}

function clearError() {
  error.value = ''
}
</script>

<template>
  <div class="xlsx-actions">
    <input
      ref="fileInput"
      type="file"
      accept=".xlsx,.xlsm"
      style="display: none"
      @change="onFileChange"
    >
    <button class="btn" @click="onDownloadTemplate" title="下载导入模板">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      模板
    </button>
    <button class="btn" @click="fileInput?.click()" :disabled="importing" title="导入 Excel">
      <span v-if="importing" class="spinner xs"></span>
      <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
      {{ importing ? '导入中...' : '导入' }}
    </button>
    <button class="btn" @click="onExport" :disabled="exporting" title="导出 Excel">
      <span v-if="exporting" class="spinner xs"></span>
      <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      导出
    </button>
    <span v-if="error" class="xlsx-error" @click="clearError" title="点击清除">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      {{ error }}
    </span>
  </div>

  <!-- 导入结果 -->
  <ModalDialog
    v-model="showResult"
    :title="report?._dry ? '导入校验结果（未写入）' : '导入结果'"
    size="lg"
    :confirm-text="report?._dry ? '确认导入' : '完成'"
    :confirm-loading="importing"
    :confirm-disabled="report?._dry ? (report?.failed || 0) > 0 || (report?.total || 0) === 0 : false"
    @confirm="report?._dry ? confirmImport() : closeResult()"
    @close="closeResult"
  >
    <div v-if="report" class="import-report">
      <div class="report-cards">
        <div class="report-card"><div class="report-card-num">{{ report.total }}</div><div class="report-card-label">总行数</div></div>
        <div class="report-card ok"><div class="report-card-num">{{ report.success }}</div><div class="report-card-label">成功</div></div>
        <div class="report-card" :class="{ bad: report.failed > 0 }"><div class="report-card-num">{{ report.failed }}</div><div class="report-card-label">失败</div></div>
        <div class="report-card"><div class="report-card-num">{{ report.created }}</div><div class="report-card-label">新建</div></div>
        <div class="report-card"><div class="report-card-num">{{ report.updated }}</div><div class="report-card-label">覆盖更新</div></div>
        <div class="report-card"><div class="report-card-num">{{ report.skipped }}</div><div class="report-card-label">已存在跳过</div></div>
      </div>

      <p v-if="report._dry && report.failed === 0 && report.total > 0" class="report-tip">
        校验通过，共 {{ report.total }} 行（新建 {{ report.created }} / 覆盖 {{ report.updated }}）。点「确认导入」写入数据库。
      </p>
      <p v-else-if="report._dry && report.failed > 0" class="report-tip bad">
        存在 {{ report.failed }} 行非法数据，未写入任何内容。请修正后重新导入。
      </p>
      <p v-else-if="report.total === 0" class="report-tip bad">
        未读取到有效数据行，请检查 sheet 名称与表头是否与模板一致。
      </p>

      <div v-if="Object.keys(report.sheets || {}).length" class="report-sheets">
        <table class="report-table">
          <thead><tr><th>Sheet</th><th>总行</th><th>成功</th><th>失败</th><th>新建</th><th>覆盖</th><th>跳过</th></tr></thead>
          <tbody>
            <tr v-for="(st, name) in report.sheets" :key="name">
              <td>{{ name }}</td><td>{{ st.total }}</td><td class="ok">{{ st.success }}</td>
              <td :class="{ bad: st.failed > 0 }">{{ st.failed }}</td><td>{{ st.created }}</td><td>{{ st.updated }}</td><td>{{ st.skipped }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="report.errors?.length" class="report-errors">
        <div class="report-subtitle">失败明细（{{ report.errors.length }} 条）</div>
        <div class="err-scroll">
          <table class="report-table">
            <thead><tr><th style="width:88px">Sheet</th><th style="width:52px">行号</th><th style="width:180px">数据</th><th>失败原因</th></tr></thead>
            <tbody>
              <tr v-for="(e, i) in report.errors" :key="i">
                <td>{{ e.sheet }}</td><td>{{ e.row }}</td><td class="err-key">{{ e.key || '—' }}</td><td class="err-reason">{{ e.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="report.warnings?.length" class="report-errors">
        <div class="report-subtitle warn">提示（{{ report.warnings.length }} 条）</div>
        <div class="err-scroll">
          <table class="report-table">
            <thead><tr><th style="width:88px">Sheet</th><th style="width:52px">行号</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="(w, i) in report.warnings" :key="i">
                <td>{{ w.sheet }}</td><td>{{ w.row }}</td><td class="err-reason">{{ w.message }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </ModalDialog>
</template>

<style scoped>
.xlsx-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.xlsx-actions .btn { padding: 5px 10px; font-size: 12px; display: inline-flex; align-items: center; gap: 5px; }
.xlsx-error {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 8px; border-radius: 6px;
  background: rgba(220, 38, 38, .08); border: 1px solid rgba(220, 38, 38, .28);
  color: var(--c-danger); font-size: 11px; cursor: pointer;
}
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
