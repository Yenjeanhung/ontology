const API = import.meta.env.DEV
  ? ''  // dev mode uses Vite proxy
  : 'http://localhost:8000'

export async function fetchKbs() {
  return (await (await fetch(`${API}/api/kb`)).json()) || []
}

export async function createKb(name) {
  return await (await fetch(`${API}/api/kb`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })).json()
}

export async function getKb(kbId) {
  return await (await fetch(`${API}/api/kb/${kbId}`)).json()
}

export async function deleteKb(kbId) {
  await fetch(`${API}/api/kb/${kbId}`, { method: 'DELETE' })
}

export async function updateKb(kbId, { name, description }) {
  const res = await fetch(`${API}/api/kb/${kbId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
  if (!res.ok) throw new Error('Update failed')
  return res.json()
}

export async function deleteFile(fileId) {
  await fetch(`${API}/api/files/${fileId}`, { method: 'DELETE' })
}

export async function batchDeleteFiles(fileIds) {
  const res = await fetch(`${API}/api/files/batch-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_ids: fileIds }),
  })
  if (!res.ok) throw new Error('Batch delete failed')
  return res.json()
}

export async function cancelProcessing(fileId) {
  const res = await fetch(`${API}/api/files/${fileId}/cancel`, { method: 'POST' })
  if (!res.ok) throw new Error('Cancel failed')
  return res.json()
}

export async function processFile(fileId, { extractGraph = true } = {}) {
  const res = await fetch(`${API}/api/files/${fileId}/process?extract_graph=${extractGraph}`, { method: 'POST' })
  if (!res.ok) throw new Error('Process failed')
  return res.json()
}

export async function reprocessFile(fileId, { extractGraph = true } = {}) {
  const res = await fetch(`${API}/api/files/${fileId}/reprocess?extract_graph=${extractGraph}`, { method: 'POST' })
  if (!res.ok) throw new Error('Reprocess failed')
  return res.json()
}

export async function getFileStatus(fileId) {
  const res = await fetch(`${API}/api/files/${fileId}/status`)
  if (!res.ok) throw new Error('Status failed')
  return res.json()
}

// 首页流水线大屏状态（轻量聚合：各阶段进行中文件数 + 最新爬虫任务）
export async function fetchPipelineStatus() {
  const res = await fetch(`${API}/api/files/pipeline/status`)
  if (!res.ok) throw new Error('Pipeline status failed')
  return res.json()
}

export async function fetchVectorRecords({ kbId = '', q = '', unsyncedOnly = false, limit = 100, offset = 0 } = {}) {
  const params = new URLSearchParams()
  if (kbId) params.set('kb_id', kbId)
  if (q) params.set('q', q)
  if (unsyncedOnly) params.set('unsynced_only', 'true')
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  const res = await fetch(`${API}/api/vector-records?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch vector records failed')
  return res.json()
}

export async function fetchVectorSearchTest({ kbId, query, topK = 8 }) {
  const params = new URLSearchParams()
  params.set('kb_id', kbId)
  params.set('query', query)
  params.set('top_k', String(topK))
  const res = await fetch(`${API}/api/vector-search-test?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch vector search test failed')
  return res.json()
}

export async function fetchVectorSummaryExport({ kbId = '', format = 'json' } = {}) {
  const params = new URLSearchParams()
  if (kbId) params.set('kb_id', kbId)
  params.set('format', format)
  const res = await fetch(`${API}/api/vector-summary-export?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch vector summary export failed')
  if (format === 'md') return res.text()
  return res.json()
}

export async function fetchGraphRelationTypes({ kbId, fileId = '' }) {
  const params = new URLSearchParams()
  params.set('kb_id', kbId)
  if (fileId) params.set('file_id', fileId)
  const res = await fetch(`${API}/api/graph/relation-types?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch graph relation types failed')
  return res.json()
}

export async function fetchGraphView({ kbId, fileId = '', entityQuery = '', relationType = '', limit = 200, offset = 0 }) {
  const params = new URLSearchParams()
  params.set('kb_id', kbId)
  if (fileId) params.set('file_id', fileId)
  if (entityQuery) params.set('entity_query', entityQuery)
  if (relationType) params.set('relation_type', relationType)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  const res = await fetch(`${API}/api/graph/view?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch graph view failed')
  return res.json()
}

/**
 * 流式问答。通过回调逐 token 输出。
 * @param {string} kbId
 * @param {string} query
 * @param {(chunks: Array) => void} onChunks  检索到 chunks 时触发
 * @param {(token: string) => void} onToken   每个 token 片段时触发
 * @param {number} topK
 */
export async function queryRagStream(kbId, query, { onChunks, onToken }) {
  const res = await fetch(`${API}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, kb_id: kbId }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6)
      if (payload === '[DONE]') return
      try {
        const data = JSON.parse(payload)
        if (data.type === 'chunks') onChunks(data.chunks)
        else if (data.type === 'token') onToken(data.content)
      } catch { /* skip malformed lines */ }
    }
  }
}

