<script setup>
/**
 * MCP 工具管理（注册中心）：ToolAgent 外部工具服务器的可视化管理。
 *
 * - 配置入库持久化（mcp_servers 表），后端 PlatformTools 每次团队运行热加载
 *   enabled=1 的服务器——增删改后下次运行即生效，无需重启；
 * - 连接测试（试连 + 拉工具清单，不落库）与状态巡检（对已启用服务器逐一实连）；
 * - 内置工具（kb_search / graph_search / data_query）由代码注册，永远可用，
 *   本页面只管理 MCP 外部工具这一层。
 */
import { computed, onMounted, ref } from 'vue'
import {
  createMcpServer,
  deleteMcpServer,
  inspectMcpServers,
  listMcpServers,
  testMcpServer,
  updateMcpServer,
} from '../../api/multiAgent'

const servers = ref([])
const loading = ref(false)
const msg = ref('')
const inspecting = ref(false)
// 服务器实时状态（巡检 / 单台测试回填）：name → {ok, error, elapsed_ms}
const statusByName = ref({})
// 工具清单展开：name → {open: bool, tools: [...]}
const toolsByName = ref({})

// 「对外提供」说明卡展开态
const showExpose = ref(false)

// 内置工具（代码注册，不可配置，仅提示）
const BUILTIN_TOOLS = [
  { name: 'kb_search', desc: '全库向量语义检索（与 Retriever 同源取数）' },
  { name: 'graph_search', desc: '实体图谱关键词检索 + 邻接关系链' },
  { name: 'data_query', desc: '实体台账结构化查询（统计 + 明细）' },
]

const enabledCount = computed(() => servers.value.filter((s) => s.enabled === 1).length)

function flash(text) {
  msg.value = text
  setTimeout(() => { if (msg.value === text) msg.value = '' }, 4000)
}

async function refresh() {
  loading.value = true
  try {
    const data = await listMcpServers()
    servers.value = data.servers || []
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
    const data = await inspectMcpServers()
    const next = {}
    for (const item of data.servers || []) {
      next[item.server] = item
      if (item.tools?.length) {
        toolsByName.value[item.server] = { open: toolsByName.value[item.server]?.open || false, tools: item.tools }
      }
    }
    statusByName.value = { ...statusByName.value, ...next }
    const ok = (data.servers || []).filter((s) => s.ok).length
    flash(`巡检完成：${ok}/${(data.servers || []).length} 台连接正常`)
  } catch (e) {
    flash(`巡检失败：${e.message}`)
  } finally {
    inspecting.value = false
  }
}

async function testOne(server) {
  statusByName.value[server.name] = { ok: null, error: '测试中…' }
  try {
    const probe = await testMcpServer(toProbeBody(server))
    statusByName.value[server.name] = probe
    if (probe.tools?.length) {
      toolsByName.value[server.name] = { open: true, tools: probe.tools }
    }
    flash(probe.ok
      ? `「${server.name}」连接正常：${probe.tools.length} 个工具，耗时 ${probe.elapsed_ms}ms`
      : `「${server.name}」连接失败：${probe.error}`)
  } catch (e) {
    statusByName.value[server.name] = { ok: false, error: e.message }
    flash(`测试请求失败：${e.message}`)
  }
}

function toProbeBody(s) {
  return { name: s.name, transport: s.transport, command: s.command, args: s.args || [], env: s.env || {}, url: s.url }
}

function toggleTools(name) {
  const cur = toolsByName.value[name]
  if (!cur) return
  toolsByName.value[name] = { ...cur, open: !cur.open }
}

// ── 启停 / 删除 ──
async function toggleEnabled(server) {
  try {
    await updateMcpServer(server.id, { enabled: server.enabled !== 1 })
    await refresh()
    flash(server.enabled === 1 ? `已停用「${server.name}」（下次团队运行不再接入）` : `已启用「${server.name}」（下次团队运行生效）`)
  } catch (e) {
    flash(`操作失败：${e.message}`)
  }
}

async function removeServer(server) {
  if (!confirm(`确定删除 MCP 服务器「${server.name}」？其全部远程工具将不再参与工具调用。`)) return
  try {
    await deleteMcpServer(server.id)
    await refresh()
    flash(`已删除「${server.name}」`)
  } catch (e) {
    flash(`删除失败：${e.message}`)
  }
}

