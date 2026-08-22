<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { fetchPipelineStatus } from '../api'

// 蛇形流水线：上排 00→01→02（左→右），下排 03→04→05→06（右→左）
const flowSteps = [
  {
    key: 'ontology',
    number: '00',
    to: '/ontology/ontologies',
    label: '本体管理',
    role: '知识定义',
    line: '定义本体类别、属性、关系和三元组约束，为知识抽取提供结构化模板。',
    action: '进入本体',
  },
  {
    key: 'files',
    number: '01',
    to: '/files',
    label: '文件/数据采集',
    role: '资料入口',
    line: '上传文件、网络采集、整理目录，先把可用资料沉淀到系统。',
    action: '进入文件',
  },
  {
    key: 'kb',
    number: '02',
    to: '/kb',
    label: '知识库',
    role: '知识中枢',
    line: '把资料组织成知识库，完成分片、索引和可选的关系抽取。',
    action: '进入知识库',
  },
  {
    key: 'graph',
    number: '03',
    to: '/graph',
    label: '图谱',
    role: '关系支线',
    line: '抽取实体与关系构建知识图谱，沉淀结构化事实。',
    action: '查看图谱',
  },
  {
    key: 'vectors',
    number: '04',
    to: '/vectors',
    label: '向量',
    role: '召回支线',
    line: '分片向量化入索引，支撑语义相似召回。',
    action: '查看向量',
  },
  {
    key: 'rag',
    number: '05',
    to: '/query',
    label: '知识库检索',
    role: '主业务出口',
    line: '选择知识库提问，基于向量召回生成可追溯的回答。',
    action: '开始检索',
  },
  {
    key: 'agent',
    number: '06',
    to: '/agent',
    label: '智能体',
    role: '增强问答',
    line: '融合知识库检索、图谱事实与技能，回答更准、推理过程可追溯。',
    action: '进入智能体',
  },
]

// 流水线连线（viewBox 0 0 1180 520，与卡片栅格对齐）
const flowEdges = [
  { id: 'e1', from: 'ontology', to: 'files', d: 'M378 119 L400 119', dur: '1.4s', half: '0.7s' },
  { id: 'e2', from: 'files', to: 'kb', d: 'M779 119 L801 119', dur: '1.4s', half: '0.7s' },
  { id: 'e3', from: 'kb', to: 'graph', d: 'M991 240 C991 258 1041 244 1041 261', dur: '1.8s', half: '0.9s' },
  { id: 'e4', from: 'graph', to: 'vectors', d: 'M902 381 L880 381', dur: '1.4s', half: '0.7s' },
  { id: 'e5', from: 'vectors', to: 'rag', d: 'M601 381 L579 381', dur: '1.4s', half: '0.7s' },
  { id: 'e6', from: 'rag', to: 'agent', d: 'M300 381 L278 381', dur: '1.4s', half: '0.7s' },
]

// ---------- 流水线实时状态（数字孪生大屏） ----------
const live = reactive({})
const pipelineActive = ref(0)

const STAGE_GROUPS = {
  kb: ['preparing', 'parsing', 'chunking'],
  vectors: ['vectorizing'],
  graph: ['extracting', 'saving'],
}
const STAGE_LABELS = {
  kb: '分片处理',
  vectors: '向量化',
  graph: '图谱构建',
}

function applyStatus(s) {
  const stages = s?.stages || {}
  for (const [key, names] of Object.entries(STAGE_GROUPS)) {
    let count = 0
    let weighted = 0
    for (const n of names) {
      const g = stages[n]
      if (!g) continue
      count += g.count || 0
      weighted += (g.progress || 0) * (g.count || 0)
    }
    live[key] = count
      ? { label: STAGE_LABELS[key], count, progress: Math.round(weighted / count) }
      : null
  }
  live.files = s?.crawling
    ? { label: '网络采集', count: 1, progress: s.crawl_progress || 0 }
    : null
  pipelineActive.value = (s?.processing_files || 0) + (s?.crawling ? 1 : 0)
}

function isEdgeActive(e) {
  return !!live[e.to]
}

let pollTimer = null
async function pollStatus() {
  try { applyStatus(await fetchPipelineStatus()) } catch {}
}
function onVisibility() {
  if (!document.hidden) pollStatus()
}

