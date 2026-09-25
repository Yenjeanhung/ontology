<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  createDataSource, deleteDataSource, fetchDataSources,
  testDataSource, updateDataSource,
} from '../../api/index.js'

// ── 列表 ──
const servers = ref([])            // 数据源记录（dsn 已掩码）
const loading = ref(false)
const msg = ref('')
const testState = ref({})          // id -> { ok, elapsed_ms, error }

async function refresh() {
  loading.value = true
  try {
    servers.value = await fetchDataSources()
  } catch (e) {
    msg.value = e.message
  } finally {
    loading.value = false
  }
}

function flash(text) {
  msg.value = text
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 2600)
}

async function toggleEnabled(s) {
  try {
    await updateDataSource(s.id, { enabled: s.enabled !== 1 })
    await refresh()
    flash(s.enabled === 1
      ? `已停用「${s.name}」——NL2SQL 链与多智能体数据源清单立即消失`
      : `已启用「${s.name}」`)
  } catch (e) { flash(e.message) }
}

async function testOne(s) {
  testState.value = { ...testState.value, [s.id]: { pending: true } }
  try {
    const r = await testDataSource({ id: s.id })
    testState.value = { ...testState.value, [s.id]: r }
  } catch (e) {
    testState.value = { ...testState.value, [s.id]: { ok: false, error: e.message } }
  }
}

async function removeServer(s) {
  if (!window.confirm(`删除数据源「${s.name}」？（被类别引用时会被拒绝）`)) return
  try {
    await deleteDataSource(s.id)
    await refresh()
    flash(`已删除「${s.name}」`)
  } catch (e) { flash(e.message) }
}

// ── 新增 / 编辑表单 ──
const DIALECT_OPTS = [
  { value: 'postgres', label: 'PostgreSQL（NL2SQL 只读执行同驱动）' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'sqlite', label: 'SQLite 文件库（CI 兜底）' },
]
const emptyForm = () => ({
  show: false, id: '', name: '', dialect: 'postgres',
  host: '', port: 5432, dbname: '', username: '', password: '',
  dsn: '', useDsn: false, description: '', enabled: true,
  testing: false, testResult: null,
})
const form = ref(emptyForm())
const saving = ref(false)
const formError = ref('')

function openCreate() {
  formError.value = ''
  form.value = { ...emptyForm(), show: true }
}

function openEdit(s) {
  formError.value = ''
  form.value = {
    ...emptyForm(), show: true,
    id: s.id, name: s.name, dialect: s.dialect,
    host: s.host || '', port: s.port || 0, dbname: s.dbname || '',
    username: s.username || '', password: '',
    dsn: '', useDsn: false, description: s.description || '',
    enabled: !!s.enabled,
  }
}

const formProbeBody = () => {
  const f = form.value
  return {
    name: f.name.trim(), dialect: f.dialect,
    host: f.host.trim(), port: Number(f.port) || 0,
    dbname: f.dbname.trim(), username: f.username.trim(),
    password: f.password, dsn: f.useDsn ? f.dsn.trim() : '',
  }
}

function validateFormLocal() {
  const f = form.value
  if (!f.name.trim()) return '名称不能为空'
  if (f.useDsn && !f.dsn.trim()) return '已选「直填 DSN」，连接串不能为空'
  if (!f.useDsn && f.dialect !== 'sqlite'
    && !(f.host.trim() && f.dbname.trim())) return '数据库类型需填写主机与数据库名（或改用直填 DSN）'
  if (!f.useDsn && f.dialect === 'sqlite' && !f.dbname.trim()) return 'SQLite 需填写数据库文件路径'
  return ''
}

async function testFormConfig() {
  const err = validateFormLocal()
  if (err) { formError.value = err; return }
  formError.value = ''
  form.value.testing = true
  try {
    form.value.testResult = await testDataSource(formProbeBody())
  } catch (e) {
    form.value.testResult = { ok: false, error: e.message }
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
    const body = { ...formProbeBody(), description: form.value.description.trim(), enabled: form.value.enabled }
    if (form.value.id) {
      if (!body.password) delete body.password       // 留空 = 保持原密码
      await updateDataSource(form.value.id, body)
      flash(`已保存「${body.name}」——下一次 NL2SQL 查询即按新连接执行`)
    } else {
      await createDataSource(body)
      flash(`已新增「${body.name}」——可在本体类别编辑中引用`)
    }
    form.value.show = false
    await refresh()
  } catch (e) {
    formError.value = e.message
  } finally {
    saving.value = false
  }
}

const boundCount = computed(() => servers.value.filter((s) => s.enabled !== 1).length)
onMounted(refresh)
</script>

