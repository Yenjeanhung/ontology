<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  fetchAgents, createAgent, updateAgent, deleteAgent,
  fetchKbs, fetchAgentSkills,
} from '../../api'
import { useToast } from '../../composables/useToast'
import ModalDialog from '../common/ModalDialog.vue'

const toast = useToast()

const agents = ref([])
const kbs = ref([])
const skills = ref([])
const loading = ref(true)

const selectedId = ref(null)
const isNew = ref(false)
const saving = ref(false)

const editForm = ref({
  name: '',
  description: '',
  kb_id: '',
  skill_ids: [],
  system_prompt: '',
})

const enabledSkills = computed(() => skills.value.filter(s => s.is_enabled))
const presetAgents = computed(() => agents.value.filter(a => a.is_preset))
const customAgents = computed(() => agents.value.filter(a => !a.is_preset))
const selectedIsPreset = () => !!selectedAgent()?.is_preset

onMounted(async () => {
  loading.value = true
  try {
    const [ag, kb, sk] = await Promise.all([fetchAgents(), fetchKbs(), fetchAgentSkills()])
    agents.value = ag
    kbs.value = kb
    skills.value = sk
  } catch {
    toast.error('加载智能体失败')
  }
  loading.value = false
})

async function loadAgents() {
  try { agents.value = await fetchAgents() } catch {}
}

const selectedAgent = () => agents.value.find(a => a.id === selectedId.value)

function kbName(id) {
  return kbs.value.find(k => k.id === id)?.name || '未知知识库'
}

function selectAgent(id) {
  const a = agents.value.find(x => x.id === id)
  if (!a) return
  selectedId.value = id
  isNew.value = false
  editForm.value = {
    name: a.name,
    description: a.description || '',
    kb_id: a.kb_id,
    skill_ids: a.skill_ids || [],
    system_prompt: a.system_prompt || '',
  }
}

function newAgent() {
  selectedId.value = null
  isNew.value = true
  editForm.value = { name: '', description: '', kb_id: '', skill_ids: [], system_prompt: '' }
}

function cancelEdit() {
  selectedId.value = null
  isNew.value = false
}

function toggleSkill(id) {
  const idx = editForm.value.skill_ids.indexOf(id)
  if (idx >= 0) editForm.value.skill_ids.splice(idx, 1)
  else editForm.value.skill_ids.push(id)
}

async function toggleEnabled(a) {
  const newVal = a.is_enabled ? 0 : 1
  try {
    await updateAgent(a.id, { isEnabled: newVal })
    a.is_enabled = newVal
  } catch (err) {
    toast.error(`操作失败: ${err.message}`)
  }
}

async function save() {
  if (!editForm.value.name.trim()) { toast.error('名称不能为空'); return }
  saving.value = true
  try {
    if (isNew.value) {
      const created = await createAgent({
        name: editForm.value.name,
        description: editForm.value.description,
        kbId: editForm.value.kb_id,
        skillIds: editForm.value.skill_ids,
        systemPrompt: editForm.value.system_prompt,
      })
      toast.success('智能体已创建')
      selectedId.value = created.id
      isNew.value = false
    } else {
      await updateAgent(selectedId.value, {
        name: editForm.value.name,
        description: editForm.value.description,
        kbId: editForm.value.kb_id,
        skillIds: editForm.value.skill_ids,
        systemPrompt: editForm.value.system_prompt,
      })
      toast.success('已保存')
    }
    await loadAgents()
  } catch (err) {
    toast.error(`保存失败: ${err.message}`)
  }
  saving.value = false
}

const deleteDialog = ref({ visible: false, loading: false })

function askRemove() {
  if (!selectedAgent()) return
  deleteDialog.value = { visible: true, loading: false }
}

async function doRemove() {
  const a = selectedAgent()
  if (!a) return
  deleteDialog.value.loading = true
  try {
    await deleteAgent(a.id)
    toast.success('已删除')
    deleteDialog.value.visible = false
    selectedId.value = null
    isNew.value = false
    await loadAgents()
  } catch (err) {
    toast.error(`删除失败: ${err.message}`)
  }
  deleteDialog.value.loading = false
}
</script>

