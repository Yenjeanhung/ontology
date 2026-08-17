<script setup>
import { onMounted, ref } from 'vue'
import { fetchAgentSkills, createAgentSkill, updateAgentSkill, deleteAgentSkill } from '../api'
import { useToast } from '../composables/useToast'

const toast = useToast()

const skills = ref([])
const loading = ref(true)
const selectedId = ref(null)
const isNew = ref(false)
const saving = ref(false)

const editForm = ref({
  name: '', code: '', description: '', instructions: '', sort_order: 0, is_enabled: 1,
})

onMounted(loadSkills)

async function loadSkills() {
  loading.value = true
  try {
    skills.value = await fetchAgentSkills()
  } catch {
    toast.show('加载技能失败', 'error')
  }
  loading.value = false
}

function selectSkill(id) {
  const s = skills.value.find(sk => sk.id === id)
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
  }
}

function newSkill() {
  selectedId.value = null
  isNew.value = true
  editForm.value = { name: '', code: '', description: '', instructions: '', sort_order: 0, is_enabled: 1 }
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
    toast.show(`操作失败: ${err.message}`, 'error')
  }
}

async function save() {
  if (!editForm.value.name.trim() || !editForm.value.code.trim()) {
    toast.show('名称和编码不能为空', 'error')
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
      })
      toast.show('技能已创建', 'success')
      selectedId.value = created.id
      isNew.value = false
    } else {
      await updateAgentSkill(selectedId.value, {
        name: editForm.value.name,
        code: editForm.value.code,
        description: editForm.value.description,
        instructions: editForm.value.instructions,
        sortOrder: editForm.value.sort_order,
      })
      toast.show('已保存', 'success')
    }
    await loadSkills()
  } catch (err) {
    toast.show(`保存失败: ${err.message}`, 'error')
  }
  saving.value = false
}

async function removeSkill() {
  const s = skills.value.find(sk => sk.id === selectedId.value)
  if (!s) return
  if (s.is_preset) {
    toast.show('预设技能只能禁用，不能删除', 'error')
    return
  }
  try {
    await deleteAgentSkill(selectedId.value)
    toast.show('已删除', 'success')
    selectedId.value = null
    isNew.value = false
    await loadSkills()
  } catch (err) {
    toast.show(`删除失败: ${err.message}`, 'error')
  }
}

const selectedPreset = () => {
  const s = skills.value.find(sk => sk.id === selectedId.value)
  return s ? s.is_preset : false
}
</script>

<template>
  <div class="skill-page">
    <div class="skill-page-head">
      <div>
        <h3>智能体技能</h3>
        <p class="skill-desc">技能是可组合的提示词指令包，提问时按需勾选，智能体将遵照指令调整回答风格与格式。</p>
      </div>
      <button class="btn primary" @click="newSkill">新建技能</button>
    </div>

    <div class="skill-page-body">
      <!-- 左列：技能列表 -->
      <div class="skill-list-col">
        <div class="skill-list-items">
          <div
            v-for="s in skills" :key="s.id"
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
              <span v-if="!s.is_enabled" class="skill-card-off-tag">已禁用</span>
            </div>
          </div>

          <div class="skill-list-empty" v-if="!loading && !skills.length">暂无技能，点击「新建技能」创建</div>
          <div class="skill-list-empty" v-if="loading">加载中...</div>
        </div>
      </div>

      <!-- 右列：编辑面板 -->
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
              <label>指令内容</label>
              <textarea
                v-model="editForm.instructions" rows="10"
                placeholder="智能体将遵照这些指令调整回答行为。支持多行说明..."
              ></textarea>
            </div>
            <div class="skill-field">
              <label>排序权重</label>
              <input type="number" v-model.number="editForm.sort_order" min="0" step="10" class="skill-input-sm">
              <span class="skill-hint">数值越小越靠前，同时决定超预算时的截断优先级</span>
            </div>
          </div>

          <div class="skill-edit-actions">
            <button class="btn primary" @click="save" :disabled="saving">
              {{ saving ? '保存中...' : '保存' }}
            </button>
            <button
              v-if="!isNew && !selectedPreset"
              class="btn danger"
              @click="removeSkill"
            >删除技能</button>
          </div>
        </template>

        <div class="skill-edit-empty" v-else>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
          <p>选择左侧技能进行编辑<br>或点击「新建技能」创建</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.skill-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 960px;
}

.skill-page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
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
  gap: 20px;
  min-height: 420px;
}

/* ── 左列 ── */
.skill-list-col {
  width: 280px;
  flex-shrink: 0;
}

.skill-list-items {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: calc(100vh - 220px);
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

.skill-list-empty {
  text-align: center;
  padding: 40px 16px;
  color: var(--c-secondary);
  font-size: 13px;
}

/* ── 右列 ── */
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
.skill-field textarea {
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
.skill-field textarea:focus {
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

.req {
  color: var(--c-danger);
}

.skill-edit-actions {
  display: flex;
  gap: 10px;
  padding: 14px 18px;
  border-top: 1px solid var(--c-border);
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
}
</style>