onMounted(() => {
  pollStatus()
  pollTimer = setInterval(pollStatus, 5000)
  document.addEventListener('visibilitychange', onVisibility)
})
onBeforeUnmount(() => {
  clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<template>
  <div class="home-view">
    <header class="home-head">
      <div class="home-title-block">
        <h1>KnowSource 知源知识中枢</h1>
        <p>从文件和数据采集开始，进入知识库加工，经图谱与向量索引，最终汇入知识库检索与智能体问答。</p>
      </div>
      <div v-if="pipelineActive" class="pipeline-pill" role="status">
        <i aria-hidden="true"></i>
        流水线运行中 · {{ pipelineActive }} 个任务
      </div>
    </header>

    <section class="flow-band" aria-label="系统菜单流程">
      <div class="flow-map">
        <svg class="flow-links" viewBox="0 0 1180 520" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <marker id="flow-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
              <path d="M1 1 9 5 1 9Z" />
            </marker>
            <marker id="flow-arrow-on" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
              <path d="M1 1 9 5 1 9Z" />
            </marker>
          </defs>
          <g
            v-for="e in flowEdges"
            :key="e.id"
            :class="['edge', { active: isEdgeActive(e) }]"
          >
            <path class="edge-base" :d="e.d" marker-end="url(#flow-arrow)" />
            <path class="edge-flow" :d="e.d" />
            <circle class="edge-pulse" r="3.2">
              <animateMotion :dur="e.dur" repeatCount="indefinite" :path="e.d" begin="0s" />
            </circle>
            <circle class="edge-pulse lag" r="2.4">
              <animateMotion :dur="e.dur" repeatCount="indefinite" :path="e.d" :begin="e.half" />
            </circle>
          </g>
        </svg>

        <RouterLink
          v-for="step in flowSteps"
          :key="step.key"
          :to="step.to"
          :class="['flow-card', `step-${step.key}`, { running: live[step.key] }]"
          :aria-label="`${step.label}：${step.line}`"
        >
          <div class="art-panel">
            <svg class="menu-art" viewBox="0 0 360 230" role="img" :aria-label="`${step.label}插画`">
              <g class="art-grid" aria-hidden="true">
                <path d="M25 168 142 104 325 182" />
                <path d="M66 194 183 130 324 194" />
                <path d="M80 84 292 201" />
                <path d="M150 47 338 151" />
                <path d="M35 151 197 61" />
                <path d="M95 204 308 84" />
              </g>

              <g v-if="step.key === 'ontology'" class="art-scene ontology-art">
                <ellipse class="art-shadow" cx="180" cy="198" rx="120" ry="17" />
                <path class="iso-base" d="M58 160 166 101 311 163 203 219Z" />
                <path class="ontology-hub" d="M140 80 L180 60 L220 80 L220 130 L180 150 L140 130 Z" />
                <path class="ontology-core" d="M155 88 L180 74 L205 88 L205 118 L180 132 L155 118 Z" />
                <circle class="ontology-node node-a" cx="100" cy="90" r="18" />
                <circle class="ontology-node node-b" cx="260" cy="90" r="18" />
                <circle class="ontology-node node-c" cx="100" cy="150" r="18" />
                <circle class="ontology-node node-d" cx="260" cy="150" r="18" />
                <path class="ontology-link" d="M118 90 L155 95" />
                <path class="ontology-link" d="M205 95 L242 90" />
                <path class="ontology-link" d="M118 150 L155 125" />
                <path class="ontology-link" d="M205 125 L242 150" />
                <path class="attr-bar" d="M85 70 L115 70" />
                <path class="attr-bar" d="M85 80 L108 80" />
                <path class="attr-bar" d="M245 70 L275 70" />
                <path class="attr-bar" d="M245 80 L268 80" />
                <circle class="token yellow" cx="180" cy="100" r="8" />
              </g>

              <g v-else-if="step.key === 'files'" class="art-scene files-art">
                <ellipse class="art-shadow" cx="175" cy="196" rx="118" ry="18" />
                <path class="iso-base" d="M55 154 150 103 302 165 204 217Z" />
                <path class="cyan-plate" d="M83 150 154 113 264 160 191 198Z" />
                <path class="folder-back" d="M111 104 144 88 174 100 195 89 251 113 251 169 165 205 111 180Z" />
                <path class="folder-face" d="M100 124 162 97 256 136 256 172 165 211 100 173Z" />
                <path class="paper paper-one" d="M145 82 185 63 236 84 195 106Z" />
                <path class="paper paper-two" d="M122 96 165 76 211 95 168 118Z" />
                <path class="paper-line" d="M154 84 186 99" />
                <path class="paper-line" d="M172 76 206 91" />
                <path class="tube-line" d="M61 135 C36 128 35 104 61 97 85 90 85 68 63 62" />
                <circle class="token yellow" cx="60" cy="136" r="9" />
                <circle class="token green" cx="63" cy="61" r="13" />
                <path class="upload-arrow" d="M280 94 280 58" />
                <path class="upload-arrow" d="M263 72 280 56 297 72" />
                <path class="tiny-node" d="M287 114 314 129" />
                <circle class="token green small" cx="315" cy="130" r="7" />
              </g>

              <g v-else-if="step.key === 'kb'" class="art-scene kb-art">
                <ellipse class="art-shadow" cx="184" cy="198" rx="123" ry="17" />
                <path class="iso-base" d="M57 158 165 99 310 162 201 219Z" />
                <path class="cylinder-body" d="M96 87 C96 67 134 51 183 51 C233 51 272 67 272 87 L272 164 C272 187 233 203 183 203 C134 203 96 187 96 164Z" />
                <ellipse class="cylinder-top" cx="184" cy="87" rx="88" ry="38" />
                <ellipse class="cylinder-liquid" cx="184" cy="94" rx="65" ry="24" />
                <path class="cylinder-rim" d="M96 87 C96 109 134 124 183 124 C233 124 272 109 272 87" />
                <path class="chunk chunk-one" d="M143 133 180 115 222 132 184 151Z" />
                <path class="chunk chunk-two" d="M126 155 156 140 193 154 160 171Z" />
                <path class="chunk chunk-three" d="M191 164 223 148 250 160 217 177Z" />
                <path class="gear-ring" d="M83 143 A25 25 0 1 0 84 143 M75 143 A16 16 0 1 1 76 143" />
                <path class="gear-tooth" d="M58 122 67 116 75 124 69 132Z" />
                <path class="gear-tooth" d="M99 122 111 127 106 139 94 136Z" />
                <path class="gear-tooth" d="M63 161 74 157 81 168 71 176Z" />
                <path class="gear-tooth" d="M99 161 107 171 96 180 88 169Z" />
                <path class="card-feed" d="M41 82 89 58 130 76 82 102Z" />
                <path class="card-feed second" d="M53 102 100 79 136 95 88 119Z" />
                <path class="tube-line" d="M69 121 C87 125 95 132 100 146" />
                <circle class="token yellow" cx="59" cy="119" r="8" />
                <circle class="token green" cx="289" cy="58" r="14" />
              </g>

              <g v-else-if="step.key === 'rag'" class="art-scene query-art">
                <ellipse class="art-shadow" cx="178" cy="197" rx="116" ry="18" />
                <path class="iso-base" d="M61 158 163 102 305 164 203 219Z" />
                <path class="console-top" d="M87 145 172 101 270 142 184 188Z" />
                <path class="console-face" d="M87 145 184 188 184 211 87 168Z" />
                <path class="console-side" d="M184 188 270 142 270 165 184 211Z" />
                <path class="screen" d="M132 143 178 119 228 140 182 164Z" />
                <path class="screen-line" d="M160 141 190 128" />
                <path class="screen-line" d="M169 151 208 134" />
                <path class="bubble" d="M178 47 C218 27 278 36 298 68 C317 98 291 128 247 130 L226 156 221 130 C182 126 155 102 160 76 C162 65 168 55 178 47Z" />
                <path class="bubble-line" d="M202 75 263 75" />
                <path class="bubble-line short" d="M202 96 244 96" />
                <path class="question-hook" d="M84 90 C63 81 63 61 82 52 103 43 125 56 119 74 115 86 100 87 100 100" />
                <circle class="question-dot" cx="100" cy="118" r="6" />
                <path class="tube-line" d="M77 139 C52 136 49 111 72 101" />
                <circle class="token yellow" cx="76" cy="139" r="8" />
                <circle class="token green" cx="300" cy="144" r="11" />
              </g>

              <g v-else-if="step.key === 'agent'" class="art-scene agent-art">
                <ellipse class="art-shadow" cx="180" cy="198" rx="118" ry="18" />
                <path class="iso-base" d="M57 160 164 101 306 162 201 219Z" />
                <path class="robot-head" d="M138 88 L180 66 L222 88 L222 138 L180 160 L138 138 Z" />
                <path class="robot-face" d="M150 94 L180 78 L210 94 L210 130 L180 148 L150 130 Z" />
                <circle class="robot-eye" cx="166" cy="110" r="7" />
                <circle class="robot-eye" cx="194" cy="110" r="7" />
                <path class="robot-mouth" d="M167 129 L193 129" />
                <path class="robot-antenna" d="M180 66 L180 50" />
                <circle class="token yellow" cx="180" cy="44" r="8" />
                <path class="gear-ring" d="M75 108 A22 22 0 1 0 76 108 M68 108 A14 14 0 1 1 69 108" />
                <path class="gear-tooth" d="M53 89 61 84 68 91 63 99Z" />
                <path class="gear-tooth" d="M89 92 96 101 87 108 81 101Z" />
                <path class="gear-tooth" d="M58 130 66 126 73 136 65 141Z" />
                <path class="tube-line" d="M97 118 C115 122 125 130 138 128" />
                <path class="bubble" d="M240 46 C272 32 316 40 330 64 C342 86 322 110 288 112 L272 132 268 110 C244 106 230 88 234 66 C236 58 236 50 240 46Z" />
                <path class="bolt" d="M286 60 L270 86 L282 86 L272 108 L294 80 L282 80 Z" />
                <path class="tiny-node" d="M247 146 274 158" />
                <circle class="token green small" cx="282" cy="162" r="7" />
              </g>

              <g v-else-if="step.key === 'vectors'" class="art-scene vector-art">
                <ellipse class="art-shadow" cx="181" cy="198" rx="121" ry="17" />
                <path class="iso-base" d="M58 160 164 101 309 163 201 219Z" />
                <path class="cylinder-body slim" d="M106 91 C106 72 140 57 183 57 C227 57 261 72 261 91 L261 162 C261 183 227 198 183 198 C140 198 106 183 106 162Z" />
                <ellipse class="cylinder-top" cx="184" cy="91" rx="78" ry="34" />
                <ellipse class="cylinder-liquid" cx="184" cy="97" rx="58" ry="22" />
                <path class="vector-link" d="M139 141 171 121 211 136 238 113" />
                <path class="vector-link" d="M147 164 177 151 210 165 238 143" />
                <path class="vector-link" d="M171 121 177 151 211 136 210 165" />
                <circle class="vector-node big" cx="139" cy="141" r="12" />
                <circle class="vector-node" cx="171" cy="121" r="8" />
                <circle class="vector-node big" cx="211" cy="136" r="11" />
                <circle class="vector-node" cx="238" cy="113" r="8" />
                <circle class="vector-node" cx="177" cy="151" r="8" />
                <circle class="vector-node big" cx="210" cy="165" r="12" />
                <circle class="vector-node" cx="238" cy="143" r="8" />
                <path class="magnifier-disc" d="M151 60 C176 48 214 51 236 66 C258 81 255 103 231 115 C206 128 168 124 146 109 C124 94 126 72 151 60Z" />
                <path class="magnifier" d="M181 82 C190 78 203 80 209 86 C215 92 212 101 203 105 C194 109 181 107 175 101 C169 95 172 86 181 82Z" />
                <path class="magnifier-handle" d="M179 101 153 116" />
                <circle class="token green" cx="73" cy="102" r="13" />
                <circle class="token yellow" cx="291" cy="69" r="13" />
              </g>

              <g v-else class="art-scene graph-art">
                <ellipse class="art-shadow" cx="180" cy="198" rx="118" ry="18" />
                <path class="iso-base" d="M57 160 164 101 306 162 201 219Z" />
                <path class="cloud-wall" d="M111 92 C119 64 150 49 183 60 C200 42 233 41 255 59 C282 58 304 77 306 105 C329 122 321 156 293 168 L141 194 C103 194 73 172 73 142 C52 123 65 92 111 92Z" />
                <path class="cloud-inner" d="M120 101 C130 78 157 70 184 78 C202 62 229 62 249 77 C275 78 293 94 294 116 C309 129 303 151 281 159 L145 181 C119 179 99 163 101 140 C83 127 91 105 120 101Z" />
                <path class="graph-link" d="M143 129 181 105 222 119 259 96" />
                <path class="graph-link" d="M181 105 200 151 259 96 252 153" />
                <path class="graph-link" d="M143 129 200 151 252 153" />
                <circle class="graph-node white" cx="143" cy="129" r="13" />
                <circle class="graph-node green" cx="181" cy="105" r="16" />
                <circle class="graph-node green" cx="222" cy="119" r="12" />
                <circle class="graph-node white" cx="259" cy="96" r="14" />
                <circle class="graph-node blue" cx="200" cy="151" r="12" />
                <circle class="graph-node white" cx="252" cy="153" r="15" />
                <path class="scope-stand" d="M88 139 88 184" />
                <ellipse class="scope-base" cx="88" cy="190" rx="24" ry="9" />
                <circle class="scope-eye" cx="88" cy="132" r="15" />
                <path class="tube-line" d="M103 139 C126 145 141 150 164 144" />
                <circle class="token yellow" cx="163" cy="144" r="7" />
              </g>
            </svg>
          </div>

          <div class="flow-copy">
            <div class="step-meta">
              <span class="step-number">{{ step.number }}</span>
              <span v-if="step.key === 'ontology'" class="define-chip">定义层</span>
              <span v-else-if="step.key === 'files'" class="start-chip">START</span>
              <span v-else-if="step.key === 'agent'" class="agent-chip">AGENT</span>
              <span v-else-if="step.key === 'vectors' || step.key === 'graph'" class="branch-chip">知识库支线</span>
            </div>
            <span class="flow-role">{{ step.role }}</span>
            <h2>{{ step.label }}</h2>
            <p>{{ step.line }}</p>
            <span class="flow-action">
              {{ step.action }}
              <svg viewBox="0 0 18 18" aria-hidden="true">
                <path d="M6.5 3.75 11.75 9 6.5 14.25" />
              </svg>
            </span>

            <!-- 任务执行实时进度条（贴卡片底部，全宽） -->
            <div v-if="live[step.key]" class="live-bar" role="status">
              <span class="live-label">
                <i aria-hidden="true"></i>
                {{ live[step.key].label }}<template v-if="live[step.key].count > 1"> ×{{ live[step.key].count }}</template>
              </span>
              <span class="live-pct">{{ live[step.key].progress }}%</span>
              <div class="live-track">
                <div class="live-fill" :style="{ width: live[step.key].progress + '%' }"></div>
              </div>
            </div>
          </div>
        </RouterLink>
      </div>
    </section>

    <section class="workflow-text" aria-label="流程说明">
      <div class="workflow-item">
        <span>01</span>
        <strong>资料先进入文件/数据采集</strong>
        <p>原始文件和采集内容先统一归档，后续知识库从这里选择可处理资料。</p>
      </div>
      <div class="workflow-item">
        <span>02</span>
        <strong>知识库承担加工和组织</strong>
        <p>知识库把资料变成可检索的分片，同时派生向量索引和实体关系图谱。</p>
      </div>
      <div class="workflow-item">
        <span>03</span>
        <strong>检索与智能体是面向使用者的主出口</strong>
        <p>知识库检索基于向量召回生成可追溯回答；智能体在其上融合图谱事实与技能。</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.home-view {
  color: var(--c-fg);
}

.home-head {
  max-width: 1180px;
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

/* 头部流水线运行状态 */
.pipeline-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
  padding: 7px 14px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
  color: #2f6b1c;
  background: rgba(134, 201, 87, 0.16);
  box-shadow: inset 0 0 0 1px rgba(74, 128, 39, 0.24);
}

.pipeline-pill i {
  position: relative;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #5cab2e;
  box-shadow: 0 0 8px rgba(92, 171, 46, 0.9);
}

.pipeline-pill i::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: #5cab2e;
  animation: live-ping 1.3s cubic-bezier(0, 0, 0.2, 1) infinite;
}

:root[data-theme='dark'] .pipeline-pill {
  color: #b7ec86;
  background: rgba(134, 201, 87, 0.14);
  box-shadow: inset 0 0 0 1px rgba(134, 201, 87, 0.28);
}

.home-title-block {
  max-width: 820px;
}

.home-title-block h1 {
  margin: 0;
  font-size: 32px;
  line-height: 1.18;
  letter-spacing: 0;
}

.home-title-block p {
  margin-top: 10px;
  color: var(--c-secondary);
  font-size: 15px;
  line-height: 1.8;
}

.flow-band {
  position: relative;
  margin: 24px -32px 0;
  padding: 30px 32px;
  min-height: clamp(650px, calc(100dvh - 230px), 1040px);
  display: flex;
  align-items: center;
  overflow: hidden;
  border-top: 1px solid var(--c-border);
  border-bottom: 1px solid var(--c-border);
  background: var(--c-panel);
}

.flow-map {
  position: relative;
  z-index: 1;
  width: 100%;
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  grid-template-rows: minmax(238px, auto) minmax(238px, auto);
  gap: 24px;
  max-width: 1180px;
  margin: 0 auto;
}

/* 流水线连线：铺满卡片区，与栅格对齐（viewBox 1180x520） */
.flow-links {
  position: absolute;
  inset: 0;
  z-index: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  overflow: visible;
}

.edge-base {
  fill: none;
  stroke: var(--c-border);
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
  marker-end: url(#flow-arrow);
}

.edge-flow {
  fill: none;
  stroke: #35c6d3;
  stroke-width: 2.6;
  vector-effect: non-scaling-stroke;
  stroke-dasharray: 8 96;
  stroke-dashoffset: 0;
  opacity: 0.85;
  filter: drop-shadow(0 0 4px rgba(53, 198, 211, 0.75));
  animation: edge-dash 2.2s linear infinite;
}

@keyframes edge-dash {
  to { stroke-dashoffset: -104; }
}

.edge-pulse {
  fill: #7ee3ec;
  opacity: 0.9;
  filter: drop-shadow(0 0 5px rgba(126, 227, 236, 0.9));
}

.edge-pulse.lag {
  opacity: 0.5;
}

/* 任务运行中：对应链路变绿加速发光 */
.edge.active .edge-base {
  stroke: rgba(134, 201, 87, 0.55);
  marker-end: url(#flow-arrow-on);
}

.edge.active .edge-flow {
  stroke: #86c957;
  opacity: 1;
  filter: drop-shadow(0 0 6px rgba(134, 201, 87, 0.95));
  animation-duration: 0.9s;
}

.edge.active .edge-pulse {
  fill: #b7ec86;
  opacity: 1;
}

#flow-arrow path {
  fill: var(--c-border);
}

#flow-arrow-on path {
  fill: #86c957;
}

.flow-card {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  min-height: 206px;
  padding: 12px;
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-panel-elevated);
  color: var(--c-fg);
  text-decoration: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
}