// 智能体（OAG）流式问答：比 queryRagStream 多 entities / subgraph 两类事件
export async function queryAgentStream(kbId, query, { onEntities, onSubgraph, onChunks, onToken, onSkills, skillIds } = {}) {
  const res = await fetch(`${API}/api/agent/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, kb_id: kbId, skill_ids: skillIds || [] }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6)
      if (payload === '[DONE]') return
      try {
        const data = JSON.parse(payload)
        if (data.type === 'skills') onSkills?.(data.skills)
        else if (data.type === 'entities') onEntities?.(data.entities)
        else if (data.type === 'subgraph') onSubgraph?.(data)
        else if (data.type === 'chunks') onChunks?.(data.chunks)
        else if (data.type === 'token') onToken?.(data.content)
      } catch { /* skip malformed lines */ }
    }
  }
}

// ───────────────────── 智能体技能（Agent Skill） ─────────────────────

export async function fetchAgentSkills() {
  const res = await fetch(`${API}/api/agent/skills`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function createAgentSkill({ name, code, description = '', instructions = '', sortOrder = 0, groupId } = {}) {
  const res = await fetch(`${API}/api/agent/skills`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, code, description, instructions, sort_order: sortOrder, group_id: groupId ?? null }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function updateAgentSkill(skillId, { name, code, description, instructions, sortOrder, isEnabled, groupId } = {}) {
  const body = {}
  if (name != null) body.name = name
  if (code != null) body.code = code
  if (description != null) body.description = description
  if (instructions != null) body.instructions = instructions
  if (sortOrder != null) body.sort_order = sortOrder
  if (isEnabled != null) body.is_enabled = isEnabled
  if (groupId !== undefined) body.group_id = groupId ?? null  // null 也要发：用于移到未分组
  const res = await fetch(`${API}/api/agent/skills/${skillId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function deleteAgentSkill(skillId) {
  const res = await fetch(`${API}/api/agent/skills/${skillId}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ───────────────────── 技能分组 ─────────────────────

export async function fetchSkillGroups() {
  const res = await fetch(`${API}/api/agent/skill-groups`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function createSkillGroup({ name, parentId = null, sortOrder = 0 } = {}) {
  const res = await fetch(`${API}/api/agent/skill-groups`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, parent_id: parentId, sort_order: sortOrder }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function updateSkillGroup(groupId, { name, parentId, sortOrder } = {}) {
  const body = {}
  if (name != null) body.name = name
  if (parentId !== undefined) body.parent_id = parentId ?? null  // null = 移到根级
  if (sortOrder != null) body.sort_order = sortOrder
  const res = await fetch(`${API}/api/agent/skill-groups/${groupId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function deleteSkillGroup(groupId) {
  const res = await fetch(`${API}/api/agent/skill-groups/${groupId}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ───────────────────── 技能导入导出 ─────────────────────

export async function exportAgentSkills(skillId) {
  const qs = skillId ? `?skill_id=${encodeURIComponent(skillId)}` : ''
  const res = await fetch(`${API}/api/agent/skills/export${qs}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function exportAgentSkillsZip(skillId, code = '') {
  const qs = skillId ? `?skill_id=${encodeURIComponent(skillId)}` : ''
  const res = await fetch(`${API}/api/agent/skills/export-zip${qs}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '')
  const stem = skillId ? `skill-${code || 'skill'}` : 'skills'
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `knowsource-${stem}-${stamp}.zip`
  a.click()
  URL.revokeObjectURL(url)
  return { ok: true }
}

export async function importAgentSkills(skillsArray, { overwrite = false, groupId } = {}) {
  const res = await fetch(`${API}/api/agent/skills/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ skills: skillsArray, overwrite, group_id: groupId ?? null }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function importAgentSkillsFromUrl(url, { overwrite = false, groupId } = {}) {
  const res = await fetch(`${API}/api/agent/skills/import-url`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, overwrite, group_id: groupId ?? null }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function importAgentSkillsFromZip(file, { overwrite = false, groupId } = {}) {
  const form = new FormData()
  form.append('file', file)
  const params = new URLSearchParams()
  if (overwrite) params.set('overwrite', 'true')
  if (groupId) params.set('group_id', groupId)
  const qs = params.toString() ? `?${params}` : ''
  const res = await fetch(`${API}/api/agent/skills/import-zip${qs}`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function searchSkillMarket(q, page = 1) {
  const params = new URLSearchParams({ q, page: String(page), limit: '20' })
  const res = await fetch(`${API}/api/agent/skills/search-market?${params}`)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export function getFilePreviewUrl(fileId) {
  return `${API}/api/files/${fileId}/preview`
}

export async function fetchFileContent(fileId) {
  const res = await fetch(`${API}/api/files/${fileId}/preview`)
  if (!res.ok) throw new Error('Preview failed')
  return res.text()
}

export async function uploadChunk({ fileId, fileName, fileSize, kbId, chunkIndex, totalChunks, chunk }) {
  const form = new FormData()
  form.append('file_id', fileId)
  form.append('file_name', fileName)
  form.append('file_size', fileSize)
  form.append('kb_id', kbId)
  form.append('chunk_index', chunkIndex)
  form.append('total_chunks', totalChunks)
  form.append('chunk', chunk)
  const res = await fetch(`${API}/api/upload/chunk`, { method: 'POST', body: form })
  if (!res.ok) throw new Error('Upload failed')
  return res.json()
}

export async function fetchDirectories() {
  return (await (await fetch(`${API}/api/file-directories`)).json()) || []
}

export async function createDirectory({ name, parentId = null }) {
  const res = await fetch(`${API}/api/file-directories`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, parent_id: parentId }),
  })
  if (!res.ok) throw new Error('Create directory failed')
  return res.json()
}

export async function deleteDirectory(directoryId) {
  const res = await fetch(`${API}/api/file-directories/${directoryId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete directory failed')
  return res.json()
}

export async function updateDirectory(directoryId, { name, parentId = null }) {
  const res = await fetch(`${API}/api/file-directories/${directoryId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, parent_id: parentId }),
  })
  if (!res.ok) throw new Error('Update directory failed')
  return res.json()
}

export async function fetchAssets({ directoryId = '', q = '' } = {}) {
  const params = new URLSearchParams()
  if (directoryId) params.set('directory_id', directoryId)
  if (q) params.set('q', q)
  const res = await fetch(`${API}/api/assets?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch assets failed')
  return res.json()
}

export async function uploadAssetChunk({ assetId, fileName, fileSize, directoryId, chunkIndex, totalChunks, chunk }) {
  const form = new FormData()
  form.append('asset_id', assetId)
  form.append('file_name', fileName)
  form.append('file_size', fileSize)
  if (directoryId) form.append('directory_id', directoryId)
  form.append('chunk_index', chunkIndex)
  form.append('total_chunks', totalChunks)
  form.append('chunk', chunk)
  const res = await fetch(`${API}/api/assets/upload/chunk`, { method: 'POST', body: form })
  if (!res.ok) throw new Error('Asset upload failed')
  return res.json()
}

export async function deleteAsset(assetId) {
  const res = await fetch(`${API}/api/assets/${assetId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete asset failed')
  return res.json()
}

export async function updateAsset(assetId, { name, directoryId, summary, content } = {}) {
  const res = await fetch(`${API}/api/assets/${assetId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name,
      directory_id: directoryId,
      summary,
      content,
    }),
  })
  if (!res.ok) throw new Error('Update asset failed')
  return res.json()
}

export async function attachAssetsToKb(kbId, assetIds, { autoProcess = false, extractGraph = true } = {}) {
  const res = await fetch(`${API}/api/kb/${kbId}/assets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      asset_ids: assetIds,
      auto_process: autoProcess,
      extract_graph: extractGraph,
    }),
  })
  if (!res.ok) throw new Error('Attach assets failed')
  return res.json()
}

export async function fetchConfig() {
  const res = await fetch(`${API}/api/config`)
  if (!res.ok) throw new Error('Fetch config failed')
  return res.json()
}

export async function createCrawlJob({
  keyword,
  directoryId = null,
  maxPages = null,
  autoAttachKbId = null,
  autoProcess = false,
  extractGraph = true,
  analysisDepth = 'medium',
}) {
  const res = await fetch(`${API}/api/crawl-jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      keyword,
      directory_id: directoryId,
      max_pages: maxPages,
      auto_attach_kb_id: autoAttachKbId,
      auto_process: autoProcess,
      extract_graph: extractGraph,
      analysis_depth: analysisDepth,
    }),
  })
  if (!res.ok) throw new Error('Create crawl job failed')
  return res.json()
}

export async function getCrawlJob(jobId) {
  const res = await fetch(`${API}/api/crawl-jobs/${jobId}`)
  if (!res.ok) throw new Error('Fetch crawl job failed')
  return res.json()
}

export async function getLatestCrawlJob() {
  try {
    const res = await fetch(`${API}/api/crawl-jobs/latest`)
    if (res.status === 404) return null
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

export function getAssetPreviewUrl(assetId) {
  return `${API}/api/assets/${assetId}/preview`
}

export async function fetchAssetContent(assetId) {
  const res = await fetch(`${API}/api/assets/${assetId}/preview`)
  if (!res.ok) throw new Error('Preview failed')
  return res.text()
}

// ===== 本体管理 API =====

// 模块一：本体类别
export async function fetchOntologyCategories({ q = '' } = {}) {
  const params = new URLSearchParams()
  if (q) params.set('q', q)
  const res = await fetch(`${API}/api/ontology-categories?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch ontology categories failed')
  return res.json()
}

export async function getOntologyCategoryDetail(categoryId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}`)
  if (!res.ok) throw new Error('Get ontology category detail failed')
  return res.json()
}

export async function createOntologyCategory({ name, description }) {
  const res = await fetch(`${API}/api/ontology-categories`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
  if (!res.ok) throw new Error('Create ontology category failed')
  return res.json()
}

export async function updateOntologyCategory(categoryId, { name, description }) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
  if (!res.ok) throw new Error('Update ontology category failed')
  return res.json()
}

export async function deleteOntologyCategory(categoryId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete ontology category failed')
  return res.json()
}

// 模块二：本体 + 属性
export async function fetchOntologies(categoryId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies`)
  if (!res.ok) throw new Error('Fetch ontologies failed')
  return res.json()
}

export async function createOntology(categoryId, { name, description = '', color = null, sort_order = 0 }) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description, color, sort_order }),
  })
  if (!res.ok) throw new Error('Create ontology failed')
  return res.json()
}

export async function updateOntology(categoryId, ontologyId, data) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Update ontology failed')
  return res.json()
}

