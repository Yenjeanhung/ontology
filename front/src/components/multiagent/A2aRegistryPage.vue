<script setup>
/**
 * A2A 智能体管理（注册中心）：远端 Agent-to-Agent 智能体的可视化管理。
 *
 * 与 MCP 工具管理的分工：MCP=Agent↔工具（纵向，函数粒度，kb_search(参数)）；
 * A2A=Agent↔Agent（横向，任务粒度，不透明协作）——组队面板勾选远端智能体
 * 当成员，子任务经 message/send 委派出去，artifact 工件转事实卡回黑板。
 *
 * - 配置入库持久化（a2a_agents 表），组队面板/团队运行热加载——保存即生效；
 * - 试连（拉 AgentCard 名片，不落库）与状态巡检（对已启用智能体逐一拉卡）；
 * - auth_token 仅落库（接口永不回传），编辑留空 = 保持原值。
 * 设计见 doc/智能体/A2A/00-A2A智能体互操作协议设计方案.md。
 */
import { computed, onMounted, ref } from 'vue'
import {
  createA2aAgent,
  deleteA2aAgent,
  inspectA2aAgents,
  listA2aAgents,
  testA2aAgent,
  updateA2aAgent,
} from '../../api/multiAgent'

const agents = ref([])
const loading = ref(false)
const msg = ref('')
const inspecting = ref(false)
// 智能体实时状态（巡检 / 单台拉卡回填）：name → {ok, error, elapsed_ms}
const statusByName = ref({})
// 技能清单展开：name → {open: bool, skills: [...]}
const skillsByName = ref({})

const showExpose = ref(false)

const enabledCount = computed(() => agents.value.filter((a) => a.enabled === 1).length)

function flash(text) {
  msg.value = text
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 4000)
}

