<script setup>
const flowSteps = [
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
    key: 'query',
    number: '03',
    to: '/query',
    label: '问答',
    role: '主业务出口',
    line: '选择知识库提问，基于召回内容生成可追溯的回答。',
    action: '开始问答',
  },
  {
    key: 'vectors',
    number: '04',
    to: '/vectors',
    label: '向量',
    role: '召回支线',
    line: '查看分片向量、同步状态和相似召回，定位索引质量。',
    action: '查看向量',
  },
  {
    key: 'graph',
    number: '05',
    to: '/graph',
    label: '图谱',
    role: '关系支线',
    line: '观察实体、关系和文件来源，理解知识之间的连接。',
    action: '查看图谱',
  },
]
</script>

<template>
  <div class="home-view">
    <header class="home-head">
      <div class="home-title-block">
        <h1>KnowSource 知源知识中枢</h1>
        <p>从文件和数据采集开始，进入知识库加工，再输出到问答；向量和图谱作为知识库的索引与关系分析支线。</p>
      </div>
    </header>

    <section class="flow-band" aria-label="系统菜单流程">
      <div class="flow-map">
        <svg class="flow-links" viewBox="0 0 1000 720" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <marker id="flow-arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="strokeWidth">
              <path d="M2 2 10 6 2 10Z" />
            </marker>
          </defs>
          <path class="flow-link main-link" d="M285 205 C340 205 374 205 428 205" marker-end="url(#flow-arrow)" />
          <path class="flow-link main-link" d="M572 205 C626 205 660 205 715 205" marker-end="url(#flow-arrow)" />
          <path class="flow-link branch-link" d="M500 294 C500 350 500 378 500 414" />
          <path class="flow-link branch-link" d="M500 414 C456 444 405 474 333 508" marker-end="url(#flow-arrow)" />
          <path class="flow-link branch-link" d="M500 414 C544 444 595 474 667 508" marker-end="url(#flow-arrow)" />
          <circle class="flow-dot dot-files" cx="167" cy="205" r="6" />
          <circle class="flow-dot dot-kb" cx="500" cy="205" r="7" />
          <circle class="flow-dot dot-query" cx="833" cy="205" r="6" />
          <circle class="flow-dot dot-vector" cx="333" cy="508" r="6" />
          <circle class="flow-dot dot-graph" cx="667" cy="508" r="6" />
        </svg>

        <RouterLink
          v-for="step in flowSteps"
          :key="step.key"
          :to="step.to"
          :class="['flow-card', `step-${step.key}`]"
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

              <g v-if="step.key === 'files'" class="art-scene files-art">
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

              <g v-else-if="step.key === 'query'" class="art-scene query-art">
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
              <span v-if="step.key === 'files'" class="start-chip">START</span>
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
        <p>知识库把资料变成可检索的分片，同时可以派生向量索引和实体关系数据。</p>
      </div>
      <div class="workflow-item">
        <span>03</span>
        <strong>问答是面向使用者的主出口</strong>
        <p>用户最终通过问答消费知识；向量和图谱用于校验、调试和深入分析。</p>
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
  border-top: 1px solid rgba(73, 174, 196, 0.2);
  border-bottom: 1px solid rgba(73, 174, 196, 0.2);
  background:
    linear-gradient(115deg, rgba(229, 250, 250, 0.96), rgba(245, 254, 253, 0.88)),
    linear-gradient(90deg, rgba(86, 191, 213, 0.13) 1px, transparent 1px),
    linear-gradient(0deg, rgba(86, 191, 213, 0.13) 1px, transparent 1px);
  background-size: auto, 72px 72px, 72px 72px;
}

:root[data-theme='dark'] .flow-band {
  border-color: rgba(73, 174, 196, 0.22);
  background:
    linear-gradient(115deg, rgba(229, 250, 250, 0.98), rgba(245, 254, 253, 0.92)),
    linear-gradient(90deg, rgba(86, 191, 213, 0.14) 1px, transparent 1px),
    linear-gradient(0deg, rgba(86, 191, 213, 0.14) 1px, transparent 1px);
  background-size: auto, 72px 72px, 72px 72px;
}

.flow-map {
  position: relative;
  z-index: 1;
  width: 100%;
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  grid-template-rows: minmax(238px, auto) minmax(224px, auto);
  gap: 30px 22px;
  max-width: 1120px;
  margin: 0 auto;
}

.flow-links {
  position: absolute;
  inset: 0;
  z-index: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.flow-link {
  fill: none;
  stroke: #0e6b85;
  stroke-width: 4.5;
  stroke-linecap: round;
  opacity: 0.42;
}

.branch-link {
  stroke-dasharray: 9 10;
}

#flow-arrow path {
  fill: #0e6b85;
  opacity: 0.72;
}