export async function deleteOntology(categoryId, ontologyId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete ontology failed')
  return res.json()
}

export async function getOntologyAttributes(categoryId, ontologyId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/attributes`)
  if (!res.ok) throw new Error('Get ontology attributes failed')
  return res.json()
}

export async function addOntologyAttribute(categoryId, ontologyId, data) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/attributes`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Add ontology attribute failed')
  return res.json()
}

export async function updateOntologyAttribute(categoryId, ontologyId, attrId, data) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/attributes/${attrId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Update ontology attribute failed')
  return res.json()
}

export async function deleteOntologyAttribute(categoryId, ontologyId, attrId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/attributes/${attrId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete ontology attribute failed')
  return res.json()
}

export async function replaceOntologyAttributes(categoryId, ontologyId, { attributes }) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/attributes`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attributes }),
  })
  if (!res.ok) throw new Error('Replace ontology attributes failed')
  return res.json()
}

// 模块三：关系定义（关系字典）
export async function fetchRelations(categoryId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/relations`)
  if (!res.ok) throw new Error('Fetch relations failed')
  return res.json()
}

export async function createRelation(categoryId, { name, code, description = '' }) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/relations`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, code, description }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Create relation failed')
  }
  return res.json()
}

export async function updateRelation(categoryId, relationId, data) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/relations/${relationId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Update relation failed')
  return res.json()
}

export async function deleteRelation(categoryId, relationId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/relations/${relationId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete relation failed')
  return res.json()
}

// 模块四：三元组约束
export async function fetchConstraints(categoryId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/constraints`)
  if (!res.ok) throw new Error('Fetch constraints failed')
  return res.json()
}