.flow-card:hover,
.flow-card:focus-visible {
  transform: translateY(-4px);
  border-color: var(--c-accent);
  background: var(--c-panel);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  outline: none;
}

.flow-card:hover .flow-action,
.flow-card:focus-visible .flow-action {
  color: var(--c-accent);
}

/* 任务运行中：卡片绿框 + 底部进度条空间 */
.flow-card.running {
  overflow: hidden;
  border-color: rgba(134, 201, 87, 0.65);
  box-shadow: 0 0 0 1px rgba(134, 201, 87, 0.3), 0 10px 28px rgba(134, 201, 87, 0.14);
  padding-bottom: 46px;
}

/* 实时进度条：贴卡片底部全宽，深色底 + 动画条纹 + 大号百分比 */
.live-bar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1;
  height: 34px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  background: linear-gradient(90deg, #143d1f, #1c5228);
  box-shadow: inset 0 1px 0 rgba(183, 236, 134, 0.25);
}

.live-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.4px;
  color: #d9f7b8;
}

/* 雷达点：实心点 + 扩散圆环 */
.live-label i {
  position: relative;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #a5f26b;
  flex-shrink: 0;
}

.live-label i::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: #a5f26b;
  animation: live-ping 1.3s cubic-bezier(0, 0, 0.2, 1) infinite;
}

