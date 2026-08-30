# 工作流「HTTP 节点」功能设计

> 状态：**已实现**（2026-08-30 开发完成，56 项测试全过：`backend/test_http_node.py --e2e`）
> 日期：2026-08-29（初稿） / 2026-08-29（评审确认） / 2026-08-30（实现）
> 一句话需求：工作流画布新增「HTTP 请求」节点，支持调用**任意 RESTful 接口**（GET / POST / PUT / PATCH / DELETE / HEAD / OPTIONS），把外部系统、内部微服务、第三方 OpenAPI 纳入工作流编排。
> 关联文档：[工作流功能设计](./工作流_功能设计.md)、[条件分支规则引擎设计](./条件分支规则引擎设计.md)、[人工节点功能设计](./人工节点_功能设计.md)
> 关联代码：`backend/services/workflow_engine.py`（新增 `_exec_http`）、`backend/services/workflow_service.py`（`NODE_TYPES` + 校验）、`backend/routers/workflow.py`（`NODE_TYPES` + 测试接口）、`front/src/components/workflow/nodeMeta.js`、`front/src/components/workflow/WorkflowEditorPage.vue`

---

## 0. 评审结论（2026-08-29 确认完毕）

| # | 议题 | 结论 |
|---|------|------|
| 1 | 失败策略 | **默认宽松 `fail_on_error=false`**：非 2xx / 连接失败不终止整流，`success=false` 继续执行，由下游条件分支按 `success` / `status_code` 自行路由；需要严格模式时按节点开启 |
| 2 | 内网访问 | **默认允许 + `WORKFLOW_HTTP_ALLOW_PRIVATE_NET` 可配置关闭** |
| 3 | 重试次数 | **默认 1 次**（仅连接失败 / 超时 / 429 / 5xx 重试），可配 0~5 |
| 4 | 测试请求 | **v1 就做**：`POST /api/workflows/http-node/test` + 编辑器「发送测试」按钮 |
| 5 | 响应解析 | **v1 只自动解析 JSON → `data`；非 JSON 落 `text` 兜底**，下游用代码节点自行处理 |
| 6 | 密钥脱敏 | **前 3 字符 + `***`**（如 `eyJ***`） |

---

## 1. 需求与目标

### 1.1 场景

| 场景 | 例子 |
|------|------|
| 对接内部业务系统 | 智能体生成结论 → POST 写入 OA / ERP / 工单系统 |
| 拉取外部数据 | GET 天气 / 汇率 / 行情接口 → 交给 LLM 节点汇总 |
| 触发第三方动作 | 调用短信 / 邮件 / 钉钉机器人 Webhook 发通知 |
| 串联微服务 | 工作流充当轻量编排器，跨服务按顺序调用并传递数据 |
| 查询图/向量服务 | 调用本系统或兄弟系统暴露的 REST API |

### 1.2 目标（v1）

1. **全方法覆盖**：支持全部 RESTful 动词（GET / POST / PUT / PATCH / DELETE / HEAD / OPTIONS）；
2. **完整请求要素**：URL / Query / Header / Body（JSON・表单・纯文本・XML）/ 鉴权（Bearer・Basic・API Key）均可配置；
3. **变量互通**：所有请求要素都支持 `{{节点id.字段}}` 变量引用，响应结果作为节点输出供下游引用——与现有变量体系（§2.4）完全一致；
4. **工程可靠性**：超时、重试（指数退避）、重定向跟随、SSL 校验开关、响应体大小上限；
5. **失败可控**：非 2xx 既可以「节点失败 fail-fast」，也可以「`success=false` 继续往下走 + 条件分支自行处理」，由配置决定；
6. **可测试**：编辑器配置抽屉里一键「发送测试请求」，实时看响应（不落库、不影响工作流定义）；
7. **安全兜底**：协议白名单、内网访问开关、运行归档中的密钥脱敏。

### 1.3 非目标（v1 明确不做）

- ❌ Cookie / Session 会话保持（每个节点独立无状态请求，跨节点不共享 Cookie）；
- ❌ 文件上传（multipart/form-data）与二进制响应（图片 / 文件流）→ v2；
- ❌ GraphQL / SOAP 专项封装（可用 raw body 覆盖）；
- ❌ 分页自动拉取（游标 / page 翻页循环）→ v2（配合循环节点）；
- ❌ 响应 JSONPath 高级提取（`$.data.list[0]`）——v1 用「`data` 自动解析 + 点号路径引用」覆盖绝大多数场景；
- ❌ 代理（HTTP Proxy）配置 → 按需后补；
- ❌ 异步回调型接口（提交后轮询/等待结果）→ v2。

