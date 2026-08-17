<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  fetchAgentSkills, createAgentSkill, updateAgentSkill, deleteAgentSkill,
  exportAgentSkills, exportAgentSkillsZip,
  importAgentSkills, importAgentSkillsFromUrl, importAgentSkillsFromZip,
  fetchSkillGroups, createSkillGroup, updateSkillGroup, deleteSkillGroup,
} from '../api'
import { useToast } from '../composables/useToast'
import ModalDialog from './common/ModalDialog.vue'
import SkillGroupTree from './SkillGroupTree.vue'

const toast = useToast()

const skills = ref([])
const groups = ref([])
const loading = ref(true)
const selectedId = ref(null)
const isNew = ref(false)
const saving = ref(false)

// ---------- 分组 ----------
// selectedGroupKey：'all' 全部 | 'none' 未分组 | 分组 id
const selectedGroupKey = ref('all')
const expandedGroups = ref(new Set())

const isFixedKey = (k) => k === 'all' || k === 'none'

// 分组 id → 子树统计（ids：子树全部分组 id；count：子树技能总数，含子孙）
const groupStats = computed(() => {
  const childrenOf = new Map()
  for (const g of groups.value) {
    const key = g.parent_id || ''
    if (!childrenOf.has(key)) childrenOf.set(key, [])
    childrenOf.get(key).push(g.id)
  }
  const directCounts = new Map()
  for (const s of skills.value) {
    const key = s.group_id || ''
    directCounts.set(key, (directCounts.get(key) || 0) + 1)
  }
  const stats = new Map()
  const walk = (id) => {
    const st = { ids: new Set([id]), count: directCounts.get(id) || 0 }
    for (const cid of childrenOf.get(id) || []) {
      const sub = walk(cid)
      sub.ids.forEach((x) => st.ids.add(x))
      st.count += sub.count
    }
    stats.set(id, st)
    return st
  }
  for (const g of groups.value) walk(g.id)
  return stats
})

// 分组树（sort_order → 名称 排序），节点带子树累计技能数
const groupTree = computed(() => {
  const sortByOrder = (a, b) => (a.sort_order - b.sort_order) || a.name.localeCompare(b.name, 'zh')
  const build = (parentId) => groups.value
    .filter((g) => (g.parent_id || '') === parentId)
    .slice()
    .sort(sortByOrder)
    .map((g) => ({
      id: g.id,
      name: g.name,
      count: groupStats.value.get(g.id)?.count || 0,
      children: build(g.id),
    }))
  return build('')
})

// 分组 id → 完整路径文案（"写作 / 润色"）
const groupPathById = computed(() => {
  const nameOf = new Map(groups.value.map((g) => [g.id, g.name]))
  const parentOf = new Map(groups.value.map((g) => [g.id, g.parent_id || '']))
  const paths = new Map()
  for (const g of groups.value) {
    const parts = []
    let cur = g.id
    let guard = 0
    while (cur && nameOf.has(cur) && guard++ < 32) {
      parts.unshift(nameOf.get(cur))
      cur = parentOf.get(cur)
    }
    paths.set(g.id, parts.join(' / '))
  }
  return paths
})

// 扁平下拉选项（新建/编辑技能的「所属分组」、分组对话框的「上级分组」）
const flatGroupOptions = computed(() => groups.value
  .slice()
  .sort((a, b) => (groupPathById.value.get(a.id) || '').localeCompare(groupPathById.value.get(b.id) || '', 'zh'))
  .map((g) => ({ id: g.id, path: groupPathById.value.get(g.id) || g.name })))

const ungroupedCount = computed(() => skills.value.filter((s) => !s.group_id).length)

const visibleSkills = computed(() => {
  if (selectedGroupKey.value === 'all') return skills.value
  if (selectedGroupKey.value === 'none') return skills.value.filter((s) => !s.group_id)
  const st = groupStats.value.get(selectedGroupKey.value)
  if (!st) return skills.value.filter((s) => !s.group_id) // 分组刚被删除 → 退回未分组
  return skills.value.filter((s) => st.ids.has(s.group_id))
})

const listTitle = computed(() => {
  if (selectedGroupKey.value === 'all') return `全部技能 (${skills.value.length})`
  if (selectedGroupKey.value === 'none') return `未分组 (${ungroupedCount.value})`
  return `${groupPathById.value.get(selectedGroupKey.value) || ''} (${visibleSkills.value.length})`
})

// 导入目标：选中具体分组 → 该分组 id；全部/未分组 → null（未分组）
const importTargetGroupId = () => (isFixedKey(selectedGroupKey.value) ? null : selectedGroupKey.value)