@keyframes live-ping {
  75%, 100% {
    transform: scale(2.6);
    opacity: 0;
  }
}

.live-pct {
  flex-shrink: 0;
  margin-left: auto;
  font-size: 15px;
  font-weight: 900;
  font-variant-numeric: tabular-nums;
  color: #fff;
  text-shadow: 0 0 8px rgba(165, 242, 107, 0.65);
}

.live-track {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 4px;
  background: rgba(255, 255, 255, 0.14);
}

.live-fill {
  height: 100%;
  background: repeating-linear-gradient(
    -45deg,
    #86c957 0 10px,
    #a5f26b 10px 20px
  );
  background-size: 200% 100%;
  animation: live-stripes 0.9s linear infinite;
  box-shadow: 0 0 10px rgba(165, 242, 107, 0.8);
  transition: width 600ms ease;
}

@keyframes live-stripes {
  to { background-position: 28.28px 0; }
}

:root:is([data-theme='dark'], [data-theme='platform-dark']) .live-bar {
  background: linear-gradient(90deg, #10270f, #1b431b);
}

.step-ontology { grid-column: 1 / span 4; grid-row: 1; }
.step-files { grid-column: 5 / span 4; grid-row: 1; }
.step-kb { grid-column: 9 / span 4; grid-row: 1; }
/* 下排蛇形：03 图谱在最右，向左流经 04 向量 → 05 检索 → 06 智能体 */
.step-graph { grid-column: 10 / span 3; grid-row: 2; }
.step-vectors { grid-column: 7 / span 3; grid-row: 2; }
.step-rag { grid-column: 4 / span 3; grid-row: 2; }
.step-agent { grid-column: 1 / span 3; grid-row: 2; }

.art-panel {
  height: 126px;
  min-height: 126px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.menu-art {
  width: 100%;
  height: 100%;
  overflow: visible;
  transition: transform 180ms ease;
}

.flow-copy {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 2px 4px 0;
}

.step-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 5px;
}

.step-number,
.start-chip,
.branch-chip,
.agent-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 22px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 900;
  line-height: 1;
}