---

## 2. 核心设计决策

| # | 议题 | 方案 | 理由 |
|---|------|------|------|
| 1 | HTTP 客户端 | **`httpx.AsyncClient`** | 依赖已在 `requirements.txt`（URL 导入技能在用）；原生 async，与 FastAPI / 引擎同生态；API 与 requests 兼容、易测试 |
| 2 | 请求失败语义 | **`fail_on_error` 可配置，默认 `false`（宽松）** | 已确认：对接弱依赖场景多，失败不应中断整流；非 2xx 时 `success=false` 继续执行，下游用条件分支按 `success` / `status_code` 路由；严格模式按节点开启 |
| 3 | 内网 / localhost 访问 | **默认允许，`WORKFLOW_HTTP_ALLOW_PRIVATE_NET` 可关** | 本系统是企业内部工具，调内网 API 是核心场景；同时保留开关给安全要求更高的部署 |
| 4 | 重试 | **默认 1 次，可配 0~5**；仅对**连接失败 / 超时 / 429 / 5xx** 重试，4xx 不重试 | 减少偶发网络抖动导致的失败；写操作如需严格不重试可配 0 |
| 5 | 响应解析 | **按 Content-Type 自动解析 JSON → `data`；原文始终保留 → `text`** | 下游引用 `{{http_1.data.xxx}}` 保留对象语义；`text` 兜底非 JSON 与调试场景 |
| 6 | 密钥安全 | **请求配置原样存 definition；运行归档 `node_states` 里对敏感 Header / 鉴权值脱敏** | definition 属于编辑态（同代码仓库敏感度）；运行记录会被多人回看 + 定期裁剪留存，必须脱敏 |
| 7 | 测试请求 | **v1 提供** `POST /api/workflows/http-node/test` | 配置外部接口的试错成本高，编辑器内即时验证是刚需；复用 `_exec_http`，成本极低 |

---

## 3. 节点定义 `http`

### 3.1 节点一览（新增行）

| type | 名称 | 输入 | 输出 | 说明 |
|------|------|------|------|------|
| `http` | HTTP 请求 | URL·参数·请求体 | status_code + data + headers | 调用任意 RESTful 接口 |

### 3.2 `config` 结构

```json
{
  "method": "GET",                       // GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS
  "url": "https://api.example.com/users/{{start.user_id}}",

  "params": {                            // Query 参数（追加到 URL ?a=b&c=d）
    "page": "1",
    "keyword": "{{start.keyword}}"
  },
  "headers": {                           // 自定义请求头
    "X-Request-Id": "{{start.trace_id}}"
  },

  "auth": {                              // 鉴权（三选一，none 为默认）
    "type": "none",                      // none | bearer | basic | api_key
    // bearer：
    "token": "eyJhbGciOi...",
    // basic：
    "username": "admin", "password": "***",
    // api_key（加到 header 或 query）：
    "key": "X-API-Key", "value": "ak-123", "in": "header"   // in: header | query
  },

  "body": {                              // 请求体（GET/HEAD/OPTIONS 通常 none）
    "type": "none",                      // none | json | form | text | xml
    // type=json 时：data 为 JSON 对象（值支持 {{变量}}），或整串 "{{start.payload}}" 整体引用
    "data": { "question": "{{agent_1.answer}}", "top_k": 5 },
    // type=form 时：data 为键值对（application/x-www-form-urlencoded）
    // type=text 时：data 为字符串，需配 content_type（默认 text/plain）
    // type=xml 时：data 为 XML 字符串（Content-Type: application/xml）
    "content_type": ""
  },

  "timeout_seconds": 30,                 // 整体超时（连接+读取）
  "max_retries": 1,                      // 0~5，默认 1；仅对连接失败/超时/429/5xx 生效
  "verify_ssl": true,                    // 关闭后跳过证书校验（内网自签证书场景）
  "follow_redirects": true,              // 跟随 3xx 重定向（最多 10 跳，httpx 默认）
  "fail_on_error": false,                // 非 2xx：true=节点失败停流；false=success=false 继续（默认宽松）

  "output_fields": ["success", "status_code", "headers", "data"]   // 输出投影，惯例字段
}
```

