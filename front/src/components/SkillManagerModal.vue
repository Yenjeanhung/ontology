<script setup>
import { ref, watch, nextTick } from 'vue'
import { fetchAgentSkills, createAgentSkill, updateAgentSkill, deleteAgentSkill } from '../api'
import { useToast } from '../composables/useToast'

const props = defineProps({ visible: Boolean })
const emit = defineEmits(['close', 'saved'])

const toast = useToast()

const skills = ref([])
const selectedId = ref(null)
const editForm = ref({ name: '', code: '', description: '', instructions: '', sort_order: 0, is_enabled: 1 })
const isNew = ref(false)
const saving = ref(false)

watch(() => props.visible, (v) => {
  if (v) loadSkills()
})

async function loadSkills() {
  try {
    skills.value = await fetchAgentSkills()
  } catch {}
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

async function toggleEnabled(s) {
  const newEnabled = s.is_enabled ? 0 : 1
  try {
    await updateAgentSkill(s.id, { isEnabled: newEnabled })
    s.is_enabled = newEnabled
    if (selectedId.value === s.id) editForm.value.is_enabled = newEnabled
    toast.show('已更新', 'success')
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
    emit('saved')
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
    emit('saved')
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
  <Teleport to="body">
    <div class="skill-modal-backdrop" v-if="visible" @click.self="emit('close')">
      <div class="skill-modal">
        <div class="skill-modal-head">
          <h4>技能管理</h4>
          <button type="button" class="skill-modal-close" @click="emit('close')">✕</button>
        </div>

        <div class="skill-modal-body">
          <!-- 左列：技能列表 -->
          <div class="skill-list">
            <div class="skill-list-header">
              <span>技能列表</span>
              <button type="button" class="skill-new-btn" @click="newSkill">+ 新建</button>
            </div>
            <div class="skill-list-items">
              <div
                v-for="s in skills" :key="s.id"
                class="skill-list-item" :class="{ active: selectedId === s.id, disabled: !s.is_enabled }"
                @click="selectSkill(s.id)"
              >
                <div class="skill-list-name">
                  <span v-if="s.is_preset" class="skill-preset-dot" title="预设技能">★</span>
                  {{ s.name }}
                </div>
                <button type="button"
                  class="skill-toggle-btn" :class="{ on: s.is_enabled }"
                  @click.stop="toggleEnabled(s)"
                  :title="s.is_enabled ? '点击禁用' : '点击启用'"
                >{{ s.is_enabled ? 'ON' : 'OFF' }}</button>
              </div>
              <div class="skill-list-empty" v-if="!skills.length">暂无技能</div>
            </div>
          </div>

          <!-- 右列：编辑表单 -->
          <div class="skill-editor">
            <template v-if="isNew || selectedId">
              <div class="skill-field">
                <label>名称 <span class="req">*</span></label>
                <input type="text" v-model="editForm.name" placeholder="如：深度分析">
              </div>
              <div class="skill-field">
                <label>编码 <span class="req">*</span></label>
                <input type="text" v-model="editForm.code" placeholder="如：deep_analysis" :readonly="!isNew && selectedPreset">
                <span class="skill-hint" v-if="!isNew && selectedPreset">预设技能编码不可修改</span>
              </div>
              <div class="skill-field">
                <label>描述</label>
                <input type="text" v-model="editForm.description" placeholder="一句话说明用途">
              </div>
              <div class="skill-field">
                <label>指令内容（Markdown）</label>
                <textarea v-model="editForm.instructions" rows="8" placeholder="智能体将遵照这些指令调整回答行为..."></textarea>
              </div>
              <div class="skill-field-row">
                <div class="skill-field">
                  <label>排序</label>
                  <input type="number" v-model.number="editForm.sort_order" min="0" step="10" style="width:100px">
                </div>
              </div>
              <div class="skill-editor-actions">
                <button type="button" class="skill-btn skill-btn-save" @click="save" :disabled="saving">
                  {{ saving ? '保存中...' : '保存' }}
                </button>
                <button type="button" class="skill-btn skill-btn-delete"
                  v-if="!isNew && !selectedPreset" @click="removeSkill">
                  删除
                </button>
              </div>
            </template>
            <div class="skill-editor-empty" v-else>
              选择左侧技能进行编辑，或点击"新建"创建技能。
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.skill-modal-backdrop {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center;
}
.skill-modal {
  background: #fff; border-radius: 16px; width: 720px; max-height: 80vh; display: flex; flex-direction: column;
  box-shadow: 0 20px 60px rgba(0,0,0,0.15);
}
.skill-modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid #eee;
}
.skill-modal-head h4 { margin: 0; font-size: 16px; font-weight: 700; color: var(--c-fg, #333); }
.skill-modal-close {
  background: none; border: none; font-size: 18px; cursor: pointer; color: #999; padding: 4px 8px;
}
.skill-modal-close:hover { color: #333; }

.skill-modal-body { display: flex; flex: 1; min-height: 0; overflow: hidden; }

/* 左列 */
.skill-list { width: 220px; border-right: 1px solid #eee; display: flex; flex-direction: column; }
.skill-list-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; font-size: 13px; font-weight: 600; color: var(--c-secondary, #888);
}
.skill-new-btn {
  background: none; border: 1px solid #d9d5cf; border-radius: 8px; font-size: 12px;
  padding: 2px 10px; cursor: pointer; color: var(--c-secondary);
}
.skill-new-btn:hover { border-color: #8b5cf6; color: #6d28d9; }

.skill-list-items { flex: 1; overflow-y: auto; padding: 0 8px 8px; }
.skill-list-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 10px; border-radius: 10px; cursor: pointer; font-size: 13px;
  transition: background 120ms;
}
.skill-list-item:hover { background: #f5f4f2; }
.skill-list-item.active { background: #f0eeff; }
.skill-list-item.disabled .skill-list-name { color: #bbb; }
.skill-list-name { display: flex; align-items: center; gap: 4px; color: var(--c-fg, #333); }
.skill-preset-dot { color: #f59e0b; font-size: 12px; }
.skill-toggle-btn {
  font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 6px;
  border: 1px solid #d9d5cf; background: #fff; cursor: pointer; transition: all 120ms;
}
.skill-toggle-btn.on { background: #dcfce7; color: #16a34a; border-color: #86efac; }

.skill-list-empty { padding: 20px 14px; color: #bbb; font-size: 12px; text-align: center; }

/* 右列 */
.skill-editor { flex: 1; padding: 16px 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.skill-field { display: flex; flex-direction: column; gap: 4px; }
.skill-field label { font-size: 12px; font-weight: 600; color: var(--c-secondary, #888); }
.skill-field input, .skill-field textarea {
  padding: 8px 12px; border: 1px solid #e8e5df; border-radius: 10px; font-size: 13px;
  outline: none; transition: border-color 150ms; resize: vertical;
}
.skill-field input:focus, .skill-field textarea:focus { border-color: #8b5cf6; }
.skill-field textarea { font-family: inherit; line-height: 1.5; }
.skill-hint { font-size: 11px; color: #b3ab9f; }
.req { color: #ef4444; }

.skill-field-row { display: flex; gap: 16px; }

.skill-editor-actions { display: flex; gap: 10px; margin-top: 4px; }
.skill-btn {
  padding: 6px 18px; border-radius: 10px; font-size: 13px; font-weight: 600;
  border: 1px solid #d9d5cf; background: #fff; cursor: pointer; transition: all 150ms;
}
.skill-btn:disabled { opacity: 0.5; cursor: default; }
.skill-btn-save { background: #6d28d9; color: #fff; border-color: #6d28d9; }
.skill-btn-save:hover:not(:disabled) { background: #5b21b6; }
.skill-btn-delete { color: #ef4444; border-color: #fca5a5; }
.skill-btn-delete:hover { background: #fef2f2; }

.skill-editor-empty { color: #bbb; font-size: 13px; text-align: center; padding: 40px 0; }
</style>
