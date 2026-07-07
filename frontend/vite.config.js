import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";

function templatesPlugin() {
  return {
    name: "virtual-templates",
    resolveId(id) {
      if (id === "virtual:templates") return "\0virtual:templates";
    },
    load(id) {
      if (id === "\0virtual:templates") {
        const jsonDir = path.resolve(
          __dirname,
          "../backend/app/ai/template_recommendation/catalog_json",
        );
        if (!fs.existsSync(jsonDir)) return "export default {}";
        const files = fs.readdirSync(jsonDir).filter((f) => f.endsWith(".json"));
        const allData = {};
        for (const f of files) {
          try {
            allData[f] = JSON.parse(fs.readFileSync(path.join(jsonDir, f), "utf-8"));
          } catch (e) {
            console.warn(`[templatesPlugin] Failed to load ${f}:`, e);
          }
        }
        return `export default ${JSON.stringify(allData)}`;
      }
    },
  };
}

export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, path.resolve(__dirname, ".."), "");
  const enableSignup = process.env.ENABLE_SIGNUP ?? rootEnv.ENABLE_SIGNUP ?? "true";

  return {
    define: {
      "import.meta.env.ENABLE_SIGNUP": JSON.stringify(enableSignup),
    },
    plugins: [react(), templatesPlugin()],
    server: {
      port: Number(process.env.PORT) || 5173,
      // VITE_API_URL 留空（same-origin）時，/api 由 dev server 轉發到後端，
      // 讓 dev server 不必落在後端 CORS 白名單的埠
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
          ws: true,
        },
      },
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    css: {
      preprocessorOptions: {
        scss: {
          additionalData: `
          @use "@/assets/styles/variables" as *;
          @use "@/assets/styles/mixins" as *;
        `,
        },
      },
    },
  };
});