**变量渲染时机**：执行前对 `url / params / headers / auth（token/username/password/key/value）/ body.data` 统一过一遍现有 `render(context)`——整串单引用保留原类型（如 `{{start.payload}}` 直接变成 dict），嵌入式引用字符串化拼接，与现有语义完全一致。

**鉴权与 Header 的合并**：`auth` 产生的头（`Authorization: Bearer x`、`Authorization: Basic x`、`X-API-Key: x`）在用户自定义 `headers` **之后**写入，即 auth 优先级更高（避免手写头与鉴权配置打架时出现歧义）。

### 3.3 输出契约

```json
{
  "success": true,           // 2xx 且无异常 → true
  "status_code": 200,
  "reason": "OK",
  "headers": { "content-type": "application/json", "x-trace-id": "..." },
  "data": { "id": 1, "list": [ { "name": "张三" } ] },   // 响应为 JSON 时自动解析；否则为 null
  "text": "{\"id\":1,...}",  // 响应原文（超长按上限截断）；JSON 时也保留，便于调试
  "duration_ms": 128,
  "attempts": 1,             // 实际请求次数（含重试）
  "error": null              // 连接失败/超时/超大小限/非法URL 时的错误信息
}
```

- 下游引用示例：`{{http_1.status_code}}`、`{{http_1.data.list[0].name}}`（沿用现有 `_lookup` 路径语法）、`{{http_1.headers.x-trace-id}}`；
- `FIXED_OUTPUTS` 新增：`"http": {"success", "status_code", "reason", "headers", "data", "text", "duration_ms", "error", "attempts"}`（前端锁定不可删）；
- 现有 `_truncate_output` / `WORKFLOW_NODE_OUTPUT_LIMIT` 对超大响应统一截断，逻辑复用不改。

### 3.4 失败语义（重要）

| 情况 | `fail_on_error=false`（默认，宽松） | `fail_on_error=true`（严格） |
|------|------------------------------|------------------------|
| 连接失败 / 超时 / DNS 错误 / 重试耗尽 | 正常返回输出：`success=false, status_code=null, error="..."`，继续执行 | `node_failed`，整流失败（现有 fail-fast） |
| 非 2xx（如 404、500） | 正常返回：`success=false, status_code=404, data/text=响应体`，继续执行 | `node_failed` |
| 2xx | `node_finished` | 同左 |

默认（宽松）时下游典型用法：`condition` 节点判 `{{http_1.success}}` 或 `{{http_1.status_code}} == 200` 走不同分支（重试通知 / 人工介入 / 降级路径）。

### 3.5 校验（`validate_definition` 新增 `_validate_http_node`）

1. `url` 必填、渲染前允许含 `{{变量}}`，剥掉变量占位后必须以 `http://` 或 `https://` 开头；
2. `method` 在 7 个枚举值内（缺省 `GET`）；
3. `auth.type` ∈ {none, bearer, basic, api_key}；bearer 需 `token`；basic 需 `username`；api_key 需 `key`+`value`+`in`；
4. `body.type` ∈ {none, json, form, text, xml}；`json`/`form` 的 `data` 必须是对象或整串 `{{...}}`；`text`/`xml` 的 `data` 必须是字符串；
5. `max_retries` ∈ [0, 5]；`timeout_seconds` ∈ (0, 300]；
6. `method` 为 GET/HEAD/OPTIONS 且 `body.type != none` → 警告但不阻断（部分老接口就是 GET 带体）。

---

## 4. 执行引擎设计（`workflow_engine.py`）

### 4.1 新增 `_exec_http`

```python
async def _exec_http(cfg: dict, context: dict) -> dict:
    # 1. render：url / params / headers / auth / body 全量渲染变量
    # 2. 预检：scheme 白名单（http/https）；内网检测（可选关闭）
    # 3. httpx.AsyncClient(timeout, verify, follow_redirects) 内重试循环：
    #    for attempt in 1..max_retries+1:
    #        try: resp = await client.request(...)
    #        except (ConnectError, TimeoutException) -> 可重试
    #        429/5xx -> 可重试（指数退避 0.5s * 2^n，上限 8s）
    #        4xx -> 不重试直接返回
    # 4. 流式读取响应体，超过 WORKFLOW_HTTP_MAX_RESPONSE_MB（默认 10MB）报错
    # 5. 按 Content-Type 解析 JSON -> data；原文 -> text
    # 6. fail_on_error 且非 2xx/异常 -> raise ValueError（触发现有 node_failed 路径）
    # 7. 返回 §3.3 输出契约
```

