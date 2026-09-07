<template>
  <div class="api-docs-page">
    <iframe :src="docsUrl" title="API 文档" class="api-docs-frame"></iframe>
  </div>
</template>

<script setup>
// dev 模式走 Vite 代理（相对路径）；生产模式直连后端
const base = import.meta.env.DEV ? '' : 'http://localhost:8000'
const docsUrl = `${base}/docs`
</script>

<style scoped>
.api-docs-page {
  position: absolute;
  inset: 0;
}
.api-docs-frame {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
  background: #fff;
}
</style>