.flow-dot {
  fill: #f6bd4b;
  stroke: #164456;
  stroke-width: 3;
}

.dot-kb,
.dot-vector,
.dot-graph {
  fill: #86c957;
}

:root[data-theme='dark'] .flow-dot {
  stroke: #164456;
}

.flow-card {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  min-height: 206px;
  padding: 12px;
  border: 1px solid rgba(13, 86, 108, 0.16);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.88);
  color: #102b36;
  text-decoration: none;
  box-shadow: 0 18px 42px rgba(35, 106, 116, 0.12);
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
}

.flow-card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  box-shadow: inset 0 0 0 0 rgba(14, 107, 133, 0);
  transition: box-shadow 160ms ease;
}

.flow-card:hover,
.flow-card:focus-visible {
  transform: translateY(-6px);
  border-color: rgba(14, 107, 133, 0.44);
  background: rgba(255, 255, 255, 0.97);
  box-shadow: 0 22px 52px rgba(35, 106, 116, 0.2);
  outline: none;
}

.flow-card:hover::before,
.flow-card:focus-visible::before {
  box-shadow: inset 0 0 0 2px rgba(14, 107, 133, 0.18);
}

.flow-card:hover .menu-art,
.flow-card:focus-visible .menu-art {
  transform: translateY(-5px) scale(1.03);
}

.flow-card:hover .flow-action,
.flow-card:focus-visible .flow-action {
  transform: translateX(3px);
}

:root[data-theme='dark'] .flow-card {
  border-color: rgba(13, 86, 108, 0.18);
  background: rgba(255, 255, 255, 0.9);
  color: #102b36;
  box-shadow: 0 18px 42px rgba(35, 106, 116, 0.14);
}

:root[data-theme='dark'] .flow-card:hover,
:root[data-theme='dark'] .flow-card:focus-visible {
  border-color: rgba(14, 107, 133, 0.44);
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 22px 52px rgba(35, 106, 116, 0.2);
}

.step-files {
  grid-column: 1 / 3;
  grid-row: 1;
}

.step-kb {
  grid-column: 3 / 5;
  grid-row: 1;
}

.step-query {
  grid-column: 5 / 7;
  grid-row: 1;
}

.step-vectors {
  grid-column: 2 / 4;
  grid-row: 2;
}

.step-graph {
  grid-column: 4 / 6;
  grid-row: 2;
}

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
.branch-chip {
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

.branch-chip {
  padding: 0 9px;
  background: rgba(134, 201, 87, 0.2);
  color: #386f1e;
  box-shadow: inset 0 0 0 1px rgba(74, 128, 39, 0.2);
}

.flow-role {
  color: #0f7893;
  font-size: 11px;
  font-weight: 800;
}

:root[data-theme='dark'] .flow-role {
  color: #0f7893;
}

.flow-copy h2 {
  margin: 4px 0 0;
  font-size: 18px;
  line-height: 1.25;
  letter-spacing: 0;
}

.flow-copy p {
  margin: 7px 0 0;
  color: #49616a;
  font-size: 13px;
  line-height: 1.55;
}

:root[data-theme='dark'] .flow-copy p {
  color: #49616a;
}

.flow-action {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: auto;
  padding-top: 10px;
  color: #0c6680;
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
  color: #0c6680;
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

:root[data-theme='dark'] .workflow-item span {
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

:root[data-theme='dark'] .art-grid path {
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

:root[data-theme='dark'] .iso-base,
:root[data-theme='dark'] .paper,
:root[data-theme='dark'] .bubble,
:root[data-theme='dark'] .screen,
:root[data-theme='dark'] .scope-base,
:root[data-theme='dark'] .graph-node.white {
  fill: #eefbfc;
}

@media (max-width: 980px) {
  .flow-map {
    grid-template-columns: 1fr;
    grid-template-rows: none;
    gap: 16px;
    max-width: 520px;
  }

  .flow-map::before {
    content: '';
    position: absolute;
    top: 40px;
    bottom: 40px;
    left: 28px;
    width: 3px;
    border-radius: 999px;
    background: rgba(14, 107, 133, 0.22);
  }

  .flow-links {
    display: none;
  }

  .step-files,
  .step-kb,
  .step-query,
  .step-vectors,
  .step-graph {
    grid-column: auto;
    grid-row: auto;
  }

  .step-files {
    order: 1;
  }

  .step-kb {
    order: 2;
  }

  .step-query {
    order: 3;
  }

  .step-vectors {
    order: 4;
  }

  .step-graph {
    order: 5;
  }
}

@media (max-width: 640px) {
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