const importTargetLabel = computed(() => {
  if (isFixedKey(selectedGroupKey.value)) return '未分组'
  return groupPathById.value.get(selectedGroupKey.value) || '未分组'
})

// ---------- 导入导出 ----------
const importUrl = ref('')
const importing = ref(false)

// 重复编码弹窗：phase-1 发现 duplicates → 用户选「覆盖更新 / 跳过重复项」
const dupDialog = ref({ visible: false, items: [], retry: null, base: null })

function toastResult(r) {
  const parts = []
  if (r.imported) parts.push(`导入 ${r.imported} 个`)
  if (r.updated) parts.push(`覆盖更新 ${r.updated} 个`)
  if (r.skipped) parts.push(`跳过 ${r.skipped} 个（编码重复）`)
  if (r.errors) parts.push(`${r.errors} 个失败`)
  if (!parts.length) {
    toast.info('无新技能可导入')
    return
  }
  const okCount = (r.imported || 0) + (r.updated || 0)
  toast[okCount && !r.errors ? 'success' : (okCount ? 'info' : 'error')](parts.join('，'))
}

async function runImport(fetchFn) {
  // phase-1：不覆盖导入；重复项返回 duplicates，由弹窗决策后再发 phase-2
  importing.value = true
  let ok = false
  try {
    const result = await fetchFn({ overwrite: false, groupId: importTargetGroupId() })
    ok = true
    // phase-1 已实际导入的先刷新列表，否则弹窗决策期间新技能一直不可见（旧版 bug）
    if ((result.imported || 0) + (result.updated || 0) > 0) await loadAll()
    if (result.duplicates?.length) {
      dupDialog.value = { visible: true, items: result.duplicates, retry: fetchFn, base: result }
    } else {
      toastResult(result)
    }
  } catch (err) {
    toast.error(`导入失败: ${err.message}`)
  }
  importing.value = false
  return ok
}

async function confirmOverwrite() {
  const { retry, base } = dupDialog.value
  importing.value = true
  try {
    const r2 = await retry({ overwrite: true, groupId: importTargetGroupId() })
    // phase-2 会把 phase-1 已导入的项再幂等 update 一遍，从 updated 里扣除
    toastResult({
      imported: base.imported || 0,
      updated: Math.max(0, (r2.updated || 0) - (base.imported || 0)),
      errors: (base.errors || 0) + (r2.errors || 0),
    })
    dupDialog.value = { visible: false, items: [], retry: null, base: null }
    await loadAll()
  } catch (err) {
    // 失败保持弹窗打开，可再次点「覆盖更新」重试
    toast.error(`覆盖更新失败: ${err.message}`)
  }
  importing.value = false
}

function skipDuplicates() {
  // 列表已在 phase-1 刷新过，这里只负责收尾提示
  const { base } = dupDialog.value
  dupDialog.value = { visible: false, items: [], retry: null, base: null }
  toastResult(base)
}

function triggerFileImport() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json,.zip'
  input.onchange = async () => {
    const file = input.files?.[0]
    if (!file) return
    if (file.name.toLowerCase().endsWith('.zip')) {
      // ZIP 技能包：直传后端解析（SKILL.md + 配套文件完整落盘）
      await runImport((opts) => importAgentSkillsFromZip(file, opts))
      return
    }
    try {
      const text = await file.text()
      const data = JSON.parse(text)
      // 兼容格式
      let arr = Array.isArray(data) ? data : data.skills
      if (!Array.isArray(arr)) {
        toast.error('JSON 格式不正确：须包含 skills 数组或为技能数组')
        return
      }
      await runImport((opts) => importAgentSkills(arr, opts))
    } catch (err) {
      toast.error(`导入失败: ${err.message}`)
    }
  }
  input.click()
}

async function handleUrlImport() {
  const url = importUrl.value.trim()
  if (!url) return
  const ok = await runImport((opts) => importAgentSkillsFromUrl(url, opts))
  if (ok) importUrl.value = ''
}