挂载点：`_execute_node` 增加分支 `if t == "http": return await _exec_http(cfg, context)`；`_summarize` 增加 http 分支：`HTTP 200 · 128ms · 3.4KB`（失败：`HTTP 请求失败：ConnectionTimeout（重试 3 次）`）。

### 4.2 与现有机制的复用关系

| 机制 | 复用方式 |
|------|----------|
| 变量渲染 | `render()` 原样调用，无改动 |
| 输出投影 | `_project_output` + `FIXED_OUTPUTS["http"]` 自动生效 |
| 输出截断 | `_truncate_output` / `WORKFLOW_NODE_OUTPUT_LIMIT` 自动生效 |
| SSE 事件 | `node_started / node_progress / node_finished / node_failed` 全部复用，无新事件 |
| 并行 / fail-fast | LangGraph superstep 调度自动覆盖（多个 HTTP 节点自动并发） |
| 人工节点挂起/续跑 | 重放机制不感知节点类型，HTTP 节点天然兼容 |

### 4.3 新增配置项（`config.py`）

| 配置 | 默认 | 说明 |
|------|------|------|
| `WORKFLOW_HTTP_TIMEOUT_SECONDS` | 30 | 节点未配置时的兜底超时 |
| `WORKFLOW_HTTP_MAX_RESPONSE_MB` | 10 | 响应体大小上限，超限节点失败 |
| `WORKFLOW_HTTP_ALLOW_PRIVATE_NET` | true | 是否允许内网 / localhost / 私有 IP 目标 |

---

## 5. 安全设计

### 5.1 SSRF 防护（企业内部工具的适度方案）

1. **协议白名单**：仅 `http://` / `https://`（`file://`、`ftp://` 等直接拒绝，httpx 本身也会拒绝，双层保险）；
2. **内网开关**：`WORKFLOW_HTTP_ALLOW_PRIVATE_NET=false` 时，解析目标主机 IP，命中回环 / 私有段（10./172.16-31./192.168./169.254./127.）→ 节点失败并提示「目标为内网地址，已被策略拦截」；
3. **重定向降级**：`follow_redirects=true` 时 httpx 不会跟随到非 http(s) 协议，天然免疫 `file://` 重定向攻击；
4. 之所以默认放行内网：本系统定位企业内部编排工具，「调内部 OA / ERP / 兄弟微服务」是第一场景；公网暴露部署时建议显式关闭该开关。

### 5.2 密钥脱敏（运行归档）

运行结束落库 `workflow_runs.node_states` 前，对 HTTP 节点输出做**请求侧脱敏**（记录请求头快照供排查，但敏感值打码）：

- 命中关键词即脱敏：header 名或 auth 字段含 `authorization` / `token` / `key` / `secret` / `password` / `cookie`（不区分大小写）→ 值替换为 `***`（前 3 字符 + `***`，如 `eyJ***`）；
- 仅脱敏**运行归档**，`definition`（编辑态配置）不动——用户改配置时需要看到原值；
- 测试接口响应同样走该脱敏逻辑。

### 5.3 超时与资源上限

- 单请求整体超时（连接 + 读）默认 30s，上限 300s，防止挂死 superstep；
- 响应体流式读取，超 `WORKFLOW_HTTP_MAX_RESPONSE_MB` 立即中断报错，防止大响应打爆内存；
- 重试退避上限 8s，总耗时仍受整体超时约束。

---

## 6. API 变更

### 6.1 既有接口（零破坏）

`NODE_TYPES`（`workflow_service.py` 与 `routers/workflow.py` 两处）各加一行 `http`，`/workflow/palette` 自动带出，旧工作流不受任何影响。

### 6.2 新增：测试请求

```
POST /api/workflows/http-node/test
{
  "config": { ...§3.2 完整节点配置... },
  "context": { "start": { "user_id": "u_1001", "keyword": "年报" } }   // 样例变量，可空
}
→ 200
{
  "output": { ...§3.3 输出契约，text 已按上限截断... },
  "request_preview": {                   // 发出去的请求回显（敏感值脱敏）
    "method": "GET",
    "url": "https://api.example.com/users/u_1001?page=1",
    "headers": { "Authorization": "Bea***" },
    "body": null
  }
}
```