export async function createConstraint(categoryId, { source_ontology_id, relation_id, target_ontology_id, description = '' }) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/constraints`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_ontology_id, relation_id, target_ontology_id, description }),
  })
  if (!res.ok) throw new Error('Create constraint failed')
  return res.json()
}

export async function updateConstraint(categoryId, constraintId, data) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/constraints/${constraintId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Update constraint failed')
  }
  return res.json()
}

export async function deleteConstraint(categoryId, constraintId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/constraints/${constraintId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete constraint failed')
  return res.json()
}

// 模块五：属性模板
export async function fetchAttributeTemplates({ q = '' } = {}) {
  const params = new URLSearchParams()
  if (q) params.set('q', q)
  const res = await fetch(`${API}/api/attribute-templates?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch attribute templates failed')
  return res.json()
}

export async function getAttributeTemplate(templateId) {
  const res = await fetch(`${API}/api/attribute-templates/${templateId}`)
  if (!res.ok) throw new Error('Get attribute template failed')
  return res.json()
}

export async function createAttributeTemplate({ name, description = '' }) {
  const res = await fetch(`${API}/api/attribute-templates`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
  if (!res.ok) throw new Error('Create attribute template failed')
  return res.json()
}

export async function updateAttributeTemplate(templateId, { name, description }) {
  const res = await fetch(`${API}/api/attribute-templates/${templateId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
  if (!res.ok) throw new Error('Update attribute template failed')
  return res.json()
}

export async function deleteAttributeTemplate(templateId) {
  const res = await fetch(`${API}/api/attribute-templates/${templateId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete attribute template failed')
  return res.json()
}

export async function addTemplateAttribute(templateId, data) {
  const res = await fetch(`${API}/api/attribute-templates/${templateId}/attributes`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Add template attribute failed')
  return res.json()
}

export async function updateTemplateAttribute(templateId, attrId, data) {
  const res = await fetch(`${API}/api/attribute-templates/${templateId}/attributes/${attrId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Update template attribute failed')
  return res.json()
}

export async function deleteTemplateAttribute(templateId, attrId) {
  const res = await fetch(`${API}/api/attribute-templates/${templateId}/attributes/${attrId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete template attribute failed')
  return res.json()
}

export async function replaceTemplateAttributes(templateId, { attributes }) {
  const res = await fetch(`${API}/api/attribute-templates/${templateId}/attributes`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attributes }),
  })
  if (!res.ok) throw new Error('Replace template attributes failed')
  return res.json()
}

// 本体引用属性模板（多对多）
export async function getOntologyTemplates(categoryId, ontologyId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/templates`)
  if (!res.ok) throw new Error('Get ontology templates failed')
  return res.json()
}

export async function setOntologyTemplates(categoryId, ontologyId, { template_ids }) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/templates`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template_ids }),
  })
  if (!res.ok) throw new Error('Set ontology templates failed')
  return res.json()
}