function downloadJson(data, filename) {
  const json = JSON.stringify(data, null, 2)
  const blob = new Blob([json], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function handleExport() {
  try {
    const data = await exportAgentSkills()
    downloadJson(data, `knowsource-skills-${new Date().toISOString().slice(0, 10)}.json`)
    toast.success('已导出')
  } catch (err) {
    toast.error(`导出失败: ${err.message}`)
  }
}

async function handleExportZip() {
  try {
    await exportAgentSkillsZip()
    toast.success('已导出完整技能包')
  } catch (err) {
    toast.error(`导出失败: ${err.message}`)
  }
}

async function handleExportSkill() {
  const s = selectedSkill()
  if (!s) return
  try {
    const data = await exportAgentSkills(s.id)
    downloadJson(data, `knowsource-skill-${s.code}-${new Date().toISOString().slice(0, 10)}.json`)
    toast.success('已导出该技能')
  } catch (err) {
    toast.error(`导出失败: ${err.message}`)
  }
}

async function handleExportSkillZip() {
  const s = selectedSkill()
  if (!s) return
  try {
    await exportAgentSkillsZip(s.id, s.code)
    toast.success('已导出该技能包')
  } catch (err) {
    toast.error(`导出失败: ${err.message}`)
  }
}

// ---------- 技能编辑 ----------
const editForm = ref({
  name: '', code: '', description: '', instructions: '', sort_order: 0, is_enabled: 1, group_id: null,
})

onMounted(async () => {
  await loadAll()
  expandedGroups.value = new Set(groups.value.map((g) => g.id))
})

async function loadAll() {
  loading.value = true
  try {
    const [sk, gr] = await Promise.all([fetchAgentSkills(), fetchSkillGroups()])
    skills.value = sk
    groups.value = gr
    // 选中项可能已被删除（覆盖导入 / 其它端操作）
    if (selectedId.value && !sk.some((s) => s.id === selectedId.value)) {
      selectedId.value = null
      isNew.value = false
    }
    if (!isFixedKey(selectedGroupKey.value) && !gr.some((g) => g.id === selectedGroupKey.value)) {
      selectedGroupKey.value = 'all'
    }
  } catch {
    toast.error('加载技能失败')
  }
  loading.value = false
}

const selectedSkill = () => skills.value.find((sk) => sk.id === selectedId.value)

function selectSkill(id) {
  const s = skills.value.find((sk) => sk.id === id)
  if (!s) return
  selectedId.value = id
  isNew.value = false
  editForm.value = {
    name: s.name,
    code: s.code,
    description: s.description || '',
    instructions: s.instructions || '',
    sort_order: s.sort_order,
    is_enabled: s.is_enabled,
    group_id: s.group_id || null,
  }
}

function newSkill() {
  selectedId.value = null
  isNew.value = true
  editForm.value = {
    name: '', code: '', description: '', instructions: '', sort_order: 0, is_enabled: 1,
    // 默认挂到当前选中的分组
    group_id: importTargetGroupId(),
  }
}

function cancelEdit() {
  selectedId.value = null
  isNew.value = false
}

async function toggleEnabled(s) {
  const newVal = s.is_enabled ? 0 : 1
  try {
    await updateAgentSkill(s.id, { isEnabled: newVal })
    s.is_enabled = newVal
    if (selectedId.value === s.id) editForm.value.is_enabled = newVal
  } catch (err) {
    toast.error(`操作失败: ${err.message}`)
  }
}

async function save() {
  if (!editForm.value.name.trim() || !editForm.value.code.trim()) {
    toast.error('名称和编码不能为空')
    return
  }
  saving.value = true
  try {
    if (isNew.value) {
      const created = await createAgentSkill({
        name: editForm.value.name,
        code: editForm.value.code,
        description: editForm.value.description,
        instructions: editForm.value.instructions,
        sortOrder: editForm.value.sort_order,
        groupId: editForm.value.group_id || null,
      })
      toast.success('技能已创建')
      selectedId.value = created.id
      isNew.value = false
    } else {
      await updateAgentSkill(selectedId.value, {
        name: editForm.value.name,
        code: editForm.value.code,
        description: editForm.value.description,
        instructions: editForm.value.instructions,
        sortOrder: editForm.value.sort_order,
        groupId: editForm.value.group_id || null,
      })
      toast.success('已保存')
    }
    await loadAll()
  } catch (err) {
    toast.error(`保存失败: ${err.message}`)
  }
  saving.value = false
}

// 删技能：所有技能（含预设）都可删，走确认弹窗
const deleteSkillDialog = ref({ visible: false, loading: false })

function askRemoveSkill() {
  if (!selectedSkill()) return
  deleteSkillDialog.value = { visible: true, loading: false }
}

async function doRemoveSkill() {
  const s = selectedSkill()
  if (!s) return
  deleteSkillDialog.value.loading = true
  try {
    await deleteAgentSkill(s.id)
    toast.success('已删除')
    deleteSkillDialog.value.visible = false
    selectedId.value = null
    isNew.value = false
    await loadAll()
  } catch (err) {
    toast.error(`删除失败: ${err.message}`)
  }
  deleteSkillDialog.value.loading = false
}

const selectedPreset = () => {
  const s = selectedSkill()
  return s ? !!s.is_preset : false
}

// ---------- 分组操作 ----------
// mode：'create'（根级 / 子级新建）| 'edit'（重命名 + 移动上级）
const groupDialog = ref({ visible: false, mode: 'create', id: null, name: '', parentId: null })

function selectGroup(key) {
  selectedGroupKey.value = key
}

function toggleGroup(id) {
  const next = new Set(expandedGroups.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedGroups.value = next
}

function openGroupCreate(parent = null) {
  groupDialog.value = { visible: true, mode: 'create', id: null, name: '', parentId: parent ? parent.id : null }
  if (parent) toggleGroup(parent.id)
}

function openGroupEdit(node) {
  const g = groups.value.find((x) => x.id === node.id)
  groupDialog.value = {
    visible: true, mode: 'edit', id: node.id,
    name: g?.name || node.name, parentId: g?.parent_id || null,
  }
}

// 上级分组下拉：编辑时排除自身及后代（防环）
const groupDialogParentOptions = computed(() => {
  const root = [{ id: null, path: '（根级）' }]
  if (groupDialog.value.mode !== 'edit') return [...root, ...flatGroupOptions.value]
  const banned = groupStats.value.get(groupDialog.value.id)?.ids || new Set()
  return [...root, ...flatGroupOptions.value.filter((o) => !banned.has(o.id))]
})

async function saveGroup() {
  const name = groupDialog.value.name.trim()
  if (!name) {
    toast.error('分组名称不能为空')
    return
  }
  const parentId = groupDialog.value.parentId || null
  try {
    if (groupDialog.value.mode === 'create') {
      await createSkillGroup({ name, parentId })
      toast.success('分组已创建')
    } else {
      await updateSkillGroup(groupDialog.value.id, { name, parentId })
      toast.success('分组已保存')
    }
    groupDialog.value.visible = false
    if (parentId) toggleGroup(parentId)
    await loadAll()
  } catch (err) {
    toast.error(`保存失败: ${err.message}`)
  }
}

const deleteGroupDialog = ref({ visible: false, node: null, loading: false })

function askRemoveGroup(node) {
  deleteGroupDialog.value = { visible: true, node, loading: false }
}

async function doDeleteGroup() {
  const { node } = deleteGroupDialog.value
  if (!node) return
  deleteGroupDialog.value.loading = true
  try {
    const r = await deleteSkillGroup(node.id)
    const bits = []
    if (r.skills_ungrouped) bits.push(`${r.skills_ungrouped} 个技能移入未分组`)
    if (r.children_promoted) bits.push(`${r.children_promoted} 个子分组上移一级`)
    toast.success(bits.length ? `分组已删除：${bits.join('，')}` : '分组已删除')
    deleteGroupDialog.value.visible = false
    if (selectedGroupKey.value === node.id) selectedGroupKey.value = 'all'
    await loadAll()
  } catch (err) {
    toast.error(`删除失败: ${err.message}`)
  }
  deleteGroupDialog.value.loading = false
}

// ---------- ZIP 技能包资源文件展示 ----------
const selectedSkillFiles = () => selectedSkill()?.files || []

function fmtSize(n) {
  if (!n && n !== 0) return ''
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${n} B`
}
</script>

<template>
  <div class="skill-page">
    <div class="skill-page-head">
      <div>
        <h3>智能体技能</h3>
        <p class="skill-desc">技能是可组合的提示词指令包，提问时按需勾选，智能体将遵照指令调整回答风格与格式。</p>
      </div>
      <div class="skill-head-actions">
        <button class="btn" @click="handleExport" :disabled="!skills.length">导出 JSON</button>
        <button class="btn" @click="handleExportZip" :disabled="!skills.length" title="每技能一个目录（SKILL.md + 配套文件），含二进制完整包">导出 ZIP</button>
        <button class="btn" @click="triggerFileImport" :disabled="importing" title="支持 .json 技能文件与 .zip 完整技能包">导入文件</button>
        <button class="btn primary" @click="newSkill">新建技能</button>
      </div>
    </div>

    <!-- 在线导入 -->
    <div class="skill-import-bar">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
      <input
        type="text" v-model="importUrl"
        class="skill-url-input"
        placeholder="粘贴技能 JSON / ZIP 文件地址，或 GitHub 仓库地址（自动拉取整仓库）"
        @keydown.enter="handleUrlImport"
        :disabled="importing"
      >
      <button class="btn" @click="handleUrlImport" :disabled="!importUrl.trim() || importing">
        {{ importing ? '导入中...' : '从 URL 导入' }}
      </button>
      <span class="skill-import-target" title="新导入且未自带分组路径的技能将归入该分组">导入到：{{ importTargetLabel }}</span>
    </div>

    <div class="skill-page-body">
      <!-- 左①：分组树 -->
      <div class="skill-group-col">
        <div class="skill-group-head">
          <span>分组</span>
          <button class="skill-group-add" @click="openGroupCreate(null)" title="新建根级分组">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
            新建
          </button>
        </div>
        <div class="skill-group-tree">
          <div
            class="skill-group-node" :class="{ active: selectedGroupKey === 'all' }"
            @click="selectGroup('all')"
          >
            <span class="skill-group-name">全部技能</span>
            <span class="skill-group-count">{{ skills.length }}</span>
          </div>
          <div
            class="skill-group-node" :class="{ active: selectedGroupKey === 'none' }"
            @click="selectGroup('none')"
          >
            <span class="skill-group-name">未分组</span>
            <span class="skill-group-count">{{ ungroupedCount }}</span>
          </div>
          <SkillGroupTree
            v-for="root in groupTree" :key="root.id"
            :node="root"
            :selected-key="isFixedKey(selectedGroupKey) ? '' : selectedGroupKey"
            :expanded="expandedGroups"
            @select="selectGroup"
            @toggle="toggleGroup"
            @create-child="openGroupCreate"
            @rename="openGroupEdit"
            @remove="askRemoveGroup"
          />
          <div class="skill-group-empty" v-if="!loading && !groups.length">暂无分组</div>
        </div>
      </div>

      <!-- 左②：技能列表（按选中分组过滤） -->
      <div class="skill-list-col">
        <div class="skill-list-title" :title="listTitle">{{ listTitle }}</div>
        <div class="skill-list-items">
          <div
            v-for="s in visibleSkills" :key="s.id"
            class="skill-card" :class="{
              active: selectedId === s.id,
              off: !s.is_enabled,
            }"
            @click="selectSkill(s.id)"
          >
            <div class="skill-card-top">
              <span class="skill-card-name">
                <span v-if="s.is_preset" class="skill-preset" title="预设">★</span>
                {{ s.name }}
              </span>
              <button
                type="button" class="skill-switch" :class="{ on: s.is_enabled }"
                @click.stop="toggleEnabled(s)"
                >{{ s.is_enabled ? 'ON' : 'OFF' }}</button>
            </div>
            <div class="skill-card-desc" v-if="s.description">{{ s.description }}</div>
            <div class="skill-card-meta">
              <span class="skill-card-code">{{ s.code }}</span>
              <span v-if="s.files?.length" class="skill-card-files" :title="`技能包含 ${s.files.length} 个资源文件`">📦 {{ s.files.length }} 文件</span>
              <span v-if="!s.is_enabled" class="skill-card-off-tag">已禁用</span>
            </div>
          </div>

          <div class="skill-list-empty" v-if="!loading && !visibleSkills.length">当前分组暂无技能</div>
          <div class="skill-list-empty" v-if="loading">加载中...</div>
        </div>
      </div>

      <!-- 右：编辑面板 -->
      <div class="skill-edit-col">
        <template v-if="isNew || selectedId">
          <div class="skill-edit-head">
            <h4>{{ isNew ? '新建技能' : '编辑技能' }}</h4>
            <button class="btn" @click="cancelEdit" v-if="!isNew">取消</button>
          </div>

          <div class="skill-form">
            <div class="skill-field">
              <label>名称 <span class="req">*</span></label>
              <input type="text" v-model="editForm.name" placeholder="如：深度分析">
            </div>
            <div class="skill-field">
              <label>编码 <span class="req">*</span></label>
              <input
                type="text" v-model="editForm.code" placeholder="如：deep_analysis"
                :readonly="!isNew && selectedPreset"
                :class="{ readonly: !isNew && selectedPreset }"
              >
              <span class="skill-hint" v-if="!isNew && selectedPreset">预设技能编码不可修改</span>
            </div>
            <div class="skill-field">
              <label>描述</label>
              <input type="text" v-model="editForm.description" placeholder="一句话说明用途">
            </div>
            <div class="skill-field">
              <label>所属分组</label>
              <select v-model="editForm.group_id">
                <option :value="null">未分组</option>
                <option v-for="o in flatGroupOptions" :key="o.id" :value="o.id">{{ o.path }}</option>
              </select>
              <span class="skill-hint">决定技能在左侧分组树中的位置，不影响智能体使用</span>
            </div>
            <div class="skill-field">
              <label>指令内容</label>
              <textarea
                v-model="editForm.instructions" rows="10"
                placeholder="智能体将遵照这些指令调整回答行为。支持多行说明..."
              ></textarea>
            </div>
            <div class="skill-field" v-if="!isNew && selectedSkillFiles().length">
              <label>资源文件（{{ selectedSkillFiles().length }}）</label>
              <details class="skill-files-box">
                <summary>查看技能包附带的文件</summary>
                <ul class="skill-files-list">
                  <li v-for="f in selectedSkillFiles()" :key="f.path">
                    <span class="skill-files-path">{{ f.path }}</span>
                    <span class="skill-files-size">{{ fmtSize(f.size) }}</span>
                  </li>
                </ul>
                <span class="skill-hint" v-if="selectedSkill()?.file_dir">
                  配套文件已解压到 <code class="skill-files-dir">{{ selectedSkill().file_dir }}</code>，智能体可按路径引用；导出 JSON / ZIP 会带出文件
                </span>
              </details>
            </div>
            <div class="skill-field">
              <label>排序权重</label>
              <input type="number" v-model.number="editForm.sort_order" min="0" step="10" class="skill-input-sm">
              <span class="skill-hint">数值越小越靠前，同时决定超预算时的截断优先级</span>
            </div>
          </div>

          <div class="skill-edit-actions">
            <div class="skill-edit-actions-main">
              <button class="btn primary" @click="save" :disabled="saving">
                {{ saving ? '保存中...' : '保存' }}
              </button>
              <button
                v-if="!isNew"
                class="btn danger"
                @click="askRemoveSkill"
              >删除技能</button>
            </div>
            <div class="skill-edit-actions-export" v-if="!isNew">
              <button class="btn" @click="handleExportSkill" title="仅导出当前技能为 JSON">导出 JSON</button>
              <button class="btn" @click="handleExportSkillZip" title="仅导出当前技能为完整 ZIP 技能包（SKILL.md + 配套文件）">导出 ZIP</button>
            </div>
          </div>
        </template>

        <div class="skill-edit-empty" v-else>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
          <p>选择左侧技能进行编辑<br>或点击「新建技能」创建</p>
        </div>
      </div>
    </div>

    <!-- 重复编码决策弹窗（✕ 与「跳过重复项」都经 close 单一出口） -->
    <ModalDialog
      v-model="dupDialog.visible"
      title="发现重复编码的技能"
      confirm-text="覆盖更新"
      cancel-text="跳过重复项"
      :confirm-loading="importing"
      @confirm="confirmOverwrite"
      @close="skipDuplicates"
    >
      <div class="dup-body">
        <p class="dup-note" v-if="dupDialog.base?.imported">
          已导入 {{ dupDialog.base.imported }} 个新技能。
        </p>
        <p>以下 {{ dupDialog.items.length }} 个技能编码已存在。覆盖将更新其指令与资源文件（保留启用状态与预设标记）：</p>
        <ul class="dup-list">
          <li v-for="d in dupDialog.items" :key="d.code">
            <code>{{ d.code }}</code>
            <span class="dup-names">{{ d.name }}<template v-if="d.existing_name && d.existing_name !== d.name"> → {{ d.existing_name }}</template></span>
          </li>
        </ul>
      </div>
    </ModalDialog>

    <!-- 分组新建 / 编辑 -->
    <ModalDialog
      v-model="groupDialog.visible"
      :title="groupDialog.mode === 'create' ? (groupDialog.parentId ? '新建子分组' : '新建分组') : '编辑分组'"
      confirm-text="保存"
      @confirm="saveGroup"
    >
      <div class="skill-form">
        <div class="skill-field">
          <label>分组名称 <span class="req">*</span></label>
          <input
            type="text" v-model="groupDialog.name"
            placeholder="如：写作" maxlength="100"
            @keydown.enter="saveGroup"
          >
        </div>
        <div class="skill-field">
          <label>上级分组</label>
          <select v-model="groupDialog.parentId">
            <option v-for="o in groupDialogParentOptions" :key="o.id ?? 'root'" :value="o.id">{{ o.path }}</option>
          </select>
          <span class="skill-hint">分组可任意嵌套；同级不能重名</span>
        </div>
      </div>
    </ModalDialog>

    <!-- 删除分组确认 -->
    <ModalDialog
      v-model="deleteGroupDialog.visible"
      title="删除分组"
      confirm-text="删除"
      confirm-variant="danger"
      :confirm-loading="deleteGroupDialog.loading"
      @confirm="doDeleteGroup"
    >
      <div class="dup-body">
        <p>确定删除分组「{{ deleteGroupDialog.node?.name }}」吗？</p>
        <p class="dup-note">不会删除任何技能：子分组将上移一级，组内技能移入「未分组」。</p>
      </div>
    </ModalDialog>

    <!-- 删除技能确认 -->
    <ModalDialog
      v-model="deleteSkillDialog.visible"
      title="删除技能"
      confirm-text="删除"
      confirm-variant="danger"
      :confirm-loading="deleteSkillDialog.loading"
      @confirm="doRemoveSkill"
    >
      <div class="dup-body">
        <p>确定删除技能「{{ selectedSkill()?.name }}」吗？</p>
        <p class="dup-note" v-if="selectedPreset()">预设技能删除后，重启不会再自动恢复；其配套文件目录也会一并清理。</p>
        <p class="dup-note" v-else>该操作不可撤销，配套文件目录会一并清理。</p>
      </div>
    </ModalDialog>
  </div>
</template>

<style scoped>
.skill-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 1160px;
}

.skill-page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.skill-head-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* ── 在线导入栏 ── */
.skill-import-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-panel);
  color: var(--c-secondary);
}

.skill-url-input {
  flex: 1;
  min-width: 0;
  padding: 6px 12px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm, 6px);
  font-size: 13px;
  font-family: var(--font);
  outline: none;
  background: var(--c-bg);
  color: var(--c-fg);
  transition: border-color 150ms;
}

.skill-url-input:focus {
  border-color: var(--c-accent);
}

.skill-url-input::placeholder {
  color: var(--c-secondary);
  opacity: 0.6;
}

.skill-import-target {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--c-secondary);
  opacity: 0.85;
  max-width: 220px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-page-head h3 {
  font-size: 18px;
  font-weight: 700;
  color: var(--c-fg);
  margin: 0 0 4px;
}

.skill-desc {
  font-size: 13px;
  color: var(--c-secondary);
  margin: 0;
}

.skill-page-body {
  display: flex;
  gap: 16px;
  min-height: 420px;
}

/* ── 左① 分组栏 ── */
.skill-group-col {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skill-group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 700;
  color: var(--c-secondary);
  padding: 0 2px;
}

.skill-group-add {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  font-size: 11px;
  color: var(--c-secondary);
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 6px;
  cursor: pointer;
  transition: all 120ms;
}

.skill-group-add:hover {
  color: var(--c-accent);
  border-color: var(--c-accent);
}

.skill-group-tree {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: calc(100vh - 260px);
  overflow-y: auto;
  padding: 6px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  background: var(--c-panel);
}

.skill-group-node {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 8px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--c-secondary);
  transition: background-color 120ms, color 120ms;
}

.skill-group-node:hover {
  background: var(--c-muted);
  color: var(--c-fg);
}

.skill-group-node.active {
  background: color-mix(in srgb, var(--c-accent) 14%, var(--c-panel));
  color: var(--c-fg);
}

.skill-group-name {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
}

.skill-group-count {
  flex-shrink: 0;
  min-width: 16px;
  padding: 0 5px;
  font-size: 10px;
  font-weight: 600;
  line-height: 16px;
  text-align: center;
  border-radius: 8px;
  background: var(--c-muted);
  color: var(--c-secondary);
}

.skill-group-node.active .skill-group-count {
  background: color-mix(in srgb, var(--c-accent) 22%, transparent);
  color: var(--c-accent);
}

.skill-group-empty {
  text-align: center;
  padding: 20px 8px;
  color: var(--c-secondary);
  font-size: 12px;
}

/* ── 左② 技能列表 ── */
.skill-list-col {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skill-list-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--c-secondary);
  padding: 0 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-list-items {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: calc(100vh - 260px);
  overflow-y: auto;
}

.skill-card {
  padding: 12px 14px;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  cursor: pointer;
  transition: background 120ms, border-color 120ms;
  background: var(--c-panel);
}

.skill-card:hover {
  border-color: var(--c-accent);
}

.skill-card.active {
  border-color: var(--c-accent);
  background: var(--c-muted);
}

.skill-card.off {
  opacity: 0.55;
}

.skill-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.skill-card-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--c-fg);
  display: flex;
  align-items: center;
  gap: 4px;
}

.skill-preset {
  color: var(--c-accent);
  font-size: 12px;
}

.skill-switch {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 6px;
  border: 1px solid var(--c-border);
  background: var(--c-panel);
  color: var(--c-secondary);
  cursor: pointer;
  transition: all 120ms;
  flex-shrink: 0;
}

.skill-switch.on {
  background: color-mix(in srgb, var(--c-success) 15%, var(--c-panel));
  color: var(--c-success);
  border-color: color-mix(in srgb, var(--c-success) 30%, transparent);
}

.skill-card-desc {
  font-size: 12px;
  color: var(--c-secondary);
  margin-top: 4px;
  line-height: 1.4;
}

.skill-card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.skill-card-code {
  font-size: 11px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  color: var(--c-secondary);
  background: var(--c-muted);
  padding: 1px 6px;
  border-radius: 4px;
}

.skill-card-off-tag {
  font-size: 10px;
  font-weight: 600;
  color: var(--c-danger);
}

.skill-card-files {
  font-size: 10px;
  color: var(--c-secondary);
  background: var(--c-muted);
  padding: 1px 6px;
  border-radius: 4px;
}

.skill-list-empty {
  text-align: center;
  padding: 40px 16px;
  color: var(--c-secondary);
  font-size: 13px;
}

/* ── 右 编辑面板 ── */
.skill-edit-col {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--c-border);
  border-radius: 14px;
  background: var(--c-panel);
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 220px);
  overflow-y: auto;
}

.skill-edit-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--c-border);
}

.skill-edit-head h4 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--c-fg);
}

.skill-form {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  flex: 1;
}

.skill-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.skill-field label {
  font-size: 12px;
  font-weight: 600;
  color: var(--c-secondary);
}

.skill-field input,
.skill-field textarea,
.skill-field select {
  padding: 8px 12px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm, 6px);
  font-size: 13px;
  font-family: var(--font);
  outline: none;
  background: var(--c-bg);
  color: var(--c-fg);
  transition: border-color 150ms;
}

.skill-field input:focus,
.skill-field textarea:focus,
.skill-field select:focus {
  border-color: var(--c-accent);
}

.skill-field input.readonly {
  opacity: 0.6;
  cursor: not-allowed;
}

.skill-field textarea {
  font-family: var(--font);
  line-height: 1.5;
  resize: vertical;
  min-height: 120px;
}

.skill-input-sm {
  width: 120px;
}

.skill-hint {
  font-size: 11px;
  color: var(--c-secondary);
}

/* ── ZIP 技能包资源文件（只读） ── */
.skill-files-box {
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm, 6px);
  background: var(--c-bg);
  padding: 8px 12px;
  font-size: 12px;
}

.skill-files-box summary {
  cursor: pointer;
  color: var(--c-secondary);
  user-select: none;
}

.skill-files-list {
  list-style: none;
  margin: 8px 0 4px;
  padding: 0;
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.skill-files-list li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 11px;
}

.skill-files-path {
  color: var(--c-fg);
  overflow-wrap: anywhere;
}

.skill-files-size {
  color: var(--c-secondary);
  flex-shrink: 0;
}

.skill-files-dir {
  font-size: 11px;
  color: var(--c-accent);
  overflow-wrap: anywhere;
}

/* ── 弹窗 ── */
.dup-body {
  font-size: 13px;
  color: var(--c-fg);
  line-height: 1.6;
}

.dup-note {
  color: var(--c-success);
}

.dup-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dup-list li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-muted);
}

.dup-list code {
  font-size: 11px;
  color: var(--c-accent);
}

.dup-names {
  font-size: 12px;
  color: var(--c-secondary);
}

.req {
  color: var(--c-danger);
}

.skill-edit-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 18px;
  border-top: 1px solid var(--c-border);
}

.skill-edit-actions-main,
.skill-edit-actions-export {
  display: flex;
  gap: 10px;
}

.skill-edit-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--c-secondary);
  font-size: 13px;
  text-align: center;
  line-height: 1.6;
}

.skill-edit-empty svg {
  opacity: 0.25;
}

@media (max-width: 960px) {
  .skill-page-body {
    flex-wrap: wrap;
  }
  .skill-group-col {
    width: 100%;
  }
  .skill-group-tree {
    max-height: 200px;
  }
  .skill-list-col {
    width: 260px;
  }
}

@media (max-width: 720px) {
  .skill-page-body {
    flex-direction: column;
  }
  .skill-list-col {
    width: 100%;
  }
  .skill-list-items {
    max-height: 240px;
  }
  .skill-edit-col {
    max-height: none;
  }
  .skill-import-target {
    display: none;
  }
}
</style>