- 不落库、不计入运行记录；鉴权走现有登录态；
- 用途：编辑器配置抽屉「发送测试」按钮，配置即时验证；
- **测试模式强制宽松**：忽略 `config.fail_on_error`，请求失败（网络错误/4xx/5xx）与内网拦截均返回 `success=false` + `error` 的结构化输出（而非 400），前端结果面板完整展示「请求回显 / 响应体 / 响应头 / 错误」；仅请求要素无法构建（URL/方法非法）时返回 400。

---

## 7. 前端交互（`WorkflowEditorPage.vue` + `nodeMeta.js`）

### 7.1 注册

- `nodeMeta.js`：新增 `http: { name: 'HTTP 请求', color: '#0ea5e9', icon: svg('<cloud>…') }`；
- `DEFAULT_CONFIG.http`、`OUTPUT_FIELDS_DEFAULT.http`、`FIXED_OUTPUTS.http`、`FIELD_DESC` 补充（status_code / headers / data / text / attempts 等说明）。

### 7.2 配置抽屉（按需折叠分组）

| 分组 | 内容 |
|------|------|
| 请求 | 方法下拉（7 选 1，胶囊样式）+ URL 输入框（支持变量插入按钮，沿用现有变量选择交互） |
| Query 参数 | 键值对编辑器（沿用 params JSON 编辑组件） |
| 请求头 | 键值对编辑器 |
| 鉴权 | 类型下拉 none/bearer/basic/api_key + 对应字段（密码类 input[type=password] 可切换明文） |
| 请求体 | 类型下拉 none/json/form/text/xml；json → JSON 编辑器（支持变量）；form → 键值对；text/xml → 多行文本 + Content-Type |
| 高级 | 超时 / 重试次数 / SSL 校验开关 / 跟随重定向开关 / 「非 2xx 视为节点失败」开关 |
| 测试 | 「发送测试」按钮 → 弹窗展示 `request_preview` + 状态码 + 响应体（JSON 高亮）+ 耗时；可填样例变量 |

### 7.3 运行面板

节点卡片与日志无需新交互：摘要显示 `HTTP 200 · 128ms · 3.4KB`；输出区按 `FIXED_OUTPUTS["http"]` 锁定固定字段。

---

## 8. 测试计划

| 层 | 用例 |
|----|------|
| 单测（新增 `test_http_node.py`） | 7 种方法各打一遍（本地起 `httpx.MockTransport`）；变量渲染进 URL/Header/Body/Query；JSON 响应解析 + `data.list[0].name` 引用；非 JSON（HTML）落到 text；`fail_on_error=true` 4xx/5xx → 节点失败；`=false` → success=false 继续；超时触发与重试次数（MockTransport 抛 Timeout）；响应超限报错；鉴权三种方式请求头正确；密钥脱敏；scheme 非法拒绝；内网开关关闭时拦截 |
| 集成 | 一个含「HTTP → 条件(status_code==200) → LLM」的 E2E 工作流跑通；HTTP 节点失败后 fail-fast 与恢复 |
| 手测 | 编辑器拖入节点 → 配置外部接口 → 发送测试 → 保存 → 运行 → 运行详情回看 |

---

## 9. 实施清单（确认后开发顺序）

| # | 改动 | 文件 | 规模 |
|---|------|------|------|
| 1 | `_exec_http` + FIXED_OUTPUTS + `_summarize` | `backend/services/workflow_engine.py` | ~150 行 |
| 2 | `NODE_TYPES` + `_validate_http_node` | `backend/services/workflow_service.py`、`backend/routers/workflow.py` | ~60 行 |
| 3 | 3 个 settings 配置项 | `backend/config.py` | ~5 行 |
| 4 | 测试请求接口 | `backend/routers/workflow.py` + `schemas.py` | ~40 行 |
| 5 | 节点注册 + 配置抽屉 + 测试弹窗 | `front/src/components/workflow/*` | ~300 行 |
| 6 | 单测 `test_http_node.py` | `backend/` | ~200 行 |

---

## 10. 评审记录

6 项待确认问题已于 2026-08-29 全部确认，结论并入 §0 评审结论表，无遗留开放问题。
