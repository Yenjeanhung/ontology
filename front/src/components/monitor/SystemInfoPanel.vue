<script setup>
import { computed } from 'vue'

const props = defineProps({
  system: { type: Object, required: true },
})

function fmtUptime(sec) {
  if (!sec && sec !== 0) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return `${d}天 ${h}小时`
  if (h > 0) return `${h}小时 ${m}分`
  return `${Math.max(1, m)}分钟`
}

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

const rows = computed(() => {
  const s = props.system || {}
  const mem = s.memory
  const disk = s.disk
  const rows = [
    { k: 'Python 版本', v: s.python_version || '—' },
    { k: '运行平台', v: s.platform || '—' },
    { k: '服务启动时间', v: fmtTime(s.started_at) },
    { k: '运行时长', v: fmtUptime(s.uptime_seconds) },
  ]
  if (mem) rows.push({ k: '内存', v: `${mem.used_mb} MB / ${mem.total_mb} MB（${mem.percent}%）` })
  if (s.cpu_percent != null) rows.push({ k: 'CPU 使用率', v: `${s.cpu_percent}%` })
  if (disk?.root) {
    const d = disk.root
    rows.push({ k: '磁盘（根）', v: `已用 ${d.used_gb} GB / 共 ${d.total_gb} GB，剩余 ${d.free_gb} GB` })
  }
  if (disk?.data) {
    const d = disk.data
    rows.push({ k: '磁盘（data）', v: `已用 ${d.used_gb} GB / 共 ${d.total_gb} GB，剩余 ${d.free_gb} GB` })
  }
  if (s.data_dir_gb != null) rows.push({ k: 'data 目录大小', v: `${s.data_dir_gb} GB` })
  return rows
})
</script>

<template>
  <section class="si-card">
    <h3 class="si-title">系统信息</h3>
    <div class="si-grid">
      <div v-for="r in rows" :key="r.k" class="si-row">
        <span class="si-k">{{ r.k }}</span>
        <span class="si-v">{{ r.v }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.si-card {
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  background: var(--c-panel-elevated);
  padding: 18px 20px;
}
.si-title { font-size: 14px; font-weight: 700; color: var(--c-fg); margin-bottom: 14px; }
.si-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px 24px; }
.si-row { display: flex; gap: 10px; font-size: 12px; line-height: 1.5; }
.si-k { color: var(--c-secondary); flex-shrink: 0; min-width: 100px; }
.si-v { color: var(--c-fg); word-break: break-all; }
</style>
