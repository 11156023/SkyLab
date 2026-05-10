import fs from "node:fs"
import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react-swc"
import { defineConfig, loadEnv } from "vite"

function templatesPlugin() {
  return {
    name: "virtual-templates",
    resolveId(id: string) {
      if (id === "virtual:templates") return "\0virtual:templates"
    },
    load(id: string) {
      if (id === "\0virtual:templates") {
        const jsonDir = path.resolve(__dirname, "src/json")
        const jsonKeyPrefix = `${path.relative(__dirname, jsonDir).split(path.sep).join("/")}/`
        if (!fs.existsSync(jsonDir)) return "export default {}"
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
        return `export default ${JSON.stringify(allData)}`
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
