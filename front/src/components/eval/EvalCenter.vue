<script setup>
// RAG 评测中心：评测集管理 / 评测任务 / 结果详情 三个 Tab（doc/知识库/RAG评测/RAG评测页面设计.md）
import { ref } from 'vue'
import TestsetPanel from './TestsetPanel.vue'
import RunPanel from './RunPanel.vue'
import RunDetailPanel from './RunDetailPanel.vue'

const activeTab = ref('testsets')
const detailRunId = ref('')
const preselectTestsetId = ref('')

function openRunDetail(runId) {
  detailRunId.value = runId
  activeTab.value = 'detail'
}

function startEval(testsetId) {
  preselectTestsetId.value = testsetId
  activeTab.value = 'runs'
}
</script>

<template>
  <div class="eval-center">
    <div class="eval-tabs">
      <button
        v-for="t in [
          { key: 'testsets', label: '评测集管理' },
          { key: 'runs', label: '评测任务' },
          { key: 'detail', label: '结果详情', disabled: !detailRunId },
        ]"
        :key="t.key"
        class="eval-tab"
        :class="{ active: activeTab === t.key }"
        :disabled="t.disabled"
        @click="activeTab = t.key"
      >{{ t.label }}</button>
    </div>

    <TestsetPanel v-show="activeTab === 'testsets'" @start-eval="startEval" />
    <RunPanel v-show="activeTab === 'runs'" :preselect-testset-id="preselectTestsetId" @view-detail="openRunDetail" />
    <RunDetailPanel
      v-show="activeTab === 'detail'"
      :run-id="detailRunId"
      @back="activeTab = 'runs'"
    />
  </div>
</template>

<style scoped>
.eval-center { display: flex; flex-direction: column; gap: 12px; height: 100%; }
.eval-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--c-border); }
.eval-tab {
  padding: 8px 18px; border: none; background: none; cursor: pointer;
  font-size: 13px; color: var(--c-secondary);
  border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.eval-tab.active { color: var(--c-accent); border-bottom-color: var(--c-accent); font-weight: 600; }
.eval-tab:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