<template>
  <div class="agent-config-page">
    <div class="page-head">
      <div>
        <h3>智能体配置</h3>
        <p class="desc">智能体 = 知识库 + 技能 + 人设，可命名复用；问答页与工作流都能引用它。</p>
      </div>
      <div class="head-actions">
        <button class="btn primary" @click="newAgent">＋ 新建智能体</button>
      </div>
    </div>

    <div class="page-body">
      <!-- 左：智能体列表（内置 / 自定义 分组） -->
      <div class="list-col">
        <div class="list-items">
          <template v-if="presetAgents.length">
            <div class="group-title">内置</div>
            <div
              v-for="a in presetAgents" :key="a.id"
              class="agent-card" :class="{ active: selectedId === a.id, off: !a.is_enabled }"
              @click="selectAgent(a.id)"
            >
              <div class="card-top">
                <span class="card-name"><span class="preset-star" title="内置">★</span>{{ a.name }}</span>
                <span class="preset-tag" title="内置智能体不可禁用、不可删除">内置</span>
              </div>
              <div class="card-desc" v-if="a.description">{{ a.description }}</div>
              <div class="card-meta">
                <span class="meta-tag">📚 {{ a.kb_id ? kbName(a.kb_id) : '问答时选择' }}</span>
                <span class="meta-tag">🧩 {{ a.skill_count }} 技能</span>
                <span v-if="!a.is_enabled" class="off-tag">已禁用</span>
              </div>
            </div>
          </template>

          <div class="group-title" v-if="!presetAgents.length">全部智能体 ({{ customAgents.length }})</div>
          <div class="group-title" v-else>自定义 ({{ customAgents.length }})</div>
          <div
            v-for="a in customAgents" :key="a.id"
            class="agent-card" :class="{ active: selectedId === a.id, off: !a.is_enabled }"
            @click="selectAgent(a.id)"
          >
            <div class="card-top">
              <span class="card-name">{{ a.name }}</span>
              <button
                type="button" class="switch" :class="{ on: a.is_enabled }"
                @click.stop="toggleEnabled(a)"
              >{{ a.is_enabled ? 'ON' : 'OFF' }}</button>
            </div>
            <div class="card-desc" v-if="a.description">{{ a.description }}</div>
            <div class="card-meta">
              <span class="meta-tag">📚 {{ a.kb_id ? kbName(a.kb_id) : '问答时选择' }}</span>
              <span class="meta-tag">🧩 {{ a.skill_count }} 技能</span>
              <span v-if="!a.is_enabled" class="off-tag">已禁用</span>
            </div>
          </div>
          <div class="list-empty" v-if="!loading && !customAgents.length">暂无自定义智能体，点击右上角新建</div>
          <div class="list-empty" v-if="loading">加载中...</div>
        </div>
      </div>

      <!-- 右：编辑面板 -->
      <div class="edit-col">
        <template v-if="isNew || selectedId">
          <div class="edit-head">
            <h4>{{ isNew ? '新建智能体' : '编辑智能体' }}</h4>
            <button class="btn" @click="cancelEdit" v-if="!isNew">取消</button>
          </div>

          <div class="form">
            <div class="field">
              <label>名称 <span class="req">*</span></label>
              <input type="text" v-model="editForm.name" placeholder="如：财务智能体">
            </div>
            <div class="field">
              <label>描述</label>
              <input type="text" v-model="editForm.description" placeholder="一句话说明用途">
            </div>
            <div class="field">
              <label>知识库（选填）</label>
              <select v-model="editForm.kb_id">
                <option value="">不绑定（不使用知识库）</option>
                <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }} ({{ kb.file_count }} 文件)</option>
              </select>
              <span class="hint">绑定后检索该知识库；不绑定则不使用知识库，仅按人设/技能直接回答</span>
            </div>
            <div class="field">
              <label>技能（可多选）</label>
              <div class="skill-chips" v-if="enabledSkills.length">
                <button
                  v-for="s in enabledSkills" :key="s.id"
                  type="button"
                  class="skill-chip" :class="{ active: editForm.skill_ids.includes(s.id) }"
                  :title="s.description"
                  @click="toggleSkill(s.id)"
                >
                  <span class="chip-ic" v-if="editForm.skill_ids.includes(s.id)">✓</span>
                  <span class="chip-ic" v-else>+</span>
                  {{ s.name }}
                </button>
              </div>
              <span class="hint" v-else>暂无启用的技能，可到「技能管理」先创建</span>
            </div>
            <div class="field">
              <label>人设（System Prompt）</label>
              <textarea
                v-model="editForm.system_prompt" rows="8"
                placeholder="自定义智能体的角色与行为，例如：你是严谨的财务分析助手，回答时先给结论再给依据…"
              ></textarea>
              <span class="hint">留空则使用系统默认人设；技能指令会追加在人设之后</span>
            </div>
          </div>

          <div class="edit-actions">
            <button class="btn primary" @click="save" :disabled="saving">
              {{ saving ? '保存中...' : '保存' }}
            </button>
            <button v-if="!isNew && !selectedIsPreset()" class="btn danger" @click="askRemove">删除智能体</button>
          </div>
        </template>

        <div class="edit-empty" v-else>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.7 4.8L18.5 9.5l-4.8 1.7L12 16l-1.7-4.8L5.5 9.5l4.8-1.7z"/><path d="M18.5 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z"/></svg>
          <p>选择左侧智能体进行编辑<br>或点击「新建智能体」创建</p>
        </div>
      </div>
    </div>

    <ModalDialog
      v-model="deleteDialog.visible"
      title="删除智能体"
      confirm-text="删除"
      confirm-variant="danger"
      :confirm-loading="deleteDialog.loading"
      @confirm="doRemove"
    >
      <div class="del-body">
        <p>确定删除智能体「{{ selectedAgent()?.name }}」吗？</p>
        <p class="del-note">该操作不可撤销；已被工作流引用的智能体删除后，工作流运行时会报错。</p>
      </div>
    </ModalDialog>
  </div>
