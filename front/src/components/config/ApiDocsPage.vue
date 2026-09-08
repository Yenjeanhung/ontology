<template>
  <div class="apid-page">
    <!-- 页头 -->
    <header class="apid-head">
      <div class="apid-head-text">
        <h1>开放接口文档</h1>
        <p>
          供第三方系统接入本平台使用的 REST API，覆盖知识库管理、文件上传解析、知识问答与知识图谱查询。
          完整接口定义可查看参考链接：
          <a class="apid-link" :href="`${base}/docs`" target="_blank" rel="noopener">Swagger UI</a>
          <span class="apid-link-sep">·</span>
          <a class="apid-link" :href="`${base}/openapi.json`" target="_blank" rel="noopener">OpenAPI JSON</a>
        </p>
      </div>
      <a class="apid-ext-btn" :href="`${base}/docs`" target="_blank" rel="noopener" title="在新窗口打开 Swagger 调试页">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
        Swagger 调试
      </a>
    </header>

    <!-- 接入说明 -->
    <section class="apid-card apid-intro">
      <div class="apid-intro-row">
        <span class="apid-intro-label">基础地址</span>
        <code class="apid-base-url">
          <span>{{ apiBase }}</span>
          <button type="button" class="apid-copy" :class="{ done: copiedKey === 'base' }" @click="copyText(apiBase, 'base')">
            {{ copiedKey === 'base' ? '已复制' : '复制' }}
          </button>
        </code>
      </div>
      <ul class="apid-notes">
        <li>请求与响应均为 <code>application/json; charset=utf-8</code>（分片上传使用 <code>multipart/form-data</code>）。</li>
        <li>出错时返回对应 HTTP 状态码，响应体形如 <code>{ detail: "错误说明" }</code>。</li>
        <li>标注 <b>SSE</b> 的接口以 <code>text/event-stream</code> 流式返回，请使用 EventSource / fetch 流式读取。</li>
        <li>接入流程建议：创建知识库（可绑定本体类别）→ 分片上传文件 → 触发解析 → 轮询 / SSE 等待完成 → 调用问答接口。</li>
      </ul>
    </section>

    <!-- 分组筛选 + 搜索 -->
    <div class="apid-toolbar">
      <div class="apid-chips">
        <button
          v-for="g in groupChips" :key="g.key" type="button"
          class="apid-chip" :class="{ 'is-active': activeGroup === g.key }"
          @click="activeGroup = g.key"
        >{{ g.label }}</button>
      </div>
      <div class="apid-search">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
        <input v-model.trim="keyword" type="text" placeholder="搜索接口路径或名称" spellcheck="false" />
      </div>
    </div>

    <!-- 分组接口列表 -->
    <section v-for="g in visibleGroups" :key="g.key" class="apid-group">
      <div class="apid-group-head">
        <span class="apid-group-icon" v-html="g.icon" />
        <div class="apid-group-title">
          <h2>{{ g.title }}</h2>
          <p>{{ g.desc }}</p>
        </div>
        <span class="apid-group-count">{{ g.apis.length }} 个接口</span>
      </div>

      <article v-for="api in g.apis" :key="api.method + api.path" class="apid-api">
        <button type="button" class="apid-api-row" :class="{ open: isOpen(api) }" @click="toggle(api)">
          <span class="apid-method" :class="api.method.toLowerCase()">{{ api.method }}</span>
          <code class="apid-path">{{ api.path }}</code>
          <span class="apid-api-name">{{ api.name }}</span>
          <svg class="apid-chevron" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>

        <div v-if="isOpen(api)" class="apid-api-detail">
          <p class="apid-api-desc">{{ api.desc }}</p>

          <table v-if="api.params && api.params.length" class="apid-params">
            <thead>
              <tr><th>参数</th><th>位置</th><th>类型</th><th>必填</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in api.params" :key="p.name">
                <td><code>{{ p.name }}</code></td>
                <td><span class="apid-loc">{{ p.loc }}</span></td>
                <td>{{ p.type }}</td>
                <td>{{ p.required ? '是' : '否' }}</td>
                <td>{{ p.desc }}</td>
              </tr>
            </tbody>
          </table>

          <div v-if="api.req" class="apid-code-block">
            <div class="apid-code-head">
              <span>请求示例</span>
              <button type="button" class="apid-copy" :class="{ done: copiedKey === keyOf(api, 'req') }" @click="copyText(api.req, keyOf(api, 'req'))">
                {{ copiedKey === keyOf(api, 'req') ? '已复制' : '复制' }}
              </button>
            </div>
            <pre class="apid-code"><code>{{ api.req }}</code></pre>
          </div>

          <div v-if="api.res" class="apid-code-block">
            <div class="apid-code-head">
              <span>响应示例</span>
              <button type="button" class="apid-copy" :class="{ done: copiedKey === keyOf(api, 'res') }" @click="copyText(api.res, keyOf(api, 'res'))">
                {{ copiedKey === keyOf(api, 'res') ? '已复制' : '复制' }}
              </button>
            </div>
            <pre class="apid-code"><code>{{ api.res }}</code></pre>
          </div>
        </div>
      </article>
    </section>

    <p v-if="!visibleGroups.length" class="apid-empty">没有匹配「{{ keyword }}」的接口</p>

    <footer class="apid-foot">
      以上为面向外部系统集成的高频接口。如需查看平台全部内部接口（含本体服务、接口、函数、版本、工作流、监控等），
      请前往 <a class="apid-link" :href="`${base}/docs`" target="_blank" rel="noopener">Swagger 完整文档</a>。
    </footer>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

// dev 模式走 Vite 代理（相对路径）；生产模式直连后端
const base = import.meta.env.DEV ? '' : 'http://localhost:8000'
const apiBase = import.meta.env.DEV ? 'http://localhost:8000' : base