.step-number {
  min-width: 32px;
  padding: 0 8px;
  background: #e4f7f7;
  color: #0b6d85;
  box-shadow: inset 0 0 0 1px rgba(14, 107, 133, 0.18);
}

.start-chip {
  padding: 0 9px;
  background: #f6bd4b;
  color: #17303a;
  box-shadow: inset 0 0 0 1px rgba(112, 70, 7, 0.18);
}

.define-chip {
  padding: 0 9px;
  background: rgba(14, 107, 133, 0.15);
  color: #0e6b85;
  box-shadow: inset 0 0 0 1px rgba(14, 107, 133, 0.2);
}

.branch-chip {
  padding: 0 9px;
  background: rgba(134, 201, 87, 0.2);
  color: #386f1e;
  box-shadow: inset 0 0 0 1px rgba(74, 128, 39, 0.2);
}

.agent-chip {
  padding: 0 9px;
  background: rgba(124, 58, 237, 0.14);
  color: #6d28d9;
  box-shadow: inset 0 0 0 1px rgba(109, 40, 217, 0.24);
}

:root[data-theme='dark'] .agent-chip {
  background: rgba(139, 92, 246, 0.18);
  color: #b79bf7;
  box-shadow: inset 0 0 0 1px rgba(139, 92, 246, 0.32);
}