<template>
  <div class="ds-page">
    <div class="ds-head">
      <div>
        <h1>数据源管理</h1>
        <p class="ds-sub">
          外部数据源注册表：集中管理数据库连接（类型 / 地址 / 凭据 / 连通性），
          <em>本体类别单选引用</em>后其表/字段/关系本体即可驱动 NL2SQL 取数；
          多智能体协作页 DataAgent 勾选类别即跨数据源取数。
          保存后<em>下一次查询即生效，无需重启</em>。
        </p>
      </div>
      <div class="ds-actions">
        <button class="btn primary" @click="openCreate">+ 新增数据源</button>
      </div>
    </div>

    <p v-if="msg" class="ds-msg">{{ msg }}</p>

    <div class="ds-builtin">
      <span class="ds-builtin-title">安全口径</span>
      <span class="ds-builtin-item">密码仅入库保存，页面与接口只回传掩码连接串（<code>://user:***@</code>）</span>
      <span class="ds-builtin-item">NL2SQL 只读执行：连接串以只读模式打开（<code>file:...?mode=ro</code> / <code>SET read_only</code>）</span>
      <span v-if="boundCount" class="ds-builtin-item warn">当前有 {{ boundCount }} 个数据源处于停用状态（NL2SQL 链不可见）</span>
    </div>

    <div v-if="loading && !servers.length" class="ds-empty">加载中…</div>
    <div v-else-if="!servers.length" class="ds-empty">
      尚未注册数据源。点「+ 新增数据源」接入外部数据库（PostgreSQL / MySQL / SQLite），
      然后在「本体管理 → 本体管理」类别编辑中引用；未绑定的类别不会参与 NL2SQL 取数。
    </div>

    <div v-for="s in servers" :key="s.id" class="ds-card" :class="{ off: s.enabled !== 1 }">
      <div class="ds-card-head">
        <span class="ds-dot" :class="testState[s.id]?.ok === true ? 'ok' : testState[s.id]?.ok === false ? 'err' : ''" />
        <span class="ds-name">{{ s.name }}</span>
        <span class="ds-transport">{{ s.dialect }}</span>
        <label class="ds-switch" :title="s.enabled ? '已启用（点击停用）' : '已停用（点击启用）'">
          <input type="checkbox" :checked="!!s.enabled" @change="toggleEnabled(s)">
          <span>{{ s.enabled ? '已启用' : '已停用' }}</span>
        </label>
        <div class="ds-card-ops">
          <button class="btn" @click="testOne(s)">测试连接</button>
          <button class="btn" @click="openEdit(s)">编辑</button>
          <button class="btn danger" @click="removeServer(s)">删除</button>
        </div>
      </div>
      <p v-if="s.description" class="ds-desc">{{ s.description }}</p>
      <p class="ds-endpoint">
        <code v-if="s.dialect === 'sqlite'">{{ s.dbname }}</code>
        <code v-else>{{ s.username }}@{{ s.host }}:{{ s.port }}/{{ s.dbname }}</code>
        <code class="ds-dsn">{{ s.dsn }}</code>
      </p>
      <p class="ds-meta">
        被 {{ s.used_by || 0 }} 个本体类别引用
        <template v-if="s.used_by">（多智能体数据源清单按类别勾选生效）</template>
      </p>
      <p v-if="testState[s.id]" class="ds-status" :class="{ err: testState[s.id].ok === false }">
        <template v-if="testState[s.id].pending">连接测试中…</template>
        <template v-else-if="testState[s.id].ok">连接正常 · {{ testState[s.id].elapsed_ms }}ms</template>
        <template v-else>连接失败：{{ testState[s.id].error }}</template>
      </p>
    </div>

    <!-- 新增 / 编辑弹层 -->
    <div v-if="form.show" class="ds-overlay" @click.self="form.show = false">
      <div class="ds-modal">
        <h2>{{ form.id ? '编辑数据源' : '新增数据源' }}</h2>
        <div class="ds-grid">
          <label>名称
            <input v-model="form.name" type="text" placeholder="旅客运输业务库">
          </label>
          <label>数据库类型
            <select v-model="form.dialect">
              <option v-for="d in DIALECT_OPTS" :key="d.value" :value="d.value">{{ d.label }}</option>
            </select>
          </label>
          <template v-if="form.dialect === 'sqlite'">
            <label>数据库文件路径
              <input v-model="form.dbname" type="text" placeholder="D:/data/biz.db 或 file:D:/data/biz.db?mode=ro">
            </label>
          </template>
          <template v-else>
            <label>主机
              <input v-model="form.host" type="text" placeholder="localhost">
            </label>
            <label>端口
              <input v-model="form.port" type="number" :placeholder="form.dialect === 'mysql' ? '3306' : '5432'">
            </label>
            <label>数据库名
              <input v-model="form.dbname" type="text" placeholder="biz_aviation">
            </label>
            <label>用户名
              <input v-model="form.username" type="text" placeholder="readonly">
            </label>
            <label>密码 <i v-if="form.id">（留空 = 保持原密码不变）</i>
              <input v-model="form.password" type="password" autocomplete="new-password" placeholder="••••••••">
            </label>
          </template>
          <label class="ds-check">
            <input v-model="form.useDsn" type="checkbox"> 高级：直填完整连接串（DSN，优先于上方字段）
          </label>
          <label v-if="form.useDsn">连接串 DSN
            <input v-model="form.dsn" type="text"
              placeholder="postgresql://user:pass@host:5432/db">
          </label>
          <label>描述 <i>（可选）</i>
            <input v-model="form.description" type="text" placeholder="业务库：旅客运输全域">
          </label>
          <label class="ds-check">
            <input v-model="form.enabled" type="checkbox"> 启用（参与 NL2SQL 取数与多智能体数据源清单）
          </label>
        </div>

        <p v-if="formError" class="ds-form-err">{{ formError }}</p>
        <p v-if="form.testResult" class="ds-status" :class="{ err: !form.testResult.ok }">
          <template v-if="form.testResult.ok">连接正常 · {{ form.testResult.elapsed_ms }}ms</template>
          <template v-else>连接失败：{{ form.testResult.error }}</template>
        </p>

        <div class="ds-modal-foot">
          <button class="btn" :disabled="form.testing" @click="testFormConfig">
            {{ form.testing ? '测试中…' : '测试连接' }}
          </button>
          <span class="ds-foot-spacer" />
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
.ds-page {
  padding: 20px 24px 32px;
  max-width: 1080px;
  margin: 0 auto;
}
.ds-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}
.ds-head h1 {
  margin: 0 0 6px;
  font-size: 20px;
}
.ds-sub {
  margin: 0;
  color: var(--text-2, #9aa4b2);
  font-size: 13px;
  line-height: 1.7;
  max-width: 720px;
}
.ds-sub em {
  font-style: normal;
  color: var(--accent, #2dd4bf);
}
.ds-actions { flex-shrink: 0; }
.ds-msg {
  margin: 12px 0 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: rgba(45, 212, 191, 0.08);
  color: var(--accent, #2dd4bf);
  font-size: 13px;
}
.ds-builtin {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  align-items: center;
  font-size: 12px;
  color: var(--text-2, #9aa4b2);
}
.ds-builtin-title { font-weight: 600; }
.ds-builtin-item.warn { color: #fbbf24; }
.ds-builtin code {
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 5px;
  border-radius: 4px;
}
.ds-empty {
  margin-top: 24px;
  padding: 32px 16px;
  text-align: center;
  color: var(--text-2, #9aa4b2);
  font-size: 13px;
  border: 1px dashed var(--line, #2a3240);
  border-radius: 10px;
}
.ds-card {
  margin-top: 12px;
  padding: 14px 16px;
  border: 1px solid var(--line, #2a3240);
  border-radius: 10px;
  background: var(--panel, #161b22);
}
.ds-card.off { opacity: 0.55; }
.ds-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.ds-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--text-3, #566070);
  flex-shrink: 0;
}
.ds-dot.ok { background: #34d399; }
.ds-dot.err { background: #f87171; }
.ds-name { font-weight: 600; font-size: 14px; }
.ds-transport {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(45, 212, 191, 0.12);
  color: var(--accent, #2dd4bf);
}
.ds-card-ops { margin-left: auto; display: flex; gap: 8px; }
.ds-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-2, #9aa4b2);
  cursor: pointer;
}
.ds-desc, .ds-endpoint, .ds-meta, .ds-status {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--text-2, #9aa4b2);
}
.ds-endpoint { display: flex; gap: 10px; flex-wrap: wrap; }
.ds-endpoint code, .ds-dsn {
  background: rgba(255, 255, 255, 0.05);
  padding: 2px 6px;
  border-radius: 4px;
}
.ds-dsn { color: var(--text-3, #7d8694); }
.ds-status.err { color: #f87171; }
.ds-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
}
.ds-modal {
  width: 520px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 64px);
  overflow: auto;
  background: var(--panel, #161b22);
  border: 1px solid var(--line, #2a3240);
  border-radius: 12px;
  padding: 20px 22px;
}
.ds-modal h2 { margin: 0 0 14px; font-size: 16px; }
.ds-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.ds-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-2, #9aa4b2);
}
.ds-grid label i { font-style: normal; color: var(--text-3, #7d8694); }
.ds-grid label.ds-check,
.ds-grid label:only-of-type { grid-column: 1 / -1; flex-direction: row; align-items: center; }
.ds-grid input, .ds-grid select {
  background: var(--bg, #0d1117);
  border: 1px solid var(--line, #2a3240);
  border-radius: 6px;
  padding: 7px 9px;
  color: var(--text-1, #e6edf3);
  font-size: 13px;
}
.ds-form-err { margin: 12px 0 0; color: #f87171; font-size: 12px; }
.ds-modal-foot {
  margin-top: 18px;
  display: flex;
  gap: 10px;
  align-items: center;
}
.ds-foot-spacer { flex: 1; }
</style>
