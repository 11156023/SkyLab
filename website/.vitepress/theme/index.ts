import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'
import ApiReference from './ApiReference.vue'
import Mermaid from './Mermaid.vue'
import { enableShotZoom } from './shotZoom'
import { enableShotZoom } from './shotZoom'
import './custom.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('ApiReference', ApiReference)
    app.component('Mermaid', Mermaid)
    if (!import.meta.env.SSR) enableShotZoom()
  },
} satisfies Theme