.flow-role {
  color: var(--c-secondary);
  font-size: 11px;
  font-weight: 800;
}

:root:is([data-theme='dark'], [data-theme='platform-dark']) .flow-role {
  color: var(--c-secondary);
}

.flow-copy h2 {
  margin: 4px 0 0;
  font-size: 18px;
  line-height: 1.25;
  letter-spacing: 0;
}

.flow-copy p {
  margin: 7px 0 0;
  color: var(--c-secondary);
  font-size: 13px;
  line-height: 1.55;
}

:root[data-theme='dark'] .flow-copy p {
  color: var(--c-secondary);
}

.flow-action {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: auto;
  padding-top: 10px;
  color: var(--c-accent);
  font-size: 13px;
  font-weight: 800;
  transition: transform 160ms ease;
}

.flow-action svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

:root[data-theme='dark'] .flow-action {
  color: var(--c-accent);
}

.workflow-text {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  max-width: 1180px;
  margin: 22px auto 0;
}

.workflow-item {
  display: grid;
  grid-template-columns: auto 1fr;
  column-gap: 12px;
  row-gap: 4px;
  padding: 16px 0 0;
  border-top: 1px solid var(--c-border);
}

.workflow-item span {
  grid-row: span 2;
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #e8faf9;
  color: #0f7893;
  font-size: 12px;
  font-weight: 900;
}