export async function getMergedAttributes(categoryId, ontologyId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/merged-attributes`)
  if (!res.ok) throw new Error('Get merged attributes failed')
  return res.json()
}

// 知识库绑定本体类别
export async function getKbOntology(kbId) {
  const res = await fetch(`${API}/api/kb/${kbId}/ontology`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error('Get kb ontology failed')
  return res.json()
}

export async function setKbOntology(kbId, categoryId) {
  const res = await fetch(`${API}/api/kb/${kbId}/ontology`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category_id: categoryId }),
  })
  if (!res.ok) throw new Error('Set kb ontology failed')
  return res.json()
}

export async function removeKbOntology(kbId) {
  const res = await fetch(`${API}/api/kb/${kbId}/ontology`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Remove kb ontology failed')
  return res.json()
}

// 模块五b：本体服务（动作）
export async function fetchOntologyServices(categoryId, ontologyId) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/services`)
  if (!res.ok) throw new Error('Fetch ontology services failed')
  return res.json()
}

export async function createOntologyService(categoryId, ontologyId, data) {
  const res = await fetch(`${API}/api/ontology-categories/${categoryId}/ontologies/${ontologyId}/services`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || 'Create service failed')
  }
  return res.json()
}

export async function updateOntologyService(serviceId, data) {
  const res = await fetch(`${API}/api/ontology-services/${serviceId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || 'Update service failed')
  }
  return res.json()
}

export async function deleteOntologyService(serviceId) {
  const res = await fetch(`${API}/api/ontology-services/${serviceId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete service failed')
  return res.json()
}

export async function testOntologyService(serviceId, { params, mock_entity } = {}) {
  const res = await fetch(`${API}/api/ontology-services/${serviceId}/test`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ params, mock_entity }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || 'Test service failed')
  }
  return res.json()
}

