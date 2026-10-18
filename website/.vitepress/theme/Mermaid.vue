<script setup lang="ts">
// Renders a ```mermaid fence in the browser. mermaid is loaded from a CDN at
// runtime so the site has no build-time dependency on it.
import { onMounted, ref, watch } from 'vue'
import { useData } from 'vitepress'

const props = defineProps<{ code: string }>()
const { isDark } = useData()
const svg = ref('')
const error = ref('')
let counter = 0

const MERMAID_SRC = 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs'

async function render() {
  try {
    const mermaid = (await import(/* @vite-ignore */ MERMAID_SRC)).default
    mermaid.initialize({ startOnLoad: false, theme: isDark.value ? 'dark' : 'default', securityLevel: 'strict' })
    const id = `mermaid-${Date.now()}-${counter++}`
    const result = await mermaid.render(id, decodeURIComponent(props.code))
    svg.value = result.svg
    error.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

onMounted(render)
watch(isDark, render)
</script>

<template>
  <div class="mermaid-block">
    <div v-if="svg" v-html="svg" />
    <pre v-else-if="error" class="mermaid-error">{{ error }}</pre>
    <pre v-else class="mermaid-source">{{ decodeURIComponent(code) }}</pre>
  </div>
</template>

<style scoped>
.mermaid-block {
  margin: 16px 0;
  overflow-x: auto;
}
.mermaid-block :deep(svg) {
  max-width: 100%;
  height: auto;
}
.mermaid-source,
.mermaid-error {
  font-size: 13px;
  padding: 12px 16px;
  border-radius: 8px;
  background: var(--vp-code-block-bg);
}
.mermaid-error {
  color: var(--vp-c-danger-1);
}
</style>