:root:is([data-theme='dark'], [data-theme='platform-dark']) .workflow-item span {
  background: rgba(120, 220, 227, 0.12);
  color: #78dce3;
}

.workflow-item strong {
  font-size: 14px;
  line-height: 1.35;
}

.workflow-item p {
  color: var(--c-secondary);
  font-size: 13px;
  line-height: 1.65;
}

.art-grid path {
  fill: none;
  stroke: #9bdff1;
  stroke-width: 1.1;
  opacity: 0.45;
}

:root:is([data-theme='dark'], [data-theme='platform-dark']) .art-grid path {
  stroke: rgba(141, 230, 236, 0.42);
}

.art-shadow {
  fill: rgba(25, 89, 106, 0.16);
}

.iso-base,
.cyan-plate,
.folder-back,
.folder-face,
.paper,
.cylinder-body,
.cylinder-top,
.cylinder-liquid,
.chunk,
.card-feed,
.console-top,
.console-face,
.console-side,
.screen,
.bubble,
.cloud-wall,
.cloud-inner,
.magnifier-disc,
.magnifier,
.vector-node,
.graph-node,
.scope-eye,
.scope-base {
  stroke: #15313d;
  stroke-width: 2.2;
  stroke-linejoin: round;
}

.iso-base {
  fill: #f8fcfc;
}

.cyan-plate {
  fill: #83dce1;
}

.folder-back {
  fill: #a6eef0;
}

.folder-face {
  fill: #43b9c7;
}

.paper {
  fill: #eefbfc;
}

.paper-two {
  fill: #c7f4f4;
}