// ── 新增 / 编辑表单 ──
const emptyForm = () => ({
  show: false, id: '', name: '', transport: 'stdio',
  command: '', argsText: '[]', envText: '{}', url: '', description: '', enabled: true,
  expanded: false, testing: false, testResult: null,
})
const form = ref(emptyForm())
const saving = ref(false)
const formError = ref('')

function openCreate() {
  formError.value = ''
  form.value = { ...emptyForm(), show: true }
}

function openEdit(server) {
  formError.value = ''
  form.value = {
    ...emptyForm(),
    show: true,
    id: server.id,
    name: server.name,
    transport: server.transport,
    command: server.command || '',
    argsText: JSON.stringify(server.args || [], null, 2),
    envText: JSON.stringify(server.env || {}, null, 2),
    url: server.url || '',
    description: server.description || '',
    enabled: server.enabled === 1,
  }
}

function parseJsonField(text, fallback) {
  const raw = (text || '').trim()
  if (!raw) return fallback
  return JSON.parse(raw)   // 抛错由调用方捕获
}

function validateFormLocal() {
  const f = form.value
  if (!f.name.trim()) return 'name 不能为空'
  if (!/^[a-zA-Z0-9_-]{1,32}$/.test(f.name.trim())) return 'name 仅限字母/数字/下划线/中划线（1~32 位）'
  try { if (parseJsonField(f.argsText, []).constructor !== Array) return 'args 必须是 JSON 数组，如 ["-y","@modelcontextprotocol/server-filesystem","D:/data"]' } catch { return 'args 不是合法 JSON' }
  try { if (parseJsonField(f.envText, {}).constructor !== Object) return 'env 必须是 JSON 对象，如 {"API_KEY":"xxx"}' } catch { return 'env 不是合法 JSON' }
  if (f.transport === 'stdio' && !f.command.trim()) return 'stdio 传输必须填写 command（可执行命令）'
  if (f.transport !== 'stdio' && !f.url.trim()) return 'streamable_http 传输必须填写 url'
  return ''
}

function formProbeBody() {
  const f = form.value
  return {
    name: f.name.trim(), transport: f.transport, command: f.command.trim(),
    args: parseJsonField(f.argsText, []), env: parseJsonField(f.envText, {}),
    url: f.url.trim(),
  }
}