// AI 辅助编写动作代码（SSE 流式）：onDelta 收增量文本，结束后返回 {code_text, params, explanation}
export async function aiAssistServiceCode({ prompt, name, code, description, owner_name, current_code, selected_code, history, onDelta, signal } = {}) {
  const res = await fetch(`${API}/api/ontology-services/ai-assist`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, name, code, description, owner_name, current_code, selected_code, history }),
    signal,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `AI assist failed (HTTP ${res.status})`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let result = null
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const line = raw.replace(/^data:\s*/, '').trim()
      if (!line) continue
      let ev
      try { ev = JSON.parse(line) } catch { continue }
      if (ev.type === 'delta') {
        onDelta?.(ev.content)
      } else if (ev.type === 'done') {
        result = ev.data
      } else if (ev.type === 'error') {
        throw new Error(ev.detail || 'AI 生成失败')
      }
    }
  }
  return result
}

export async function fetchEntityServices(entityId) {
  const res = await fetch(`${API}/api/entities/${entityId}/services`)
  if (!res.ok) throw new Error('Fetch entity services failed')
  return res.json()
}

export async function createEntityService(entityId, data) {
  const res = await fetch(`${API}/api/entities/${entityId}/services`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || 'Create service failed')
  }
  return res.json()
}

export async function invokeEntityService(entityId, serviceId, { params } = {}) {
  const res = await fetch(`${API}/api/entities/${entityId}/services/${serviceId}/invoke`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ params }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || 'Invoke service failed')
  }
  return res.json()
}

export async function copyServiceToEntity(entityId, serviceId) {
  const res = await fetch(`${API}/api/entities/${entityId}/services/${serviceId}/copy`, { method: 'POST' })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || 'Copy service failed')
  }
  return res.json()
}

// 模块六：实体实例管理
export async function fetchEntities({ kb_id = '', ontology_id = '', q = '', page = 1, page_size = 20 } = {}) {
  const params = new URLSearchParams()
  if (kb_id) params.set('kb_id', kb_id)
  if (ontology_id) params.set('ontology_id', ontology_id)
  if (q) params.set('q', q)
  params.set('page', String(page))
  params.set('page_size', String(page_size))
  const res = await fetch(`${API}/api/entities?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch entities failed')
  return res.json()
}

export async function getEntityDetail(entityId) {
  const res = await fetch(`${API}/api/entities/${entityId}`)
  if (!res.ok) throw new Error('Get entity detail failed')
  return res.json()
}

export async function updateEntity(entityId, { name, description, properties }) {
  const res = await fetch(`${API}/api/entities/${entityId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description, properties }),
  })
  if (!res.ok) throw new Error('Update entity failed')
  return res.json()
}