.paper-line,
.screen-line,
.bubble-line,
.upload-arrow,
.tube-line,
.tiny-node,
.vector-link,
.graph-link,
.scope-stand,
.magnifier-handle,
.question-hook,
.cylinder-rim {
  fill: none;
  stroke: #0f5c78;
  stroke-width: 2.3;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.tiny-node {
  stroke-width: 1.8;
}

.token {
  stroke: #15313d;
  stroke-width: 2;
}

.token.green {
  fill: #86c957;
}

.token.yellow {
  fill: #f6bd4b;
}

.token.small {
  stroke-width: 1.6;
}

.cylinder-body {
  fill: rgba(103, 218, 226, 0.38);
}

.cylinder-body.slim {
  fill: rgba(103, 218, 226, 0.34);
}

.cylinder-top {
  fill: #e8fcfc;
}

.cylinder-liquid {
  fill: #9eeaed;
}

.chunk {
  fill: #68d0d7;
}

.chunk-two {
  fill: #b9f2f1;
}

.chunk-three {
  fill: #8be2e6;
}

.gear-ring,
.gear-tooth {
  fill: #93d461;
  stroke: #15313d;
  stroke-width: 2.2;
  stroke-linejoin: round;
}

.card-feed {
  fill: #f3feff;
  stroke: #15313d;
  stroke-width: 2.2;
  stroke-linejoin: round;
}

.card-feed.second {
  fill: #c3f3f5;
}

.console-top {
  fill: #83dce1;
}

.console-face {
  fill: #106f8e;
}

.console-side {
  fill: #33a7b7;
}

.screen {
  fill: #eefbfc;
}

.bubble {
  fill: #f3feff;
}

.bubble-line.short {
  stroke-width: 2;
}

.question-dot {
  fill: #0f7893;
  stroke: #15313d;
  stroke-width: 2;
}

.magnifier-disc {
  fill: #ccf6f6;
}

.magnifier {
  fill: #3fbac8;
}

.vector-node {
  fill: #65cdd6;
}

.vector-node.big {
  fill: #a4eff0;
}

.cloud-wall {
  fill: #43b9c7;
}

.cloud-inner {
  fill: #a0e8ec;
}

.graph-node.white {
  fill: #f5feff;
}

.graph-node.green {
  fill: #86c957;
}

.graph-node.blue,
.scope-eye {
  fill: #65cdd6;
}

.scope-base {
  fill: #f3feff;
}

/* 智能体插画样式 */
.robot-head {
  fill: rgba(14, 107, 133, 0.2);
  stroke: #0e6b85;
  stroke-width: 2;
  stroke-linejoin: round;
}
.robot-face {
  fill: #e8fcfc;
  stroke: #15313d;
  stroke-width: 2.2;
  stroke-linejoin: round;
}
.robot-eye {
  fill: #0f7893;
  stroke: #15313d;
  stroke-width: 2;
}
.robot-mouth,
.robot-antenna {
  fill: none;
  stroke: #0f5c78;
  stroke-width: 2.3;
  stroke-linecap: round;
}
.bolt {
  fill: #f6bd4b;
  stroke: #15313d;
  stroke-width: 2;
  stroke-linejoin: round;
}
:root:is([data-theme='dark'], [data-theme='platform-dark']) .robot-face {
  fill: #eefbfc;
}

/* 本体管理插画样式 */
.ontology-hub {
  fill: rgba(14, 107, 133, 0.2);
  stroke: #0e6b85;
  stroke-width: 2;
}
.ontology-core {
  fill: #83dce1;
  stroke: #0e6b85;
  stroke-width: 2;
}
.ontology-node {
  fill: #86c957;
  stroke: #15313d;
  stroke-width: 2;
}
.ontology-link {
  fill: none;
  stroke: #0e6b85;
  stroke-width: 2;
  stroke-dasharray: 4 3;
}
.attr-bar {
  fill: none;
  stroke: #0e6b85;
  stroke-width: 2.5;
  stroke-linecap: round;
}

:root:is([data-theme='dark'], [data-theme='platform-dark']) .iso-base,
:root:is([data-theme='dark'], [data-theme='platform-dark']) .paper,
:root:is([data-theme='dark'], [data-theme='platform-dark']) .bubble,
:root:is([data-theme='dark'], [data-theme='platform-dark']) .screen,
:root:is([data-theme='dark'], [data-theme='platform-dark']) .scope-base,
:root:is([data-theme='dark'], [data-theme='platform-dark']) .graph-node.white {
  fill: #eefbfc;
}

@media (max-width: 980px) {
  .flow-map {
    grid-template-columns: 1fr;
    grid-template-rows: none;
    gap: 16px;
    max-width: 520px;
  }

  .flow-links {
    display: none;
  }

  .step-ontology,
  .step-files,
  .step-kb,
  .step-graph,
  .step-vectors,
  .step-rag,
  .step-agent {
    grid-column: auto;
    grid-row: auto;
  }

  .step-ontology { order: 1; }
  .step-files { order: 2; }
  .step-kb { order: 3; }
  .step-graph { order: 4; }
  .step-vectors { order: 5; }
  .step-rag { order: 6; }
  .step-agent { order: 7; }
}

@media (max-width: 640px) {
  .home-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .home-title-block h1 {
    font-size: 26px;
  }

  .home-title-block p {
    font-size: 14px;
  }

  .flow-band {
    margin-left: -16px;
    margin-right: -16px;
    padding: 22px 16px;
    min-height: 0;
  }

  .flow-card {
    min-height: 196px;
  }

  .art-panel {
    height: 116px;
    min-height: 116px;
  }

  .workflow-text {
    grid-template-columns: 1fr;
  }
}
</style>
