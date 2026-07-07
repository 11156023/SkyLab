import fs from "node:fs"
import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

function templatesPlugin() {
  return {
    name: "virtual-templates",
    resolveId(id: string) {
      if (id === "virtual:templates") return "\0virtual:templates"
    },
    load(id: string) {
      if (id === "\0virtual:templates") {
        const jsonDir = path.resolve(
          __dirname,
          "../backend/app/ai/template_recommendation/catalog_json",
        )
        const jsonKeyPrefix = "template_catalog/"
        if (!fs.existsSync(jsonDir))
          return { code: "export default {}", moduleType: "js" }
        const files = fs.readdirSync(jsonDir).filter((f) => f.endsWith(".json"))
        const allData: Record<string, any> = {}
        for (const f of files) {
          try {
            allData[`${jsonKeyPrefix}${f}`] = JSON.parse(
              fs.readFileSync(path.join(jsonDir, f), "utf-8"),
            )
          } catch (e: unknown) {
            console.warn(
              `[templatesPlugin] Failed to load JSON file ${path.join(jsonDir, f)}:`,
              e,
            )
          }
        }
        return {
          code: `export default ${JSON.stringify(allData)}`,
          moduleType: "js",
        }
      }
    },
  }
}

export default defineConfig(({ mode }) => {
  // Load VITE_* env vars from .env files (cwd = frontend/)
  const env = loadEnv(mode, process.cwd(), "")
  const apiUrl = env.VITE_API_URL || "http://localhost:8000"
  const wsTarget = apiUrl.replace(/^http/, "ws")

  return {
    // Production 部署時掛在 /old/ subpath（共用 nginx 反向代理同源入口），
    // dev mode 仍走 root 讓 `bun run dev` 直接訪問 http://localhost:5173。
    // main.tsx 透過 import.meta.env.BASE_URL 同步 router basepath。
    base: mode === "production" ? "/old/" : "/",
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      headers: {
        "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
      },
      proxy: {
        // Backend WebSocket endpoints (VNC, terminal, jobs) — proxy to FastAPI.
        // REST goes via absolute VITE_API_URL, but WS helpers use
        // window.location.host (5173), so we need to forward /ws to backend.
        "/ws": {
          target: wsTarget,
          ws: true,
          changeOrigin: true,
          configure(proxy) {
            // node-http-proxy reports ECONNRESET / EPIPE on normal WS teardown —
            // Suppress these to avoid spurious console noise.
            proxy.on("error", (err) => {
              const code = (err as NodeJS.ErrnoException).code
              if (
                code === "ECONNRESET" ||
                code === "EPIPE" ||
                code === "ECONNABORTED"
              )
                return
              console.error("[ws proxy]", err)
            })
          },
        },
      },
    },
    plugins: [
      templatesPlugin(),
      tanstackRouter({
        target: "react",
        autoCodeSplitting: true,
      }),
      react(),
      tailwindcss(),
    ],
  }
})
