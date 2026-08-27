<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Object, required: true },  // 规则树根节点
  depth: { type: Number, default: 1 },           // 当前层级，根=1
  maxDepth: { type: Number, default: 5 },         // 最大嵌套层级
})
const emit = defineEmits(['update:modelValue'])

const OPERATORS = [
  { value: '==', label: '==' },
  { value: '!=', label: '!=' },
  { value: '>', label: '>' },
  { value: '>=', label: '>=' },
  { value: '<', label: '<' },
  { value: '<=', label: '<=' },
  { value: 'between', label: '介于[lo,hi]' },
  { value: 'not_between', label: '不介于[lo,hi]' },
  { value: 'contains', label: '包含' },
  { value: 'not_contains', label: '不包含' },
  { value: 'startsWith', label: '以…开头' },
  { value: 'endsWith', label: '以…结尾' },
  { value: 'in', label: '属于[…]' },
  { value: 'not_in', label: '不属于[…]' },
  { value: 'regex', label: '匹配正则' },
  { value: 'not_regex', label: '不匹配正则' },
  { value: 'empty', label: '为空' },
  { value: 'not_empty', label: '非空' },
  { value: 'exists', label: '变量存在' },
  { value: 'not_exists', label: '变量不存在' },
  { value: 'type', label: '类型为' },
]

const NO_VALUE_OPS = new Set(['empty', 'not_empty', 'exists', 'not_exists'])

const canAddGroup = computed(() => props.depth < props.maxDepth)

function commit(next) {
  emit('update:modelValue', next)
}

// 根节点必须是组合节点
function ensureRoot() {
  const r = props.modelValue || {}
  if (r.combinator !== 'and' && r.combinator !== 'or') {
    return { combinator: 'and', rules: [] }
  }
  return r
}

const root = computed({
  get: () => ensureRoot(),
  set: (v) => commit(v),
})

function newLeaf() {
  return { field: '', operator: '==', value: '' }
}
function newGroup() {
  return { combinator: 'and', rules: [newLeaf()] }
}

function addRule() {
  const r = ensureRoot()
  const rules = [...(r.rules || []), newLeaf()]
  commit({ ...r, rules })
}
function addGroup() {
  if (!canAddGroup.value) return
  const r = ensureRoot()
  const rules = [...(r.rules || []), newGroup()]
  commit({ ...r, rules })
}
function removeRuleAt(idx) {
  const r = ensureRoot()
  const rules = (r.rules || []).filter((_, i) => i !== idx)
  commit({ ...r, rules })
}
function updateRuleAt(idx, rule) {
  const r = ensureRoot()
  const rules = (r.rules || []).map((x, i) => (i === idx ? rule : x))
  commit({ ...r, rules })
}

function setCombinator(v) {
  const r = ensureRoot()
  commit({ ...r, combinator: v })
}
function toggleNegate() {
  const r = ensureRoot()
  commit({ ...r, negate: !r.negate })
}
</script>

<template>
  <div class="rule-group" :class="{ 'rule-group-root': depth === 1 }">
    <div class="rule-group-head">
      <select class="combinator" :value="root.combinator" @change="setCombinator($event.target.value)">
        <option value="and">全部满足 (AND)</option>
        <option value="or">任意满足 (OR)</option>
      </select>
      <label class="negate">
        <input type="checkbox" :checked="!!root.negate" @change="toggleNegate" /> 取反
      </label>
      <span class="spacer"></span>
      <button type="button" class="mini-btn" @click="addRule">+ 条件</button>
      <button type="button" class="mini-btn" :disabled="!canAddGroup" @click="addGroup">+ 条件组</button>
    </div>

    <div v-if="!(root.rules && root.rules.length)" class="rule-empty">
      暂无条件，请添加（空组将判定为 <b>true</b>）。
    </div>

    <div v-for="(rule, idx) in root.rules" :key="idx" class="rule-row">
      <!-- 子组 -->
      <div v-if="rule.combinator === 'and' || rule.combinator === 'or'" class="rule-subgroup">
        <ConditionRuleBuilder
          :model-value="rule"
          :depth="depth + 1"
          :max-depth="maxDepth"
          @update:model-value="updateRuleAt(idx, $event)"
        />
        <button type="button" class="mini-btn danger" @click="removeRuleAt(idx)">删除组</button>
      </div>

      <!-- 叶子条件 -->
      <div v-else class="rule-leaf">
        <input type="text" class="rule-field" v-model="rule.field" placeholder="{{n3.chechang}}（可拖拽变量）" />
        <select class="rule-op" v-model="rule.operator">
          <option v-for="op in OPERATORS" :key="op.value" :value="op.value">{{ op.label }}</option>
        </select>
        <input
          v-if="!NO_VALUE_OPS.has(rule.operator)"
          type="text"
          class="rule-value"
          v-model="rule.value"
          placeholder="值（可填 {{变量}} 或常量）"
        />
        <label class="leaf-negate">
          <input type="checkbox" v-model="rule.negate" /> NOT
        </label>
        <button type="button" class="mini-btn danger" @click="removeRuleAt(idx)">删除</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rule-group {
  border: 1px solid var(--c-border, #ddd);
  border-radius: 8px;
  padding: 10px;
  background: rgba(0, 0, 0, 0.02);
}
.rule-group-root { background: transparent; border: none; padding: 0; }
.rule-group-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.rule-group-head .spacer { flex: 1; }
.combinator { padding: 4px 6px; }
.mini-btn {
  border: 1px solid var(--c-border, #ccc);
  background: var(--c-bg, #fff);
  border-radius: 6px;
  padding: 3px 8px;
  cursor: pointer;
  font-size: 12px;
}
.mini-btn:hover { border-color: var(--c-primary, #409eff); }
.mini-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.mini-btn.danger { color: var(--c-danger, #f56c6c); }
.negate, .leaf-negate { font-size: 12px; display: inline-flex; align-items: center; gap: 3px; }
.rule-empty { color: #999; font-size: 12px; padding: 6px 0; }
.rule-row { margin: 6px 0; }
.rule-subgroup { border-left: 2px solid var(--c-primary, #409eff); padding-left: 10px; margin-left: 4px; }
.rule-leaf { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.rule-field, .rule-value { flex: 1; min-width: 120px; padding: 5px 7px; border: 1px solid var(--c-border, #ccc); border-radius: 6px; }
.rule-op { padding: 5px 6px; border: 1px solid var(--c-border, #ccc); border-radius: 6px; }
</style>
