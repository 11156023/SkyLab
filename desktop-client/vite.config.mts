import { existsSync, readFileSync, rmSync } from "node:fs";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import electron from "vite-plugin-electron";
import { notBundle } from "vite-plugin-electron/plugin";
import { resolve } from "path";

import pkg from "./package.json" with { type: "json" };

/** 路径查找 */
const pathResolve = (dir: string): string => {
  return resolve(import.meta.dirname, ".", dir);
};

/** 「關於」頁的直接依賴授權清單：建置時從 package.json 與 node_modules 讀，注入成 __SKYLAB_ABOUT__ */
const buildAboutInfo = () => {
  const deps =
    "dependencies" in pkg ? (pkg.dependencies as Record<string, string>) : {};
  const dependencies = Object.keys(deps)
    .sort((a, b) => a.localeCompare(b))
    .map(name => {
      const metaFile = pathResolve(`node_modules/${name}/package.json`);
      let version = deps[name].replace(/^[\^~]/, "");
      let license = "";
      let repository = "";
      if (existsSync(metaFile)) {
        const meta = JSON.parse(readFileSync(metaFile, "utf8"));
        version = meta.version ?? version;
        license =
          typeof meta.license === "string"
            ? meta.license
            : (meta.license?.type ?? "");
        const raw =
          typeof meta.repository === "string"
            ? meta.repository
            : meta.repository?.url;
        repository = raw
          ? String(raw)
              .replace(/^git\+/, "")
              .replace(/^git:\/\//, "https://")
              .replace(/^ssh:\/\/git@/, "https://")
              .replace(/\.git$/, "")
          : (meta.homepage ?? "");
      }
      return { name, version, license, repository };
    });
  return {
    license: "AGPL-3.0",
    repository:
      process.env.SKYLAB_REPO_URL || "https://github.com/ntubclass/SkyLab",
    dependencies
  };
};

// https://vitejs.dev/config/
export default defineConfig(({ command }) => {
  rmSync("dist-electron", { recursive: true, force: true });

  const isServe = command === "serve";
  const isBuild = command === "build";
  const sourcemap = isServe || !!process.env.VSCODE_DEBUG;

  return {
    define: {
      __SKYLAB_ABOUT__: JSON.stringify(buildAboutInfo())
    },
    css: {
      preprocessorOptions: {
        scss: {
          api: "modern-compiler"
        }
      } as any
    },
    plugins: [
      vue(),
      electron([
        {
          // Main process entry file of the Electron App.
          entry: "electron/main/index.ts",
          onstart({ startup }) {
            if (!process.env.VSCODE_DEBUG) startup();
          },
          vite: {
            build: {
              sourcemap,
              minify: isBuild,
              outDir: "dist-electron/main",
              rollupOptions: {
                // Some third-party Node.js libraries may not be built correctly by Vite, especially `C/C++` addons,
                // we can use `external` to exclude them to ensure they work correctly.
                // Others need to put them in `dependencies` to ensure they are collected into `app.asar` after the app is built.
                // Of course, this is not absolute, just this way is relatively simple. :)
                external: Object.keys(
                  "dependencies" in pkg ? pkg.dependencies : {}
                )
              }
            },
            plugins: [
              // This is just an option to improve build performance, it's non-deterministic!
              // e.g. `import log from 'electron-log'` -> `const log = require('electron-log')`
              isServe && notBundle()
            ]
          }
        },
        {
          entry: "electron/preload/index.ts",
          onstart({ reload }) {
            // Notify the Renderer process to reload the page when the Preload scripts build is complete,
            // instead of restarting the entire Electron App.
            reload();
          },
          vite: {
            build: {
              sourcemap: sourcemap ? "inline" : undefined, // #332
              minify: isBuild,
              outDir: "dist-electron/preload",
              rollupOptions: {
                external: Object.keys(
                  "dependencies" in pkg ? pkg.dependencies : {}
                )
              }
            },
            plugins: [isServe && notBundle()]
          }
        }
      ])
    ],
    resolve: {
      alias: {
        "@": pathResolve("src"),
        "@build": pathResolve("build")
      }
    },
    // Keep the Electron renderer separate from the web frontend on :5173.
    // The backend's device-login URL must always open the web application.
    server: {
      host: "127.0.0.1",
      port: 3344,
      strictPort: true
    },
    clearScreen: false
  };
});
