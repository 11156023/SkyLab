import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { buildAboutInfo } from "./scripts/aboutInfo.mjs";

export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, path.resolve(import.meta.dirname, ".."), "");
  const enableSignup = process.env.ENABLE_SIGNUP ?? rootEnv.ENABLE_SIGNUP ?? "true";

  return {
    define: {
      "import.meta.env.ENABLE_SIGNUP": JSON.stringify(enableSignup),
      // 帳號設定「關於」分頁：版本、commit、授權與直接依賴清單，建置時從 package.json 產生
      __SKYLAB_ABOUT__: JSON.stringify(buildAboutInfo(import.meta.dirname)),
    },
    plugins: [react()],
    server: {
      host: process.env.HOST || "127.0.0.1",
      port: Number(process.env.PORT) || 5173,
      // 前端開發埠與本機 CORS/代理設定固定對齊；占用時直接失敗，
      // 避免 Vite 靜默改用下一個埠造成前端與後端 origin 不一致。
      strictPort: true,
      // VITE_API_URL 留空（same-origin）時，/api 由 dev server 轉發到後端，
      // 讓 dev server 不必落在後端 CORS 白名單的埠
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
          ws: true,
        },
        "/ws": {
          target: "http://localhost:8000",
          changeOrigin: true,
          ws: true,
        },
      },
    },
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "src"),
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
