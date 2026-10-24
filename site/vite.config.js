import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { CONTACT_EMAIL, DOCS_URL, GITHUB_URL, SITE_URL } from "./site.config.js";

// 沿用平台前端的設計 token（_variables／_mixins／_themes／_reset），不另存一份
const frontendStyles = path.resolve(import.meta.dirname, "../frontend/src/assets/styles");

export default defineConfig({
  define: {
    __SITE_CONFIG__: JSON.stringify({
      siteUrl: SITE_URL,
      docsUrl: DOCS_URL,
      contactEmail: CONTACT_EMAIL,
      githubUrl: GITHUB_URL,
    }),
  },
  plugins: [react()],
  server: {
    host: process.env.HOST || "127.0.0.1",
    port: Number(process.env.PORT) || 5180,
    strictPort: true,
  },
  css: {
    preprocessorOptions: {
      scss: {
        loadPaths: [frontendStyles],
        additionalData: `
          @use "variables" as *;
          @use "mixins" as *;
        `,
      },
    },
  },
  // gsap 的 ESM 檔沒有 "type": "module"，預渲染時 Node 直接載入會失敗，所以打包進 SSR bundle
  ssr: { noExternal: ["gsap"] },
});
