/**
 * 编排图 → Python 代码（可执行）。
 * 生成的 call_function 与编排模式共用同一沙箱运行时（后端注入函数注册表），
 * 代码模式可直接执行本代码；供 ActionFlowCanvas（查看生成代码）
 * 与 ServiceEditorPage（切到代码模式时兜底填充）共用。
 */

/** JSON / 任意值 → 文本 */
function asText(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'string') return v
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

/** {{ var }} → var */
function exprToPy(expr) {
  return String(expr == null ? '' : expr).replace(/\{\{\s*([^}]+?)\s*\}\}/g, (_, v) => String(v).trim())
}

/** 条件规则树 → Python 布尔表达式 */
function condToPy(rule) {
  if (!rule || !rule.rules || !rule.rules.length) return 'True'
  const opMap = {
    '>=': '>=', '<=': '<=', '>': '>', '<': '<', '==': '==', '!=': '!=',
    'contains': 'in', 'not_contains': 'not in',
  }
  const parts = rule.rules.map(r => {
    const left = exprToPy(r.field)
    const right = exprToPy(r.value)
    const op = opMap[r.operator] || r.operator || '=='
    return `${left} ${op} ${right}`
  })
  return parts.join(rule.combinator === 'or' ? ' or ' : ' and ')
}

/** 函数出参描述（return_schema 解析为易读字符串） */
export function fnReturns(fn) {
  if (!fn) return '—'
  let r = fn.return_schema
  if (!r) return '—'
  if (typeof r === 'string') {
    try { r = JSON.parse(r) } catch { return r || '—' }
  }
  if (typeof r !== 'object' || r === null) return String(r)
  const type = r.type || (Array.isArray(r) ? 'array' : '')
  const unit = r.unit
  const extra = Object.keys(r)
    .filter(k => !['type', 'unit', 'description'].includes(k))
    .map(k => `${k}:${r[k]}`)
    .join(', ')
  let s = type || ''
  if (unit) s += ` · ${unit}`
  if (extra) s += ` (${extra})`
  return s || 'object'
}

/** 编排图 → 可读 Python 预览代码 */
export function flowToCode(flow, functions, name) {
  const nodes = (flow?.nodes || []).filter(n => n && n.id)
  if (!nodes.length) return '# （空编排）'
  const fnList = Array.isArray(functions) ? functions : []
  const byId = {}
  nodes.forEach(n => { byId[n.id] = n })
  const edges = flow?.edges || []
  const targetsOf = (id, handle) =>
    edges.filter(e => e.source === id && (!handle || (e.source_handle || '') === handle))
      .map(e => byId[e.target]).filter(Boolean)
  const titleOf = n => (n ? (n.title || n.id) : '?')

  // 简单拓扑序（从 start 出发 BFS）
  const start = nodes.find(n => n.type === 'start')
  const order = []
  const seen = new Set()
  const queue = start ? [start.id] : nodes.map(n => n.id)
  while (queue.length) {
    const id = queue.shift()
    if (seen.has(id) || !byId[id]) continue
    seen.add(id)
    order.push(id)
    targetsOf(id).forEach(t => { if (!seen.has(t.id)) queue.push(t.id) })
  }
  nodes.forEach(n => { if (!seen.has(n.id)) order.push(n.id) })

  const fnOf = id => fnList.find(f => f.id === id)
  const L = []
  L.push(`# ===== 编排动作：${name || '（未命名动作）'} =====`)
  L.push(`# 由编排图自动生成：call_function 与编排模式共用同一沙箱运行时，`)
  L.push(`# 代码模式 / 编排模式均可直接执行。`)
  L.push(`def run(params, entity, context):`)
  const ends = order.filter(id => byId[id].type === 'end')
  let firstEnd = true
  for (const id of order) {
    const n = byId[id]
    const cfg = n.config || {}
    if (n.type === 'start') {
      L.push(`    # [${id}] 开始`)
    } else if (n.type === 'function') {
      const fn = fnOf(cfg.function_id)
      const ps = JSON.stringify(cfg.params || {})
      L.push(`    # [${id}] ${fn ? fn.name : '函数'} (${fn ? fn.code : cfg.function_code || '?'})`
        + `  入参: ${ps}  出参: ${fnReturns(fn)}`)
      L.push(`    ${id} = call_function("${fn ? fn.code : cfg.function_code || ''}", ${ps})  # ${fn ? fn.name : '函数'}`)
    } else if (n.type === 'code') {
      L.push(`    # [${id}] 内联代码节点（${titleOf(n)}）`)
      L.push(`    ${id} = run_${id}(params, entity, context)`)
    } else if (n.type === 'condition') {
      const tt = targetsOf(id, 'true')
      const ff = targetsOf(id, 'false')
      L.push(`    # [${id}] 条件：${titleOf(n)}`)
      L.push(`    if ${condToPy(cfg.rule)}:   # 真分支 → ${tt.map(titleOf).join('、') || '—'}`)
      L.push(`        pass`)
      L.push(`    else:                       # 假分支 → ${ff.map(titleOf).join('、') || '—'}`
        + `（${cfg.on_false === 'abort' ? '中止动作' : '继续'}）`)
      L.push(`        pass`)
    } else if (n.type === 'end') {
      L.push(`    # [${id}] 结束：${titleOf(n)}（${cfg.mode === 'abort' ? '中止' : '输出'}）`)
      if (cfg.mode === 'abort') {
        const line = `    return {"abort": True, "message": ${JSON.stringify(cfg.abort_message || '')}}`
        L.push(firstEnd ? line : `    # ${line}`)
      } else {
        const line = `    return {"result": ${asText(cfg.result)}, "edits": ${asText(cfg.edits)}}`
        L.push(firstEnd ? line : `    # ${line}`)
      }
      if (firstEnd) firstEnd = false
    } else {
      L.push(`    # [${id}] 未知节点`)
    }
  }
  return L.join('\n')
}