</template>

<style scoped>
.agent-config-page { display: flex; flex-direction: column; gap: 20px; max-width: 1160px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-head h3 { font-size: 18px; font-weight: 700; color: var(--c-fg); margin: 0 0 4px; }
.desc { font-size: 13px; color: var(--c-secondary); margin: 0; }
.head-actions { display: flex; gap: 8px; flex-shrink: 0; }

.page-body { display: flex; gap: 16px; min-height: 420px; }

/* 左列表 */
.list-col { width: 300px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; }
.list-title { font-size: 12px; font-weight: 700; color: var(--c-secondary); padding: 0 2px; }
.group-title { font-size: 11px; font-weight: 700; color: var(--c-secondary); letter-spacing: 1px; padding: 8px 2px 2px; }
.preset-star { color: var(--c-accent); font-size: 12px; margin-right: 4px; }
.preset-tag {
  flex-shrink: 0; font-size: 10px; font-weight: 700; padding: 1px 8px; border-radius: 999px;
  color: var(--c-accent); background: color-mix(in srgb, var(--c-accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--c-accent) 30%, transparent);
}
.list-items { display: flex; flex-direction: column; gap: 8px; max-height: calc(100vh - 240px); overflow-y: auto; }

.agent-card {
  padding: 12px 14px; border: 1px solid var(--c-border); border-radius: 12px;
  cursor: pointer; background: var(--c-panel); transition: background 120ms, border-color 120ms;
}
.agent-card:hover { border-color: var(--c-accent); }
.agent-card.active { border-color: var(--c-accent); background: var(--c-muted); }
.agent-card.off { opacity: 0.55; }
.card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.card-name { font-size: 14px; font-weight: 600; color: var(--c-fg); }
.card-desc { font-size: 12px; color: var(--c-secondary); margin-top: 4px; line-height: 1.4; }
.card-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.meta-tag { font-size: 10.5px; color: var(--c-secondary); background: var(--c-muted); padding: 1px 6px; border-radius: 4px; }
.off-tag { font-size: 10px; font-weight: 600; color: var(--c-danger); }

.switch {
  font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 6px;
  border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-secondary);
  cursor: pointer; transition: all 120ms; flex-shrink: 0;
}
.switch.on {
  background: color-mix(in srgb, var(--c-success) 15%, var(--c-panel));
  color: var(--c-success); border-color: color-mix(in srgb, var(--c-success) 30%, transparent);
}

.list-empty { text-align: center; padding: 40px 16px; color: var(--c-secondary); font-size: 13px; }

/* 右编辑 */
.edit-col {
  flex: 1; min-width: 0; border: 1px solid var(--c-border); border-radius: 14px;
  background: var(--c-panel); display: flex; flex-direction: column;
  max-height: calc(100vh - 220px); overflow-y: auto;
}
.edit-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid var(--c-border); }
.edit-head h4 { margin: 0; font-size: 15px; font-weight: 700; color: var(--c-fg); }
.form { padding: 16px 18px; display: flex; flex-direction: column; gap: 14px; flex: 1; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field label { font-size: 12px; font-weight: 600; color: var(--c-secondary); }
.field input, .field textarea, .field select {
  padding: 8px 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm, 6px);
  font-size: 13px; font-family: var(--font); outline: none; background: var(--c-bg); color: var(--c-fg);
  transition: border-color 150ms;
}
.field input:focus, .field textarea:focus, .field select:focus { border-color: var(--c-accent); }
.field textarea { font-family: var(--font); line-height: 1.5; resize: vertical; min-height: 120px; }
.hint { font-size: 11px; color: var(--c-secondary); }
.req { color: var(--c-danger); }

.skill-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.skill-chip {
  display: inline-flex; align-items: center; gap: 4px; padding: 4px 12px;
  border-radius: 20px; font-size: 12px; font-weight: 500;
  border: 1px solid var(--c-border); background: var(--c-panel); color: var(--c-secondary);
  cursor: pointer; user-select: none; transition: all 150ms;
}
.skill-chip:hover { border-color: var(--c-accent); color: var(--c-fg); }
.skill-chip.active { background: var(--c-muted); border-color: var(--c-accent); color: var(--c-accent); }
.chip-ic { font-size: 11px; line-height: 1; }

.edit-actions { display: flex; gap: 10px; padding: 14px 18px; border-top: 1px solid var(--c-border); }

.edit-empty {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 12px; color: var(--c-secondary); font-size: 13px; text-align: center; line-height: 1.6;
}
.edit-empty svg { opacity: 0.25; }

.del-body { font-size: 13px; color: var(--c-fg); line-height: 1.6; }
.del-note { color: var(--c-danger); }

@media (max-width: 720px) {
  .page-body { flex-direction: column; }
  .list-col { width: 100%; }
  .list-items { max-height: 240px; }
  .edit-col { max-height: none; }
}
</style>