async function testFormConfig() {
  const err = validateFormLocal()
  if (err) { formError.value = err; return }
  formError.value = ''
  form.value.testing = true
  try {
    form.value.testResult = await testMcpServer(formProbeBody())
  } catch (e) {
    form.value.testResult = { ok: false, error: e.message, tools: [] }
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
    const body = formProbeBody()
    body.description = form.value.description.trim()
    body.enabled = form.value.enabled
    if (form.value.id) await updateMcpServer(form.value.id, body)
    else await createMcpServer(body)
    form.value.show = false
    await refresh()
    flash(`已保存「${body.name}」——下次团队运行即生效，无需重启`)
  } catch (e) {
    formError.value = e.message
  } finally {
    saving.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="mp-page">
    <div class="mp-head">
      <div>
        <h1>MCP 工具管理</h1>
        <p class="mp-sub">
          ToolAgent（Function Calling）的外部工具注册中心：配置入库持久化，保存后<em>下次团队运行即生效，无需重启</em>。
          远程工具以 mcp_&lt;服务器名&gt;_&lt;工具名&gt; 前缀与内置工具同表参与调用；单台连接失败自动降级，不影响其余工具。
        </p>
      </div>
      <div class="mp-actions">
        <button class="btn" :disabled="inspecting || !servers.length" @click="inspectAll">
          {{ inspecting ? '巡检中…' : `巡检全部（已启用 ${enabledCount}）` }}
        </button>
        <button class="btn primary" @click="openCreate">+ 新增服务器</button>
      </div>
    </div>

    <p v-if="msg" class="mp-msg">{{ msg }}</p>

    <div class="mp-builtin">
      <span class="mp-builtin-title">内置工具（代码注册，永远可用）</span>
      <span v-for="t in BUILTIN_TOOLS" :key="t.name" class="mp-builtin-item">
        <code>{{ t.name }}</code>{{ t.desc }}
      </span>
    </div>

    <div class="mp-expose">
      <button class="mp-expose-head" type="button" @click="showExpose = !showExpose">
        <span>{{ showExpose ? '▾' : '▸' }} 对外提供本系统能力（作为 MCP 服务器）</span>
        <span class="mp-expose-sub">把知识库检索 / 图谱 / 台账查询暴露给 Claude Desktop 等外部 AI 客户端</span>
      </button>
      <div v-if="showExpose" class="mp-expose-body">
        <p class="mp-expose-line">
          <b>方式一 · stdio（本地客户端）</b>：
          <code>cd backend &amp;&amp; python scripts/mcp_server.py</code>
        </p>
        <p class="mp-expose-line">
          <b>方式二 · streamable-http（远程客户端 / 本页自接入）</b>：
          <code>cd backend &amp;&amp; python scripts/mcp_server.py --http --port 9800</code>
          ，端点 <code>http://&lt;host&gt;:9800/mcp</code>（可填回上方注册中心：transport=streamable_http）
        </p>
        <p class="mp-expose-line">
          <b>鉴权（仅 HTTP 模式）</b>：加 <code>--token &lt;密钥&gt;</code>（或环境变量 MCP_EXPOSE_TOKEN），
          客户端需带 <code>Authorization: Bearer &lt;密钥&gt;</code>；不设置则不鉴权，仅限内网。
        </p>
        <p class="mp-expose-line">
          <b>暴露的工具</b>：<code>kb_search</code> / <code>graph_search</code> / <code>data_query</code>
          ——与 ToolAgent 内置工具同源取数，口径一致。
        </p>
        <pre class="mp-expose-code">// Claude Desktop 配置示例（claude_desktop_config.json）
{
  "mcpServers": {
    "knowsource": {
      "command": "python",
      "args": ["D:/path/to/ontology/backend/scripts/mcp_server.py"]
    }
  }
}</pre>
      </div>
    </div>

    <div v-if="loading && !servers.length" class="mp-empty">加载中…</div>
    <div v-else-if="!servers.length" class="mp-empty">
      尚未注册 MCP 服务器。点「+ 新增服务器」接入外部工具（支持 stdio 本地进程与 streamable_http 远程端点）；
      未配置时 ToolAgent 仅使用内置三工具。
    </div>

    <div v-for="s in servers" :key="s.id" class="mp-card" :class="{ off: s.enabled !== 1 }">
      <div class="mp-card-head">
        <span class="mp-dot" :class="statusByName[s.name]?.ok === true ? 'ok' : statusByName[s.name]?.ok === false ? 'err' : ''" />
        <span class="mp-name">{{ s.name }}</span>
        <span class="mp-transport">{{ s.transport === 'stdio' ? 'stdio · 本地进程' : 'streamable_http · 远程端点' }}</span>
        <label class="mp-switch" :title="s.enabled === 1 ? '已启用（点击停用）' : '已停用（点击启用）'">
          <input type="checkbox" :checked="s.enabled === 1" @change="toggleEnabled(s)">
          <span>{{ s.enabled === 1 ? '已启用' : '已停用' }}</span>
        </label>
        <div class="mp-card-ops">
          <button class="btn" @click="testOne(s)">测试连接</button>
          <button v-if="toolsByName[s.name]?.tools?.length" class="btn" @click="toggleTools(s.name)">
            {{ toolsByName[s.name].open ? '收起工具' : `工具 ${toolsByName[s.name].tools.length}` }}
          </button>
          <button class="btn" @click="openEdit(s)">编辑</button>
          <button class="btn danger" @click="removeServer(s)">删除</button>
        </div>
      </div>
      <p v-if="s.description" class="mp-desc">{{ s.description }}</p>
      <p class="mp-endpoint">
        <template v-if="s.transport === 'stdio'">
          <code>{{ s.command }} {{ (s.args || []).join(' ') }}</code>
        </template>
        <template v-else><code>{{ s.url }}</code></template>
      </p>
      <p v-if="statusByName[s.name]" class="mp-status" :class="{ err: statusByName[s.name].ok === false }">
        <template v-if="statusByName[s.name].ok === true">
          连接正常 · {{ (statusByName[s.name].tools || []).length }} 个工具 · {{ statusByName[s.name].elapsed_ms }}ms
        </template>
        <template v-else-if="statusByName[s.name].ok === false">连接失败：{{ statusByName[s.name].error }}</template>
        <template v-else>{{ statusByName[s.name].error }}</template>
      </p>
      <div v-if="toolsByName[s.name]?.open && toolsByName[s.name].tools?.length" class="mp-tools">
        <div v-for="t in toolsByName[s.name].tools" :key="t.name" class="mp-tool">
          <code>{{ t.name }}</code>
          <span>{{ t.description || '（无描述）' }}</span>
        </div>
      </div>
    </div>

    <!-- 新增 / 编辑弹层 -->
    <div v-if="form.show" class="mp-overlay" @click.self="form.show = false">
      <div class="mp-modal">
        <h2>{{ form.id ? '编辑服务器' : '新增服务器' }}</h2>
        <div class="mp-grid">
          <label>名称 <i>（工具前缀 mcp_&lt;name&gt;_，字母/数字/_/-）</i>
            <input v-model="form.name" type="text" placeholder="fs" :disabled="!!form.id">
          </label>
          <label>传输类型
            <select v-model="form.transport">
              <option value="stdio">stdio · 本地进程（npx / python …）</option>
              <option value="streamable_http">streamable_http · 远程端点</option>
            </select>
          </label>
          <template v-if="form.transport === 'stdio'">
            <label>命令 <i>（可执行命令）</i>
              <input v-model="form.command" type="text" placeholder="npx">
            </label>
            <label>参数 <i>（JSON 数组）</i>
              <textarea v-model="form.argsText" rows="3" spellcheck="false"
                placeholder='["-y","@modelcontextprotocol/server-filesystem","D:/data"]' />
            </label>
            <label>环境变量 <i>（JSON 对象，可选）</i>
              <textarea v-model="form.envText" rows="2" spellcheck="false" placeholder='{"API_KEY":"xxx"}' />
            </label>
          </template>
          <template v-else>
            <label>端点 URL
              <input v-model="form.url" type="text" placeholder="http://10.0.0.8:9001/mcp">
            </label>
          </template>
          <label>描述 <i>（可选）</i>
            <input v-model="form.description" type="text" placeholder="文件系统工具">
          </label>
          <label class="mp-check">
            <input v-model="form.enabled" type="checkbox"> 启用（参与工具调用）
          </label>
        </div>

        <p v-if="formError" class="mp-form-err">{{ formError }}</p>
        <p v-if="form.testResult" class="mp-status" :class="{ err: !form.testResult.ok }">
          <template v-if="form.testResult.ok">
            连接正常 · {{ form.testResult.tools.length }} 个工具 · {{ form.testResult.elapsed_ms }}ms
          </template>
          <template v-else>连接失败：{{ form.testResult.error }}</template>
        </p>
        <div v-if="form.testResult?.ok && form.testResult.tools.length" class="mp-tools">
          <div v-for="t in form.testResult.tools" :key="t.name" class="mp-tool">
            <code>{{ t.name }}</code><span>{{ t.description || '（无描述）' }}</span>
          </div>
        </div>

        <div class="mp-modal-foot">
          <button class="btn" :disabled="form.testing" @click="testFormConfig">
            {{ form.testing ? '测试中…' : '测试连接' }}
          </button>
          <span class="mp-foot-spacer" />
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
.mp-page {
  padding: 20px 24px 32px;
  max-width: 1080px;
  margin: 0 auto;
}
.mp-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.mp-head h1 {
  font-size: 20px;
  margin: 0 0 4px;
}
.mp-sub {
  margin: 0;
  font-size: 12.5px;
  color: var(--c-secondary);
  max-width: 720px;
  line-height: 1.6;
}
.mp-sub em {
  font-style: normal;
  color: var(--c-accent);
}
.mp-actions {
  display: flex;
  gap: 8px;
}
.mp-msg {
  margin: 0 0 10px;
  font-size: 12.5px;
  padding: 8px 12px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-panel);
}
.mp-builtin {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 14px;
  font-size: 12px;
  color: var(--c-secondary);
  padding: 8px 12px;
  border: 1px dashed var(--c-border);
  border-radius: 8px;
  margin-bottom: 12px;
}
.mp-builtin-title { font-size: 12px; color: var(--c-fg); }
.mp-builtin-item code {
  margin-right: 6px;
  font-size: 11.5px;
  color: var(--c-accent);
}

/* 对外提供说明卡 */
.mp-expose {
  border: 1px dashed var(--c-border);
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
}
.mp-expose-head {
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
.mp-expose-sub {
  font-size: 11.5px;
  color: var(--c-secondary);
}
.mp-expose-body {
  padding: 0 12px 10px;
  display: grid;
  gap: 6px;
}
.mp-expose-line {
  margin: 0;
  font-size: 12px;
  color: var(--c-secondary);
  line-height: 1.7;
}
.mp-expose-line b {
  color: var(--c-fg);
}
.mp-expose-line code {
  font-size: 11.5px;
  color: var(--c-accent);
  word-break: break-all;
}
.mp-expose-code {
  margin: 4px 0 0;
  padding: 8px 10px;
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 6px;
  font-size: 11.5px;
  color: var(--c-secondary);
  overflow-x: auto;
  line-height: 1.6;
}
.mp-empty {
  padding: 28px 16px;
  text-align: center;
  font-size: 13px;
  color: var(--c-secondary);
  border: 1px dashed var(--c-border);
  border-radius: 10px;
  line-height: 1.7;
}
.mp-card {
  background: var(--c-panel);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 10px;
}
.mp-card.off { opacity: 0.62; }
.mp-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.mp-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--c-border);
  flex: none;
}
.mp-dot.ok { background: var(--c-success); }
.mp-dot.err { background: var(--c-danger); }
.mp-name {
  font-size: 14px;
  font-weight: 600;
  font-family: ui-monospace, monospace;
}
.mp-transport {
  font-size: 11px;
  color: var(--c-secondary);
  border: 1px solid var(--c-border);
  border-radius: 999px;
  padding: 1px 8px;
}
.mp-switch {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--c-secondary);
  cursor: pointer;
  user-select: none;
}
.mp-card-ops {
  margin-left: auto;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.mp-card-ops .btn { padding: 4px 10px; font-size: 12px; }
.mp-desc {
  margin: 8px 0 0;
  font-size: 12.5px;
  color: var(--c-fg);
}
.mp-endpoint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--c-secondary);
  word-break: break-all;
}
.mp-endpoint code {
  font-size: 11.5px;
}
.mp-status {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--c-success);
}
.mp-status.err { color: var(--c-danger); word-break: break-all; }
.mp-tools {
  margin-top: 8px;
  border-top: 1px dashed var(--c-border);
  padding-top: 8px;
  display: grid;
  gap: 5px;
}
.mp-tool {
  display: flex;
  gap: 10px;
  align-items: baseline;
  font-size: 12px;
  color: var(--c-secondary);
}
.mp-tool code {
  color: var(--c-accent);
  font-size: 11.5px;
  flex: none;
}

/* 弹层 */
.mp-overlay {
  position: fixed;
  inset: 0;
  background: var(--c-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
  padding: 20px;
}
.mp-modal {
  width: min(640px, 100%);
  max-height: 88vh;
  overflow: auto;
  background: var(--c-panel-elevated);
  border: 1px solid var(--c-border);
  border-radius: 12px;
  padding: 18px 20px;
}
.mp-modal h2 {
  font-size: 16px;
  margin: 0 0 14px;
}
.mp-grid {
  display: grid;
  gap: 12px;
}
.mp-grid label {
  display: grid;
  gap: 5px;
  font-size: 12.5px;
  color: var(--c-fg);
}
.mp-grid label i {
  font-style: normal;
  color: var(--c-secondary);
  font-size: 11.5px;
}
.mp-check {
  display: flex !important;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.mp-check input { width: auto; }
.mp-form-err {
  margin: 12px 0 0;
  font-size: 12.5px;
  color: var(--c-danger);
}
.mp-modal-foot {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}
.mp-foot-spacer { flex: 1; }
</style>