const activeGroup = ref('all')
const keyword = ref('')
const expandedKeys = ref(new Set())
const copiedKey = ref('')
let copiedTimer = null

const groupChips = [
  { key: 'all', label: '全部' },
  { key: 'ontology', label: '本体管理' },
  { key: 'kb', label: '知识库管理' },
  { key: 'files', label: '文件管理' },
  { key: 'query', label: '知识问答' },
  { key: 'graph', label: '知识图谱' },
]

const iconOntology = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="4.5" r="2.2"/><circle cx="4.5" cy="19.5" r="2.2"/><circle cx="19.5" cy="19.5" r="2.2"/><path d="M12 6.7v4.3M12 11 5.8 17.6M12 11l6.2 6.6"/></svg>'
const iconBox = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>'
const iconLink = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>'
const iconTemplate = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>'
const iconSwap = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>'
const iconBook = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>'
const iconFile = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 18 15 15"/></svg>'
const iconChat = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
const iconGraph = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2.5"/><circle cx="19" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M7.5 6h9M6.3 8.2 10.8 16M17.7 8.2 13.2 16"/></svg>'

const groups = [
  {
    key: 'ontology',
    icon: iconOntology,
    title: '本体类别',
    desc: '本体按类别组织，一个类别下包含本体定义、关系字典与三元组约束。',
    apis: [
      {
        method: 'GET', path: '/ontology-categories', name: '获取类别列表',
        desc: '返回全部本体类别，可按名称模糊搜索。',
        params: [{ name: 'q', loc: 'query', type: 'string', required: false, desc: '名称模糊搜索' }],
        req: `curl "${apiBase}/ontology-categories?q=设备"`,
        res: `[\n  {\n    "id": "cat1a2b3c4d5e",\n    "name": "设备台账",\n    "description": "",\n    "is_system": 0,\n    "created_at": "2026-09-01T10:00:00"\n  }\n]`,
      },
      {
        method: 'POST', path: '/ontology-categories', name: '创建类别',
        desc: '创建一个新的本体类别。',
        params: [
          { name: 'name', loc: 'body', type: 'string', required: true, desc: '类别名称' },
          { name: 'description', loc: 'body', type: 'string', required: false, desc: '类别描述' },
        ],
        req: `curl -X POST ${apiBase}/ontology-categories \\\n  -H "Content-Type: application/json" \\\n  -d '{"name": "设备台账", "description": "接入方设备域模型"}'`,
      },
      {
        method: 'GET', path: '/ontology-categories/{category_id}', name: '获取类别详情',
        desc: '返回类别完整内容，含下挂本体、关系字典与三元组约束，可用于一次拉取整个域模型。',
        params: [{ name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' }],
      },
      {
        method: 'PUT', path: '/ontology-categories/{category_id}', name: '更新类别',
        desc: '更新类别名称与描述，字段均可选。',
        params: [{ name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' }],
      },
      {
        method: 'DELETE', path: '/ontology-categories/{category_id}', name: '删除类别',
        desc: '删除类别及其下全部本体定义（系统内置类别不可删除）。',
        params: [{ name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' }],
        res: `{ "status": "deleted" }`,
      },
    ],
  },
  {
    key: 'ontology',
    icon: iconBox,
    title: '本体定义（对象类型）',
    desc: '定义业务对象类型（如"设备"、"人员"），是图谱实体与结构化对象的建模基础。',
    apis: [
      {
        method: 'GET', path: '/ontology-categories/{category_id}/ontologies', name: '获取本体列表',
        desc: '返回指定类别下的全部本体。',
        params: [{ name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' }],
      },
      {
        method: 'POST', path: '/ontology-categories/{category_id}/ontologies', name: '创建本体',
        desc: '在类别下创建一个本体（对象类型），可设置 API 名、主键属性、状态等元数据。',
        params: [
          { name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' },
          { name: 'name', loc: 'body', type: 'string', required: true, desc: '本体名称' },
          { name: 'code', loc: 'body', type: 'string', required: false, desc: 'API 名（如 equipment）' },
          { name: 'description', loc: 'body', type: 'string', required: false, desc: '描述' },
          { name: 'color', loc: 'body', type: 'string', required: false, desc: '展示颜色' },
          { name: 'sort_order', loc: 'body', type: 'integer', required: false, desc: '排序值' },
        ],
        req: `curl -X POST ${apiBase}/ontology-categories/cat1a2b3c4d5e/ontologies \\\n  -H "Content-Type: application/json" \\\n  -d '{"name": "设备", "code": "equipment", "color": "#3b82f6"}'`,
        res: `{\n  "id": "ont9f8e7d6c5b4",\n  "category_id": "cat1a2b3c4d5e",\n  "name": "设备",\n  "code": "equipment",\n  "status": "active",\n  "primary_key": "name",\n  "sort_order": 0\n}`,
      },
      {
        method: 'POST', path: '/ontology-categories/{category_id}/ontologies/batch', name: '批量创建本体',
        desc: '一次性在类别下创建多个本体。',
        params: [{ name: 'ontologies', loc: 'body', type: 'object[]', required: true, desc: '本体定义数组，元素同单个创建' }],
        req: `curl -X POST ${apiBase}/ontology-categories/cat1a2b3c4d5e/ontologies/batch \\\n  -H "Content-Type: application/json" \\\n  -d '{"ontologies": [{"name": "设备"}, {"name": "部门"}]}'`,
      },
      {
        method: 'GET', path: '/ontology-categories/{category_id}/ontologies/{ontology_id}', name: '获取本体详情',
        desc: '返回本体完整信息，含属性列表。',
        params: [
          { name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' },
          { name: 'ontology_id', loc: 'path', type: 'string', required: true, desc: '本体 id' },
        ],
      },
      {
        method: 'PUT', path: '/ontology-categories/{category_id}/ontologies/{ontology_id}', name: '更新本体',
        desc: '更新本体名称、描述、颜色、状态等，字段均可选。',
        params: [
          { name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' },
          { name: 'ontology_id', loc: 'path', type: 'string', required: true, desc: '本体 id' },
        ],
      },
      {
        method: 'DELETE', path: '/ontology-categories/{category_id}/ontologies/{ontology_id}', name: '删除本体',
        desc: '删除本体及其属性定义。',
        params: [
          { name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' },
          { name: 'ontology_id', loc: 'path', type: 'string', required: true, desc: '本体 id' },
        ],
        res: `{ "status": "deleted" }`,
      },
    ],
  },
  {
    key: 'ontology',
    icon: iconTemplate,
    title: '本体属性',
    desc: '为本体定义属性字段（类型、必填、默认值），文件解析抽取与结构化查询均以此为准。',
    apis: [
      {
        method: 'GET', path: '/ontology-categories/{cid}/ontologies/{oid}/attributes', name: '获取属性列表',
        desc: '返回本体自有属性。',
        params: [
          { name: 'cid', loc: 'path', type: 'string', required: true, desc: '类别 id' },
          { name: 'oid', loc: 'path', type: 'string', required: true, desc: '本体 id' },
        ],
      },
      {
        method: 'POST', path: '/ontology-categories/{cid}/ontologies/{oid}/attributes', name: '创建属性',
        desc: '为本体新增一个属性字段。',
        params: [
          { name: 'name', loc: 'body', type: 'string', required: true, desc: '属性名称' },
          { name: 'code', loc: 'body', type: 'string', required: false, desc: '属性 API 名' },
          { name: 'data_type', loc: 'body', type: 'string', required: true, desc: '类型：string / number / boolean / date 等' },
          { name: 'is_required', loc: 'body', type: 'boolean', required: false, desc: '是否必填' },
          { name: 'default_value', loc: 'body', type: 'string', required: false, desc: '默认值' },
          { name: 'sort_order', loc: 'body', type: 'integer', required: false, desc: '排序值' },
        ],
        req: `curl -X POST ${apiBase}/ontology-categories/cat1a2b3c4d5e/ontologies/ont9f8e7d6c5b4/attributes \\\n  -H "Content-Type: application/json" \\\n  -d '{"name": "设备编号", "code": "equip_no", "data_type": "string", "is_required": true}'`,
        res: `{\n  "id": "attr5e6f7a8b9c",\n  "ontology_id": "ont9f8e7d6c5b4",\n  "name": "设备编号",\n  "code": "equip_no",\n  "data_type": "string",\n  "is_required": 1,\n  "sort_order": 0\n}`,
      },
      {
        method: 'PUT', path: '/ontology-categories/{cid}/ontologies/{oid}/attributes', name: '批量保存属性',
        desc: '整体提交属性列表，带 id 的更新、不带 id 的新增，一次调用完成全量同步。',
        params: [{ name: 'attributes', loc: 'body', type: 'object[]', required: true, desc: '属性数组（可含 id 表示更新）' }],
      },
      {
        method: 'DELETE', path: '/ontology-categories/{cid}/ontologies/{oid}/attributes/{attr_id}', name: '删除属性',
        desc: '删除本体上的单个属性。',
        params: [{ name: 'attr_id', loc: 'path', type: 'string', required: true, desc: '属性 id' }],
        res: `{ "status": "deleted" }`,
      },
      {
        method: 'GET', path: '/ontology-categories/{cid}/ontologies/{oid}/merged-attributes', name: '获取合并属性',
        desc: '返回本体自有属性与引用模板属性的合并视图，是读取"最终生效属性"的推荐接口。',
        params: [
          { name: 'cid', loc: 'path', type: 'string', required: true, desc: '类别 id' },
          { name: 'oid', loc: 'path', type: 'string', required: true, desc: '本体 id' },
        ],
      },
    ],
  },
  {
    key: 'ontology',
    icon: iconLink,
    title: '关系与约束',
    desc: '关系字典定义实体间连接语义，三元组约束限定"头实体-关系-尾实体"的合法组合。',
    apis: [
      {
        method: 'GET', path: '/ontology-categories/{category_id}/relations', name: '获取关系列表',
        desc: '返回类别下的关系字典。',
        params: [{ name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' }],
      },
      {
        method: 'POST', path: '/ontology-categories/{category_id}/relations', name: '创建关系',
        desc: '新增一条关系定义，可声明基数、反向名称、对称与传递性。',
        params: [
          { name: 'name', loc: 'body', type: 'string', required: true, desc: '关系名称（如"隶属于"）' },
          { name: 'code', loc: 'body', type: 'string', required: false, desc: '关系 API 名' },
          { name: 'cardinality', loc: 'body', type: 'string', required: false, desc: '基数，如 1:1 / 1:N / N:N' },
          { name: 'inverse_name', loc: 'body', type: 'string', required: false, desc: '反向关系名' },
          { name: 'is_symmetric', loc: 'body', type: 'boolean', required: false, desc: '是否对称关系' },
          { name: 'is_transitive', loc: 'body', type: 'boolean', required: false, desc: '是否传递关系' },
        ],
        req: `curl -X POST ${apiBase}/ontology-categories/cat1a2b3c4d5e/relations \\\n  -H "Content-Type: application/json" \\\n  -d '{"name": "隶属于", "code": "belongs_to", "cardinality": "N:1"}'`,
        res: `{\n  "id": "rel3d4e5f6a7b",\n  "category_id": "cat1a2b3c4d5e",\n  "name": "隶属于",\n  "code": "belongs_to",\n  "cardinality": "N:1",\n  "status": "active"\n}`,
      },
      {
        method: 'PUT', path: '/ontology-categories/{category_id}/relations/{relation_id}', name: '更新关系',
        desc: '更新关系定义，字段均可选。',
        params: [{ name: 'relation_id', loc: 'path', type: 'string', required: true, desc: '关系 id' }],
      },
      {
        method: 'DELETE', path: '/ontology-categories/{category_id}/relations/{relation_id}', name: '删除关系',
        desc: '从字典中删除一条关系。',
        params: [{ name: 'relation_id', loc: 'path', type: 'string', required: true, desc: '关系 id' }],
        res: `{ "status": "deleted" }`,
      },
      {
        method: 'GET', path: '/ontology-categories/{category_id}/constraints', name: '获取约束列表',
        desc: '返回类别下的全部三元组约束。',
        params: [{ name: 'category_id', loc: 'path', type: 'string', required: true, desc: '类别 id' }],
      },
      {
        method: 'POST', path: '/ontology-categories/{category_id}/constraints', name: '创建约束',
        desc: '声明"头实体类型 - 关系 - 尾实体类型"的合法组合，解析与写入时会据此校验。',
        params: [
          { name: 'source_ontology_id', loc: 'body', type: 'string', required: true, desc: '头实体本体 id' },
          { name: 'relation_id', loc: 'body', type: 'string', required: true, desc: '关系 id' },
          { name: 'target_ontology_id', loc: 'body', type: 'string', required: true, desc: '尾实体本体 id' },
          { name: 'description', loc: 'body', type: 'string', required: false, desc: '约束说明' },
        ],
        req: `curl -X POST ${apiBase}/ontology-categories/cat1a2b3c4d5e/constraints \\\n  -H "Content-Type: application/json" \\\n  -d '{"source_ontology_id": "ont9f8e7d6c5b4", "relation_id": "rel3d4e5f6a7b", "target_ontology_id": "ont2b3c4d5e6f7"}'`,
      },
      {
        method: 'PUT', path: '/ontology-categories/{category_id}/constraints/{constraint_id}', name: '更新约束',
        desc: '修改约束的头/尾本体或关系。',
        params: [{ name: 'constraint_id', loc: 'path', type: 'string', required: true, desc: '约束 id' }],
      },
      {
        method: 'DELETE', path: '/ontology-categories/{category_id}/constraints/{constraint_id}', name: '删除约束',
        desc: '删除一条三元组约束。',
        params: [{ name: 'constraint_id', loc: 'path', type: 'string', required: true, desc: '约束 id' }],
        res: `{ "status": "deleted" }`,
      },
    ],
  },
  {
    key: 'ontology',
    icon: iconTemplate,
    title: '属性模板',
    desc: '全局属性模板可被多个本体引用，修改模板属性即同步到所有引用它的本体。',
    apis: [
      {
        method: 'GET', path: '/attribute-templates', name: '获取模板列表',
        desc: '返回全部属性模板，可按名称模糊搜索。',
        params: [{ name: 'q', loc: 'query', type: 'string', required: false, desc: '名称模糊搜索' }],
      },
      {
        method: 'POST', path: '/attribute-templates', name: '创建模板',
        desc: '创建一个空的属性模板。',
        params: [
          { name: 'name', loc: 'body', type: 'string', required: true, desc: '模板名称' },
          { name: 'description', loc: 'body', type: 'string', required: false, desc: '描述' },
        ],
        req: `curl -X POST ${apiBase}/attribute-templates \\\n  -H "Content-Type: application/json" \\\n  -d '{"name": "通用审计字段"}'`,
      },
      {
        method: 'GET', path: '/attribute-templates/{template_id}', name: '获取模板详情',
        desc: '返回模板信息及其属性列表。',
        params: [{ name: 'template_id', loc: 'path', type: 'string', required: true, desc: '模板 id' }],
      },
      {
        method: 'GET', path: '/attribute-templates/{template_id}/attributes', name: '获取模板属性',
        desc: '仅返回模板下的属性列表。',
        params: [{ name: 'template_id', loc: 'path', type: 'string', required: true, desc: '模板 id' }],
      },
    ],
  },
  {
    key: 'ontology',
    icon: iconBook,
    title: '知识库绑定本体',
    desc: '将知识库绑定到本体类别后，文件解析抽取实体 / 关系时会遵循该类别的本体与约束。',
    apis: [
      {
        method: 'GET', path: '/kb/{kb_id}/ontology', name: '查询绑定',
        desc: '返回知识库当前绑定的本体类别，未绑定时为空。',
        params: [{ name: 'kb_id', loc: 'path', type: 'string', required: true, desc: '知识库 id' }],
      },
      {
        method: 'PUT', path: '/kb/{kb_id}/ontology', name: '绑定 / 切换本体',
        desc: '为知识库绑定本体类别；重复调用可切换到其他类别。',
        params: [
          { name: 'kb_id', loc: 'path', type: 'string', required: true, desc: '知识库 id' },
          { name: 'category_id', loc: 'body', type: 'string', required: true, desc: '目标类别 id' },
        ],
        req: `curl -X PUT ${apiBase}/kb/a1b2c3d4e5f6/ontology \\\n  -H "Content-Type: application/json" \\\n  -d '{"category_id": "cat1a2b3c4d5e"}'`,
      },
      {
        method: 'DELETE', path: '/kb/{kb_id}/ontology', name: '解除绑定',
        desc: '移除知识库与本体类别的绑定关系。',
        params: [{ name: 'kb_id', loc: 'path', type: 'string', required: true, desc: '知识库 id' }],
        res: `{ "status": "unbound" }`,
      },
    ],
  },
  {
    key: 'ontology',
    icon: iconSwap,
    title: 'Excel 导入导出',
    desc: '把本体定义批量导入 / 导出为 Excel，适合从既有数据字典迁移或跨环境同步。',
    apis: [
      {
        method: 'GET', path: '/ontology/export/excel', name: '导出本体',
        desc: '导出本体定义为 xlsx 文件，可用 scope 控制范围，不传类别则导出全部。',
        params: [
          { name: 'scope', loc: 'query', type: 'string', required: false, desc: 'full / ontologies / relations / constraints / templates，默认 full' },
          { name: 'category_ids', loc: 'query', type: 'string[]', required: false, desc: '按类别过滤（可多个）' },
          { name: 'template_ids', loc: 'query', type: 'string[]', required: false, desc: 'scope=templates 时按模板过滤' },
        ],
        req: `curl -o ontology.xlsx "${apiBase}/ontology/export/excel?scope=full&category_ids=cat1a2b3c4d5e"`,
      },
      {
        method: 'GET', path: '/ontology/import/template', name: '下载导入模板',
        desc: '下载标准导入模板（xlsx），含各 sheet 表头与示例行。',
        params: [
          { name: 'scope', loc: 'query', type: 'string', required: false, desc: '模板范围，默认 full' },
          { name: 'with_example', loc: 'query', type: 'boolean', required: false, desc: '是否包含示例数据，默认 true' },
        ],
      },
      {
        method: 'POST', path: '/ontology/import/excel', name: '导入本体',
        desc: '上传 xlsx 批量导入。已存在（按业务唯一键）则覆盖更新，不存在则新建，不删除既有数据；dry_run=true 时只校验不写入。',
        params: [
          { name: 'file', loc: 'form', type: 'file', required: true, desc: 'xlsx 文件' },
          { name: 'scope', loc: 'query', type: 'string', required: false, desc: '只导入哪些 sheet，默认 full' },
          { name: 'dry_run', loc: 'query', type: 'boolean', required: false, desc: '仅校验，默认 false' },
        ],
        req: `curl -X POST "${apiBase}/ontology/import/excel?dry_run=true" \\\n  -F "file=@本体导入.xlsx"`,
      },
    ],
  },
  {
    key: 'kb',
    icon: iconBook,
    title: '知识库管理',
    desc: '知识库的创建、查询、更新与删除。知识库是文件与问答的载体，接入时先创建或复用已有知识库。',
    apis: [
      {
        method: 'GET', path: '/api/kb', name: '获取知识库列表',
        desc: '返回平台上全部知识库，可用于对接方选择目标知识库。',
        res: `[\n  {\n    "id": "a1b2c3d4e5f6",\n    "name": "产品手册",\n    "description": "",\n    "created_at": "2026-09-01T10:00:00"\n  }\n]`,
      },
      {
        method: 'POST', path: '/api/kb', name: '创建知识库',
        desc: '按名称创建一个空知识库，返回新建的知识库对象。',
        params: [{ name: 'name', loc: 'body', type: 'string', required: true, desc: '知识库名称' }],
        req: `curl -X POST ${apiBase}/api/kb \\\n  -H "Content-Type: application/json" \\\n  -d '{"name": "我的知识库"}'`,
        res: `{\n  "id": "a1b2c3d4e5f6",\n  "name": "我的知识库",\n  "description": "",\n  "created_at": "2026-09-01T10:00:00"\n}`,
      },
      {
        method: 'GET', path: '/api/kb/{kb_id}', name: '获取知识库详情',
        desc: '按 id 查询单个知识库，不存在时返回 404。',
        params: [{ name: 'kb_id', loc: 'path', type: 'string', required: true, desc: '知识库 id' }],
      },
      {
        method: 'PUT', path: '/api/kb/{kb_id}', name: '更新知识库',
        desc: '更新知识库名称与描述，两个字段均可选，仅传需要修改的字段。',
        params: [
          { name: 'kb_id', loc: 'path', type: 'string', required: true, desc: '知识库 id' },
          { name: 'name', loc: 'body', type: 'string', required: false, desc: '新名称' },
          { name: 'description', loc: 'body', type: 'string', required: false, desc: '新描述' },
        ],
        req: `curl -X PUT ${apiBase}/api/kb/a1b2c3d4e5f6 \\\n  -H "Content-Type: application/json" \\\n  -d '{"name": "新名称", "description": "接入方文档库"}'`,
      },
      {
        method: 'POST', path: '/api/kb/batch-delete', name: '批量删除知识库',
        desc: '一次性删除多个知识库及其下全部文件与已入库数据，不可恢复。',
        params: [{ name: 'kb_ids', loc: 'body', type: 'string[]', required: true, desc: '要删除的知识库 id 列表' }],
        req: `curl -X POST ${apiBase}/api/kb/batch-delete \\\n  -H "Content-Type: application/json" \\\n  -d '{"kb_ids": ["a1b2c3d4e5f6", "b2c3d4e5f6a1"]}'`,
      },
      {
        method: 'DELETE', path: '/api/kb/{kb_id}', name: '删除知识库',
        desc: '删除单个知识库，级联删除其下所有文件、分片、向量与图谱数据。',
        params: [{ name: 'kb_id', loc: 'path', type: 'string', required: true, desc: '知识库 id' }],
        res: `{ "status": "deleted" }`,
      },
    ],
  },
  {
    key: 'files',
    icon: iconFile,
    title: '文件上传与解析',
    desc: '大文件分片上传，触发解析入库（分块 → 向量化 → 图谱抽取），并跟踪处理进度。',
    apis: [
      {
        method: 'POST', path: '/api/upload/chunk', name: '分片上传文件',
        desc: '将文件切成固定大小的分片逐个上传。同一 file_id 的分片按 chunk_index 乱序到达也可正确合并；首片会自动创建文件记录。',
        params: [
          { name: 'file_id', loc: 'form', type: 'string', required: true, desc: '接入方生成的文件唯一 id（建议 uuid）' },
          { name: 'file_name', loc: 'form', type: 'string', required: true, desc: '文件名（含扩展名）' },
          { name: 'file_size', loc: 'form', type: 'integer', required: true, desc: '文件总字节数' },
          { name: 'kb_id', loc: 'form', type: 'string', required: true, desc: '目标知识库 id' },
          { name: 'chunk_index', loc: 'form', type: 'integer', required: true, desc: '当前分片序号，从 0 开始' },
          { name: 'total_chunks', loc: 'form', type: 'integer', required: true, desc: '分片总数' },
          { name: 'chunk', loc: 'form', type: 'file', required: true, desc: '分片二进制数据' },
        ],
        req: `curl -X POST ${apiBase}/api/upload/chunk \\\n  -F "file_id=3f8a2c1e9b4d" \\\n  -F "file_name=产品白皮书.pdf" \\\n  -F "file_size=10485760" \\\n  -F "kb_id=a1b2c3d4e5f6" \\\n  -F "chunk_index=0" \\\n  -F "total_chunks=10" \\\n  -F "chunk=@part_0.bin"`,
      },
      {
        method: 'GET', path: '/api/files', name: '获取文件列表',
        desc: '返回平台上全部文件及其所属知识库、大小、状态、进度等信息。',
      },
      {
        method: 'POST', path: '/api/files/{file_id}/process', name: '触发文件解析',
        desc: '对上传完成的文件启动解析流水线（分块、向量化、图谱抽取）。文件未处于待处理状态时返回 400。',
        params: [
          { name: 'file_id', loc: 'path', type: 'string', required: true, desc: '文件 id' },
          { name: 'extract_graph', loc: 'query', type: 'boolean', required: false, desc: '是否抽取图谱，默认 true' },
        ],
        req: `curl -X POST "${apiBase}/api/files/3f8a2c1e9b4d/process?extract_graph=true"`,
        res: `{ "status": "processing" }`,
      },
      {
        method: 'POST', path: '/api/files/{file_id}/reprocess', name: '重新解析文件',
        desc: '清理该文件旧的分片、向量与图谱数据后重新走解析流水线。',
        params: [
          { name: 'file_id', loc: 'path', type: 'string', required: true, desc: '文件 id' },
          { name: 'extract_graph', loc: 'query', type: 'boolean', required: false, desc: '是否抽取图谱，默认 true' },
        ],
      },
      {
        method: 'GET', path: '/api/files/{file_id}/status', name: '查询处理状态',
        desc: '轮询获取文件当前处理状态、总进度、阶段明细与日志。',
        params: [{ name: 'file_id', loc: 'path', type: 'string', required: true, desc: '文件 id' }],
        res: `{\n  "status": "processing",\n  "progress": 45,\n  "message": "向量化中…",\n  "detail": { "stages": { "chunking": {"progress": 100}, "vectorizing": {"progress": 60} } },\n  "logs": "[INFO] 开始解析"\n}`,
      },
      {
        method: 'GET', path: '/api/files/{file_id}/events', name: '订阅处理状态流',
        desc: 'SSE 接口。连接后立即推送一次当前状态，此后每次状态变更实时推送，适合替代轮询；断开连接即取消订阅。',
        params: [{ name: 'file_id', loc: 'path', type: 'string', required: true, desc: '文件 id' }],
        res: `data: {"status": "processing", "progress": 30, ...}\n\ndata: {"status": "ready", "progress": 100, ...}`,
      },
      {
        method: 'POST', path: '/api/files/{file_id}/cancel', name: '取消解析',
        desc: '取消正在进行的解析任务，清理已入库数据并将文件重置为待处理状态。',
        params: [{ name: 'file_id', loc: 'path', type: 'string', required: true, desc: '文件 id' }],
        res: `{ "status": "cancelled" }`,
      },
      {
        method: 'DELETE', path: '/api/files/{file_id}', name: '删除文件',
        desc: '删除文件及其全部分片、向量与图谱数据。',
        params: [{ name: 'file_id', loc: 'path', type: 'string', required: true, desc: '文件 id' }],
        res: `{ "status": "deleted" }`,
      },
      {
        method: 'POST', path: '/api/files/batch-delete', name: '批量删除文件',
        desc: '一次性删除多个文件及其入库数据。',
        params: [{ name: 'file_ids', loc: 'body', type: 'string[]', required: true, desc: '要删除的文件 id 列表' }],
        req: `curl -X POST ${apiBase}/api/files/batch-delete \\\n  -H "Content-Type: application/json" \\\n  -d '{"file_ids": ["3f8a2c1e9b4d", "5e6f7a8b9c0d"]}'`,
      },
    ],
  },
  {
    key: 'query',
    icon: iconChat,
    title: '知识问答',
    desc: '基于指定知识库的检索增强问答（RAG），以 SSE 流式返回引用片段与生成内容。',
    apis: [
      {
        method: 'POST', path: '/api/query', name: '知识库问答',
        desc: '先做混合检索（向量 + BM25），随后流式生成回答。事件顺序：先推送一条 type=chunks（带编号的引用片段），再连续推送 type=token（增量文本），最后以 [DONE] 结束。',
        params: [
          { name: 'kb_id', loc: 'body', type: 'string', required: true, desc: '知识库 id' },
          { name: 'query', loc: 'body', type: 'string', required: true, desc: '用户问题' },
        ],
        req: `curl -N -X POST ${apiBase}/api/query \\\n  -H "Content-Type: application/json" \\\n  -d '{"kb_id": "a1b2c3d4e5f6", "query": "产品的保修期限是多久？"}'`,
        res: `data: {"type":"chunks","chunks":[{"index":1,"text":"……保修期为 24 个月……"}]}\n\ndata: {"type":"token","content":"产品"}\n\ndata: {"type":"token","content":"保修期为 24 个月。"}\n\ndata: [DONE]`,
      },
    ],
  },
  {
    key: 'graph',
    icon: iconGraph,
    title: '知识图谱查询',
    desc: '查询文件抽取出的实体关系图谱，可用于接入方自建图谱可视化或关系分析。',
    apis: [
      {
        method: 'GET', path: '/api/graph/relation-types', name: '获取关系类型列表',
        desc: '返回指定知识库（可再按文件过滤）内出现的全部关系类型及数量，用于构建筛选器。',
        params: [
          { name: 'kb_id', loc: 'query', type: 'string', required: true, desc: '知识库 id' },
          { name: 'file_id', loc: 'query', type: 'string', required: false, desc: '按文件过滤' },
        ],
        req: `curl "${apiBase}/api/graph/relation-types?kb_id=a1b2c3d4e5f6"`,
      },
      {
        method: 'GET', path: '/api/graph/view', name: '获取图谱视图',
        desc: '分页返回实体与关系，支持按实体名称、关系类型过滤。',
        params: [
          { name: 'kb_id', loc: 'query', type: 'string', required: true, desc: '知识库 id' },
          { name: 'file_id', loc: 'query', type: 'string', required: false, desc: '按文件过滤' },
          { name: 'entity_query', loc: 'query', type: 'string', required: false, desc: '实体名称模糊搜索' },
          { name: 'relation_type', loc: 'query', type: 'string', required: false, desc: '按关系类型过滤' },
          { name: 'limit', loc: 'query', type: 'integer', required: false, desc: '返回数量上限，默认 200，最大 500' },
          { name: 'offset', loc: 'query', type: 'integer', required: false, desc: '分页偏移，默认 0' },
        ],
        req: `curl "${apiBase}/api/graph/view?kb_id=a1b2c3d4e5f6&limit=200&offset=0"`,
      },
      {
        method: 'GET', path: '/api/graph/expand', name: '展开实体邻居',
        desc: '查询某实体的一跳邻居（懒加载），按关系 id 稳定分页，适合交互式逐层展开。',
        params: [
          { name: 'entity_id', loc: 'query', type: 'string', required: true, desc: '实体 id' },
          { name: 'relation_type', loc: 'query', type: 'string', required: false, desc: '按关系类型过滤' },
          { name: 'limit', loc: 'query', type: 'integer', required: false, desc: '返回数量上限，默认 50，最大 200' },
          { name: 'offset', loc: 'query', type: 'integer', required: false, desc: '分页偏移，默认 0' },
        ],
        req: `curl "${apiBase}/api/graph/expand?entity_id=ent_9f8e7d&limit=50"`,
      },
    ],
  },
]

// 搜索 + 分组过滤
const visibleGroups = computed(() => {
  const kw = keyword.value.toLowerCase()
  return groups
    .filter(g => activeGroup.value === 'all' || activeGroup.value === g.key)
    .map(g => ({
      ...g,
      apis: kw
        ? g.apis.filter(a => `${a.path} ${a.name} ${a.desc}`.toLowerCase().includes(kw))
        : g.apis,
    }))
    .filter(g => g.apis.length)
})

function keyOf(api, suffix) {
  return `${api.method} ${api.path} ${suffix}`
}
function isOpen(api) {
  return expandedKeys.value.has(api.method + api.path)
}
function toggle(api) {
  const key = api.method + api.path
  const next = new Set(expandedKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedKeys.value = next
}
function copyText(text, key) {
  const done = () => {
    copiedKey.value = key
    clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => { copiedKey.value = '' }, 1600)
  }
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done))
  } else {
    fallbackCopy(text, done)
  }
}
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  try { document.execCommand('copy') } catch { /* ignore */ }
  document.body.removeChild(ta)
  done()
}
</script>

<style scoped>
.apid-page { max-width: 960px; margin: 0 auto; padding-bottom: 24px; }

/* ─── 页头 ─── */
.apid-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.apid-head h1 { font-size: 22px; font-weight: 700; color: var(--c-fg); margin-bottom: 6px; }
.apid-head p { font-size: 13px; color: var(--c-secondary); line-height: 1.7; max-width: 640px; margin: 0; }
.apid-link { color: var(--c-accent); font-weight: 600; text-decoration: none; }
.apid-link:hover { text-decoration: underline; }
.apid-link-sep { margin: 0 6px; opacity: 0.5; }
.apid-ext-btn {
  flex-shrink: 0; display: inline-flex; align-items: center; gap: 6px; margin-top: 4px;
  padding: 8px 14px; border-radius: var(--radius-sm);
  border: 1px solid var(--c-border); background: var(--c-panel);
  color: var(--c-fg); font-size: 13px; font-weight: 600; text-decoration: none;
  transition: background 150ms, border-color 150ms;
}
.apid-ext-btn:hover { background: var(--c-muted); border-color: var(--c-accent); }

/* ─── 卡片 / 接入说明 ─── */
.apid-card {
  border: 1px solid var(--c-border); border-radius: var(--radius);
  background: var(--c-panel-elevated); padding: 16px 20px; margin-bottom: 16px;
}
.apid-intro-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.apid-intro-label { font-size: 12px; font-weight: 700; color: var(--c-secondary); }
.apid-base-url {
  display: inline-flex; align-items: center; gap: 10px;
  padding: 6px 8px 6px 12px; border-radius: var(--radius-sm);
  background: var(--c-muted); border: 1px solid var(--c-border);
  font-family: var(--font-mono, ui-monospace, monospace); font-size: 13px; color: var(--c-fg);
}
.apid-notes { margin: 0; padding: 0 0 0 18px; display: flex; flex-direction: column; gap: 5px; }
.apid-notes li { font-size: 12.5px; color: var(--c-secondary); line-height: 1.7; }
.apid-notes code {
  padding: 1px 5px; border-radius: 4px; background: var(--c-muted);
  font-family: var(--font-mono, ui-monospace, monospace); font-size: 11.5px; color: var(--c-fg);
}

/* ─── 工具栏 ─── */
.apid-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.apid-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.apid-chip {
  padding: 6px 14px; border-radius: 999px; border: 1px solid var(--c-border);
  background: var(--c-panel); color: var(--c-fg); font-size: 12.5px; font-weight: 600; cursor: pointer;
  transition: background 150ms, border-color 150ms, color 150ms;
}
.apid-chip:hover { background: var(--c-muted); }
.apid-chip.is-active { background: var(--c-accent); color: #fff; border-color: var(--c-accent); }
.apid-search {
  display: flex; align-items: center; gap: 8px; min-width: 220px;
  padding: 0 12px; border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); color: var(--c-secondary);
  transition: border-color 150ms, box-shadow 150ms;
}
.apid-search:focus-within { border-color: var(--c-accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c-accent) 22%, transparent); }
.apid-search input {
  flex: 1; border: 0; outline: none; background: transparent;
  padding: 8px 0; font-size: 13px; font-family: var(--font); color: var(--c-fg);
}
.apid-search input::placeholder { color: var(--c-secondary); opacity: 0.7; }

/* ─── 分组 ─── */
.apid-group { margin-bottom: 22px; }
.apid-group-head { display: flex; align-items: center; gap: 12px; padding: 4px 4px 10px; }
.apid-group-icon {
  flex-shrink: 0; display: flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: 10px;
  background: color-mix(in srgb, var(--c-accent) 12%, transparent);
  color: var(--c-accent);
}
.apid-group-title { flex: 1; min-width: 0; }
.apid-group-title h2 { font-size: 15px; font-weight: 700; color: var(--c-fg); margin: 0 0 2px; }
.apid-group-title p { font-size: 12px; color: var(--c-secondary); margin: 0; line-height: 1.6; }
.apid-group-count {
  flex-shrink: 0; font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 999px;
  background: var(--c-muted); color: var(--c-secondary);
}

/* ─── 接口行 ─── */
.apid-api {
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  background: var(--c-panel); margin-bottom: 8px; overflow: hidden;
  transition: border-color 150ms;
}
.apid-api:has(.apid-api-row.open) { border-color: color-mix(in srgb, var(--c-accent) 55%, var(--c-border)); }
.apid-api-row {
  width: 100%; display: flex; align-items: center; gap: 12px;
  padding: 11px 14px; border: 0; background: transparent; cursor: pointer; text-align: left;
  transition: background 150ms;
}
.apid-api-row:hover { background: var(--c-muted); }
.apid-method {
  flex-shrink: 0; width: 58px; text-align: center;
  font-family: var(--font-mono, ui-monospace, monospace); font-size: 11px; font-weight: 700; letter-spacing: 0.4px;
  padding: 4px 0; border-radius: 6px;
}
.apid-method.get { background: rgba(59, 130, 246, 0.14); color: #3b82f6; }
.apid-method.post { background: rgba(34, 197, 94, 0.14); color: #22c55e; }
.apid-method.put { background: rgba(245, 158, 11, 0.16); color: #f59e0b; }
.apid-method.delete { background: rgba(239, 68, 68, 0.14); color: #ef4444; }
.apid-path {
  flex-shrink: 0; font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 12.5px; font-weight: 600; color: var(--c-fg);
}
.apid-api-name {
  flex: 1; min-width: 0; font-size: 13px; color: var(--c-secondary);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.apid-chevron { flex-shrink: 0; color: var(--c-secondary); transition: transform 180ms; }
.apid-api-row.open .apid-chevron { transform: rotate(180deg); }

/* ─── 接口详情 ─── */
.apid-api-detail { padding: 4px 16px 16px; border-top: 1px dashed var(--c-border); }
.apid-api-desc { font-size: 13px; color: var(--c-secondary); line-height: 1.8; margin: 12px 0; }
.apid-params { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
.apid-params th, .apid-params td {
  text-align: left; font-size: 12px; padding: 7px 10px;
  border: 1px solid var(--c-border);
}
.apid-params th { background: var(--c-muted); color: var(--c-secondary); font-weight: 600; white-space: nowrap; }
.apid-params td { color: var(--c-fg); line-height: 1.6; }
.apid-params td:first-child code { font-family: var(--font-mono, ui-monospace, monospace); font-weight: 700; color: var(--c-accent); }
.apid-loc {
  display: inline-block; padding: 1px 8px; border-radius: 999px;
  background: var(--c-muted); color: var(--c-secondary); font-size: 11px; font-weight: 600;
}

.apid-code-block { margin-bottom: 10px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); overflow: hidden; }
.apid-code-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 12px; background: var(--c-muted);
  font-size: 11.5px; font-weight: 700; color: var(--c-secondary); letter-spacing: 0.3px;
}
.apid-code {
  margin: 0; padding: 12px 14px; overflow-x: auto;
  background: color-mix(in srgb, var(--c-muted) 45%, transparent);
  font-family: var(--font-mono, ui-monospace, monospace); font-size: 12px; line-height: 1.7;
  color: var(--c-fg);
}

.apid-copy {
  padding: 2px 10px; border-radius: 5px; border: 1px solid var(--c-border);
  background: var(--c-panel); color: var(--c-secondary);
  font-size: 11px; font-weight: 600; cursor: pointer; transition: color 150ms, border-color 150ms;
}
.apid-copy:hover { color: var(--c-fg); }
.apid-copy.done { color: var(--c-success, #22c55e); border-color: var(--c-success, #22c55e); }

.apid-empty { text-align: center; font-size: 13px; color: var(--c-secondary); padding: 40px 0; }
.apid-foot { margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--c-border); font-size: 12.5px; color: var(--c-secondary); line-height: 1.7; }

@media (max-width: 720px) {
  .apid-head { flex-direction: column; }
  .apid-path { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 200px; }
}
</style>