export async function deleteEntity(entityId) {
  const res = await fetch(`${API}/api/entities/${entityId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete entity failed')
  return res.json()
}

// 模块七：关系实例管理
export async function fetchRelationInstances({ kb_id = '', relation_type = '', q = '', page = 1, page_size = 20 } = {}) {
  const params = new URLSearchParams()
  if (kb_id) params.set('kb_id', kb_id)
  if (relation_type) params.set('relation_type', relation_type)
  if (q) params.set('q', q)
  params.set('page', String(page))
  params.set('page_size', String(page_size))
  const res = await fetch(`${API}/api/relations?${params.toString()}`)
  if (!res.ok) throw new Error('Fetch relation instances failed')
  return res.json()
}

export async function deleteRelationInstance(relationId) {
  const res = await fetch(`${API}/api/relations/${relationId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete relation instance failed')
  return res.json()
}

// ===== 模块八：本体建议（动态生成 + 审核）=====
export async function fetchOntologySuggestions({ kbId, status } = {}) {
  const params = new URLSearchParams()
  if (kbId) params.set('kb_id', kbId)
  if (status) params.set('status', status)
  const qs = params.toString() ? `?${params.toString()}` : ''
  const res = await fetch(`${API}/api/ontology-suggestions${qs}`)
  if (!res.ok) throw new Error('Fetch ontology suggestions failed')
  return res.json()
}

export async function getOntologySuggestion(suggestionId) {
  const res = await fetch(`${API}/api/ontology-suggestions/${suggestionId}`)
  if (!res.ok) throw new Error('Get ontology suggestion failed')
  return res.json()
}

export async function updateOntologySuggestion(suggestionId, data) {
  const res = await fetch(`${API}/api/ontology-suggestions/${suggestionId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Update ontology suggestion failed')
  return res.json()
}

export async function approveOntologySuggestion(suggestionId, { reviewer } = {}) {
  const res = await fetch(`${API}/api/ontology-suggestions/${suggestionId}/approve`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reviewer }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Approve suggestion failed')
  }
  return res.json()
}

export async function rejectOntologySuggestion(suggestionId) {
  const res = await fetch(`${API}/api/ontology-suggestions/${suggestionId}/reject`, { method: 'POST' })
  if (!res.ok) throw new Error('Reject suggestion failed')
  return res.json()
}

export async function deleteOntologySuggestion(suggestionId) {
  const res = await fetch(`${API}/api/ontology-suggestions/${suggestionId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Delete suggestion failed')
  return res.json()
}

// ===== 图谱清洗（合并 / 精简实体关系）=====
export async function fetchCleanupSuggestions(kbId) {
  const res = await fetch(`${API}/api/graph-cleanup/suggestions?kb_id=${encodeURIComponent(kbId)}`)
  if (!res.ok) throw new Error('Fetch cleanup suggestions failed')
  return res.json()
}

export async function applyCleanup({ kbId, merges = [], deleteEntityIds = [], deleteRelationIds = [] } = {}) {
  const res = await fetch(`${API}/api/graph-cleanup/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      kb_id: kbId,
      merges,
      delete_entity_ids: deleteEntityIds,
      delete_relation_ids: deleteRelationIds,
    }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Apply cleanup failed')
  }
  return res.json()
}

export async function mergeEntities({ canonicalId, mergedIds, kbId } = {}) {
  const res = await fetch(`${API}/api/entities/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ canonical_id: canonicalId, merged_ids: mergedIds, kb_id: kbId }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Merge entities failed')
  }
  return res.json()
}

// ===== 大模型（LLM）配置 =====
export async function testLLMConfig({ provider, apiKey, baseUrl, model, maxTokens, temperature }) {
  const res = await fetch(`${API}/api/config/llm/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      provider,
      api_key: apiKey,
      base_url: baseUrl,
      model,
      max_tokens: maxTokens,
      temperature,
    }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Test connection failed')
  }
  return res.json()
}

// 配置方案（多套 LLM 配置，可一键切换）
export async function fetchLLMPlans() {
  const res = await fetch(`${API}/api/config/llm/plans`)
  if (!res.ok) throw new Error('Fetch LLM plans failed')
  return res.json()
}

export async function createLLMPlan({ name, provider, apiKey, baseUrl, model, maxTokens, temperature }) {
  const res = await fetch(`${API}/api/config/llm/plans`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name, provider, api_key: apiKey, base_url: baseUrl,
      model, max_tokens: maxTokens, temperature,
    }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Create plan failed')
  }
  return res.json()
}

export async function updateLLMPlan(planId, { name, provider, apiKey, baseUrl, model, maxTokens, temperature }) {
  const res = await fetch(`${API}/api/config/llm/plans/${planId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name, provider, api_key: apiKey, base_url: baseUrl,
      model, max_tokens: maxTokens, temperature,
    }),
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Update plan failed')
  }
  return res.json()
}

export async function deleteLLMPlan(planId) {
  const res = await fetch(`${API}/api/config/llm/plans/${planId}`, { method: 'DELETE' })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Delete plan failed')
  }
  return res.json()
}

export async function applyLLMPlan(planId) {
  const res = await fetch(`${API}/api/config/llm/plans/${planId}/apply`, { method: 'POST' })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.detail || 'Apply plan failed')
  }
  return res.json()
}
