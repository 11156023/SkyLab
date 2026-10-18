<script setup lang="ts">
// Interactive REST API reference rendered by Scalar from public/openapi.json.
// The schema is produced by backend/scripts/export_openapi.py before the build;
// Scalar itself is loaded from a CDN at runtime.
import { onMounted, ref, watch } from 'vue'
import { useData, withBase } from 'vitepress'

const SCALAR_SRC = 'https://cdn.jsdelivr.net/npm/@scalar/api-reference@1'

const { isDark, lang } = useData()
const container = ref<HTMLElement | null>(null)
const state = ref<'loading' | 'ready' | 'missing' | 'failed'>('loading')
let instance: { updateConfiguration?: (config: Record<string, unknown>) => void } | null = null

const zh = () => lang.value === 'zh-TW'

function loadScalar(): Promise<void> {
  return new Promise((resolve, reject) => {
    const w = window as unknown as { Scalar?: unknown }
    if (w.Scalar) return resolve()
    const script = document.createElement('script')
    script.src = SCALAR_SRC
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('Scalar failed to load'))
    document.head.appendChild(script)
  })
}

onMounted(async () => {
  const url = withBase('/openapi.json')
  try {
    const head = await fetch(url, { method: 'HEAD' })
    if (!head.ok) {
      state.value = 'missing'
      return
    }
    await loadScalar()
    const Scalar = (window as unknown as {
      Scalar: { createApiReference: (el: HTMLElement, config: Record<string, unknown>) => typeof instance }
    }).Scalar
    instance = Scalar.createApiReference(container.value!, {
      url,
      darkMode: isDark.value,
      hideDarkModeToggle: true,
      hideClientButton: true,
      hideTestRequestButton: true,
      showSidebar: true,
    })
    state.value = 'ready'
  } catch {
    state.value = 'failed'
  }
})

watch(isDark, (dark) => {
  instance?.updateConfiguration?.({ darkMode: dark })
})
</script>

<template>
  <div class="api-reference">
    <div v-if="state === 'missing'" class="api-notice">
      <p v-if="zh()">
        這個建置沒有附 <code>openapi.json</code>。在 <code>backend/</code> 先匯出再建站：
      </p>
      <p v-else>
        This build does not include <code>openapi.json</code>. Export it from <code>backend/</code> before building the site:
      </p>
      <pre><code>uv run --no-sync python scripts/export_openapi.py ../website/public/openapi.json</code></pre>
    </div>
    <div v-else-if="state === 'failed'" class="api-notice">
      <p v-if="zh()">API 參考載入失敗，請檢查網路連線後重新整理。</p>
      <p v-else>The API reference could not be loaded. Check your network connection and reload.</p>
    </div>
    <p v-else-if="state === 'loading'" class="api-notice">{{ zh() ? '載入中…' : 'Loading…' }}</p>
    <div ref="container" />
  </div>
</template>

<style scoped>
.api-reference {
  min-height: 60vh;
}
.api-notice {
  margin: 24px auto;
  max-width: 720px;
  padding: 0 24px;
}
.api-notice pre {
  padding: 12px 16px;
  border-radius: 8px;
  background: var(--vp-code-block-bg);
  overflow-x: auto;
}
</style>