async function refresh() {
  loading.value = true
  try {
    const data = await listA2aAgents()
    agents.value = data.agents || []
  } catch (e) {
    flash(`加载失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

// ── 状态巡检 ──
async function inspectAll() {
  if (inspecting.value) return
  inspecting.value = true
  try {
    const data = await inspectA2aAgents()
    const next = {}
    for (const item of data.agents || []) {
      next[item.agent] = item
      if (item.card?.skills?.length) {
        skillsByName.value[item.agent] = { open: skillsByName.value[item.agent]?.open || false, skills: item.card.skills }
      }
    }
    statusByName.value = { ...statusByName.value, ...next }
    const ok = (data.agents || []).filter((s) => s.ok).length
    flash(`巡检完成：${ok}/${(data.agents || []).length} 个智能体名片可拉取`)
  } catch (e) {
    flash(`巡检失败：${e.message}`)
  } finally {
    inspecting.value = false
  }
}

/** 拉名片试连（用已缓存名片优先展示技能；失败 ok:false）。 */
async function testOne(agent) {
  statusByName.value[agent.name] = { ok: null, error: '拉取中…' }
  try {
    const probe = await testA2aAgent({ name: agent.name, base_url: agent.base_url })
    statusByName.value[agent.name] = probe
    if (probe.skills?.length) {
      skillsByName.value[agent.name] = { open: true, skills: probe.skills }
    }
    flash(probe.ok
      ? `「${agent.name}」名片拉取正常：${probe.card?.name} · ${probe.skills.length} 个技能，耗时 ${probe.elapsed_ms}ms`
      : `「${agent.name}」拉取失败：${probe.error}`)
  } catch (e) {
    statusByName.value[agent.name] = { ok: false, error: e.message }
    flash(`测试请求失败：${e.message}`)
  }
}

function toggleSkills(name) {
  const cur = skillsByName.value[name]
  if (!cur) return
  skillsByName.value[name] = { ...cur, open: !cur.open }
}

// ── 启停 / 删除 ──
async function toggleEnabled(agent) {
  try {
    await updateA2aAgent(agent.id, { enabled: agent.enabled !== 1 })
    await refresh()
    flash(agent.enabled === 1
      ? `已停用「${agent.name}」（组队面板不可选，运行中委派将降级报错）`
      : `已启用「${agent.name}」（组队面板立即可选，无需重启）`)
  } catch (e) {
    flash(`操作失败：${e.message}`)
  }
}

async function removeAgent(agent) {
  if (!confirm(`确定删除远端智能体「${agent.name}」？组队面板将不再出现该远程成员。`)) return
  try {
    await deleteA2aAgent(agent.id)
    await refresh()
    flash(`已删除「${agent.name}」`)
  } catch (e) {
    flash(`删除失败：${e.message}`)
  }
}

// ── 新增 / 编辑表单 ──
const emptyForm = () => ({
  show: false, id: '', name: '', base_url: '', auth_token: '',
  description: '', enabled: true,
  testing: false, testResult: null, skills: [],
})
const form = ref(emptyForm())
const saving = ref(false)
const formError = ref('')

function openCreate() {
  formError.value = ''
  form.value = { ...emptyForm(), show: true }
}

function openEdit(agent) {
  formError.value = ''
  form.value = {
    ...emptyForm(),
    show: true,
    id: agent.id,
    name: agent.name,
    base_url: agent.base_url || '',
    auth_token: '',            // 不回显（后端永不回传 token）；留空 = 保持原值
    description: agent.description || '',
    enabled: agent.enabled === 1,
  }
}

function validateFormLocal() {
  const f = form.value
  if (!f.name.trim()) return 'name 不能为空'
  if (!/^[a-zA-Z0-9_-]{1,32}$/.test(f.name.trim())) return 'name 仅限字母/数字/下划线/中划线（1~32 位）'
  if (!/^https?:\/\//.test(f.base_url.trim())) return 'base_url 必须以 http:// 或 https:// 开头（远端名片同源根地址）'
  return ''
}

async function testFormConfig() {
  const err = validateFormLocal()
  if (err) { formError.value = err; return }
  formError.value = ''
  form.value.testing = true
  try {
    const probe = await testA2aAgent({ name: form.value.name.trim(), base_url: form.value.base_url.trim(), auth_token: form.value.auth_token || '' })
    form.value.testResult = probe
    form.value.skills = probe.skills || []
  } catch (e) {
    form.value.testResult = { ok: false, error: e.message, skills: [] }
  } finally {
    form.value.testing = false
  }
}

async function saveForm() {
  const err = validateFormLocal()
  if (err) { formError.value = err; return }
  formError.value = ''
  saving.value = true
  try {
    const body = {
      name: form.value.name.trim(),
      base_url: form.value.base_url.trim(),
      description: form.value.description.trim(),
      enabled: form.value.enabled,
      // 编辑时留空 = null = 保持原 token（后端口径）；填写 = 覆盖
      auth_token: form.value.id ? (form.value.auth_token || null) : (form.value.auth_token || ''),
    }
    if (form.value.id) await updateA2aAgent(form.value.id, body)
    else await createA2aAgent(body)
    form.value.show = false
    await refresh()
    flash(`已保存「${body.name}」——组队面板立即可选，无需重启`)
  } catch (e) {
    formError.value = e.message
  } finally {
    saving.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="a2a-page">
    <div class="a2a-head">
      <div>
        <h1>智能体注册</h1>
        <p class="a2a-sub">
          远程智能体注册中心（Agent↔Agent 横向协作）：配置入库持久化，保存后<em>组队面板立即可选，无需重启</em>。
          多智能体组队勾选后，子任务经 A2A 协议（message/send）委派给远端智能体执行，工件（artifact）转事实卡回灌黑板；
          不透明协作——远端只回结论，不暴露其记忆 / 工具 / 数据源。与 MCP 工具管理互补：
          MCP 管「能用哪些工具」，A2A 管「能把活委派给谁」。
        </p>
      </div>
      <div class="a2a-actions">
        <button class="btn" :disabled="inspecting || !agents.length" @click="inspectAll">
          {{ inspecting ? '巡检中…' : `巡检全部（已启用 ${enabledCount}）` }}
        </button>
        <button class="btn primary" @click="openCreate">+ 新增智能体</button>
      </div>
    </div>

    <p v-if="msg" class="a2a-msg">{{ msg }}</p>

    <div class="a2a-expose">
      <button class="a2a-expose-head" type="button" @click="showExpose = !showExpose">
        <span>{{ showExpose ? '▾' : '▸' }} 对外提供本系统能力（作为 A2A 服务器，被其他平台组队调用）</span>
        <span class="a2a-expose-sub">知识检索 / 图谱核验 / 台账查询 / 配置页智能体，以任务粒度交付</span>
      </button>
      <div v-if="showExpose" class="a2a-expose-body">
        <p class="a2a-expose-line">
          <b>启动</b>：<code>cd backend &amp;&amp; python scripts/a2a_server.py --port 9801</code>
          ，服务发现 <code>http://&lt;host&gt;:9801/.well-known/agent-card.json</code>
          （外部平台注册中心填根地址 <code>http://&lt;host&gt;:9801</code> 即可）
        </p>
        <p class="a2a-expose-line">
          <b>鉴权</b>：加 <code>--token &lt;密钥&gt;</code>（或 .env 的 A2A_EXPOSE_TOKEN），
          调用方需带 <code>Authorization: Bearer &lt;密钥&gt;</code>；不设置则不鉴权，仅限内网。
        </p>
        <p class="a2a-expose-line">
          <b>暴露的技能</b>：<code>kb_search</code> / <code>graph_search</code> / <code>data_query</code>
          （与内置工具同源取数）+ 配置页自定义智能体（<code>agent:&lt;id&gt;</code>，人设 + 工具白名单）。
        </p>
        <p class="a2a-expose-line">
          <b>协议</b>：JSON-RPC 2.0（message/send、message/stream SSE、tasks/get、tasks/cancel），
          Task 状态机 submitted → working → completed / failed，产出以 Task.artifacts 交付。
        </p>
      </div>
    </div>

    <div v-if="loading && !agents.length" class="a2a-empty">加载中…</div>
    <div v-else-if="!agents.length" class="a2a-empty">
      尚未注册远程智能体。点「+ 新增智能体」填远端 A2A 服务根地址（如本机自测：
      <code>http://127.0.0.1:9801</code>，先启动 <code>scripts/a2a_server.py</code>）；
      未配置时组队面板不出现「远程智能体」成员。
    </div>

    <div v-for="a in agents" :key="a.id" class="a2a-card" :class="{ off: a.enabled !== 1 }">
      <div class="a2a-card-head">
        <span class="a2a-dot" :class="statusByName[a.name]?.ok === true ? 'ok' : statusByName[a.name]?.ok === false ? 'err' : ''" />
        <span class="a2a-name">{{ a.name }}</span>
        <span class="a2a-tag">{{ a.card?.name || '（名片未拉取）' }}</span>
        <span v-if="a.card?.protocolVersion" class="a2a-tag">A2A {{ a.card.protocolVersion }}</span>
        <span v-if="a.card?.streaming" class="a2a-tag stream">支持流式</span>
        <label class="a2a-switch" :title="a.enabled === 1 ? '已启用（点击停用）' : '已停用（点击启用）'">
          <input type="checkbox" :checked="a.enabled === 1" @change="toggleEnabled(a)">
          <span>{{ a.enabled === 1 ? '已启用' : '已停用' }}</span>
        </label>
        <div class="a2a-card-ops">
          <button class="btn" @click="testOne(a)">拉取名片</button>
          <button v-if="skillsByName[a.name]?.skills?.length" class="btn" @click="toggleSkills(a.name)">
            {{ skillsByName[a.name].open ? '收起技能' : `技能 ${skillsByName[a.name].skills.length}` }}
          </button>
          <button class="btn" @click="openEdit(a)">编辑</button>
          <button class="btn danger" @click="removeAgent(a)">删除</button>
        </div>
      </div>
      <p v-if="a.description" class="a2a-desc">{{ a.description }}</p>
      <p class="a2a-endpoint"><code>{{ a.base_url }}</code></p>
      <p v-if="statusByName[a.name]" class="a2a-status" :class="{ err: statusByName[a.name].ok === false }">
        <template v-if="statusByName[a.name].ok === true">
          名片正常 · {{ a.card?.name }} · {{ (statusByName[a.name].skills || []).length || a.card?.skills?.length || 0 }} 个技能 · {{ statusByName[a.name].elapsed_ms }}ms
        </template>
        <template v-else-if="statusByName[a.name].ok === false">拉取失败：{{ statusByName[a.name].error }}</template>
        <template v-else>{{ statusByName[a.name].error }}</template>
      </p>
      <div v-if="skillsByName[a.name]?.open && skillsByName[a.name].skills?.length" class="a2a-skills">
        <div v-for="s in skillsByName[a.name].skills" :key="s.id" class="a2a-skill">
          <code>{{ s.id }}</code><span>{{ s.name || '（无名称）' }}{{ s.description ? ` · ${s.description}` : '' }}</span>
        </div>
      </div>
      <div v-else-if="a.card?.skills?.length" class="a2a-skills">
        <span v-for="s in a.card.skills" :key="s.id" class="a2a-skill-chip">{{ s.name || s.id }}</span>
      </div>
    </div>

    <!-- 新增 / 编辑弹层 -->
    <div v-if="form.show" class="a2a-overlay" @click.self="form.show = false">
      <div class="a2a-modal">
        <h2>{{ form.id ? '编辑远端智能体' : '新增远端智能体' }}</h2>
        <div class="a2a-grid">
          <label>名称 <i>（组队成员标识，字母/数字/_/-）</i>
            <input v-model="form.name" type="text" placeholder="flights_pro" :disabled="!!form.id">
          </label>
          <label>远端根地址 <i>（AgentCard 名片同源；填服务根即可，自动发现 /.well-known/agent-card.json）</i>
            <input v-model="form.base_url" type="text" placeholder="http://127.0.0.1:9801">
          </label>
          <label>Bearer 令牌 <i>（远端开启 --token 鉴权时填写；{{ form.id ? '编辑留空 = 保持原值' : '可留空' }}）</i>
            <input v-model="form.auth_token" type="password" placeholder="••••••••" autocomplete="new-password">
          </label>
          <label>描述 <i>（可选）</i>
            <input v-model="form.description" type="text" placeholder="航班业务方智能体（数据不出域）">
          </label>
          <label class="a2a-check">
            <input v-model="form.enabled" type="checkbox"> 启用（组队面板可选）
          </label>
        </div>

        <p v-if="formError" class="a2a-form-err">{{ formError }}</p>
        <p v-if="form.testResult" class="a2a-status" :class="{ err: !form.testResult.ok }">
          <template v-if="form.testResult.ok">
            名片正常 · {{ form.testResult.card?.name }}（{{ form.testResult.card?.protocolVersion || '未知版本' }}） · {{ form.testResult.elapsed_ms }}ms
          </template>
          <template v-else>拉取失败：{{ form.testResult.error }}</template>
        </p>
        <div v-if="form.testResult?.ok && form.skills.length" class="a2a-skills">
          <div v-for="s in form.skills" :key="s.id" class="a2a-skill">
            <code>{{ s.id }}</code><span>{{ s.name || '（无名称）' }}</span>
          </div>
        </div>

        <div class="a2a-modal-foot">
          <button class="btn" :disabled="form.testing" @click="testFormConfig">
            {{ form.testing ? '测试中…' : '拉取名片测试' }}
          </button>
          <span class="a2a-foot-spacer" />
          <button class="btn" @click="form.show = false">取消</button>
          <button class="btn primary" :disabled="saving" @click="saveForm">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.a2a-page {
  padding: 20px 24px 32px;
  max-width: 1080px;
  margin: 0 auto;
}
.a2a-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.a2a-head h1 {
  font-size: 20px;
  margin: 0 0 4px;
}
.a2a-sub {
  margin: 0;
  font-size: 12.5px;
  color: var(--c-secondary);
  max-width: 720px;
  line-height: 1.6;
}
.a2a-sub em {
  font-style: normal;
  color: var(--c-accent);
}
.a2a-actions {
  display: flex;
  gap: 8px;
}
.a2a-msg {
  margin: 0 0 10px;
  font-size: 12.5px;
  padding: 8px 12px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-panel);
}
.a2a-expose {
  border: 1px dashed var(--c-border);
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
}
.a2a-expose-head {
  width: 100%;
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  background: none;
  border: none;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 12.5px;
  color: var(--c-fg);
  text-align: left;
}
.a2a-expose-sub {
  font-size: 11.5px;
  color: var(--c-secondary);
}
.a2a-expose-body {
  padding: 0 12px 10px;
  display: grid;
  gap: 6px;
}
.a2a-expose-line {
  margin: 0;
  font-size: 12px;
  color: var(--c-secondary);
  line-height: 1.7;
}
.a2a-expose-line b {
  color: var(--c-fg);
}
.a2a-expose-line code {
  font-size: 11.5px;
  color: var(--c-accent);
  word-break: break-all;
}
.a2a-empty {
  padding: 28px 16px;
  text-align: center;
  font-size: 13px;
  color: var(--c-secondary);
  border: 1px dashed var(--c-border);
  border-radius: 10px;
  line-height: 1.7;
}
.a2a-empty code { color: var(--c-accent); }
.a2a-card {
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 10px;
}
.a2a-card.off { opacity: 0.62; }
.a2a-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.a2a-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--c-border);
  flex: none;
}
.a2a-dot.ok { background: var(--c-success); }
.a2a-dot.err { background: var(--c-danger); }
.a2a-name {
  font-size: 14px;
  font-weight: 600;
  font-family: ui-monospace, monospace;
}
.a2a-tag {
  font-size: 11px;
  color: var(--c-secondary);
  border: 1px solid var(--c-border);
  border-radius: 999px;
  padding: 1px 8px;
}
.a2a-tag.stream { color: var(--c-accent); }
.a2a-switch {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--c-secondary);
  cursor: pointer;
  user-select: none;
}
.a2a-card-ops {
  margin-left: auto;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.a2a-card-ops .btn { padding: 4px 10px; font-size: 12px; }
.a2a-desc {
  margin: 8px 0 0;
  font-size: 12.5px;
  color: var(--c-fg);
}
.a2a-endpoint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--c-secondary);
  word-break: break-all;
}
.a2a-endpoint code {
  font-size: 11.5px;
}
.a2a-status {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--c-success);
}
.a2a-status.err { color: var(--c-danger); word-break: break-all; }
.a2a-skills {
  margin-top: 8px;
  border-top: 1px dashed var(--c-border);
  padding-top: 8px;
  display: grid;
  gap: 5px;
}
.a2a-skill {
  display: flex;
  gap: 10px;
  align-items: baseline;
  font-size: 12px;
  color: var(--c-secondary);
}
.a2a-skill code {
  color: var(--c-accent);
  font-size: 11.5px;
  flex: none;
}
.a2a-skills:has(.a2a-skill-chip) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.a2a-skill-chip {
  font-size: 11px;
  color: var(--c-secondary);
  border: 1px solid var(--c-border);
  border-radius: 999px;
  padding: 1px 8px;
}

/* 弹层 */
.a2a-overlay {
  position: fixed;
  inset: 0;
  background: var(--c-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
  padding: 20px;
}
.a2a-modal {
  width: min(620px, 100%);
  max-height: 88vh;
  overflow: auto;
  background: var(--c-panel-elevated);
  border: 1px solid var(--c-border);
  border-radius: 12px;
  padding: 18px 20px;
}
.a2a-modal h2 {
  font-size: 16px;
  margin: 0 0 14px;
}
.a2a-grid {
  display: grid;
  gap: 12px;
}
.a2a-grid label {
  display: grid;
  gap: 5px;
  font-size: 12.5px;
  color: var(--c-fg);
}
.a2a-grid label i {
  font-style: normal;
  color: var(--c-secondary);
  font-size: 11.5px;
}
.a2a-check {
  display: flex !important;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.a2a-check input { width: auto; }
.a2a-form-err {
  margin: 12px 0 0;
  font-size: 12.5px;
  color: var(--c-danger);
}
.a2a-modal-foot {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}
.a2a-foot-spacer { flex: 1; }
</style>
