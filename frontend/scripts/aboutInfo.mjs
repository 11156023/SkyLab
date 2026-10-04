/**
 * 建置時產生「關於」分頁要顯示的資料：版本、commit、授權與直接依賴的授權清單。
 * 由 vite.config.js 以 define 注入成全域常數 __SKYLAB_ABOUT__，不落地成檔案，
 * 所以 package.json 一變，下一次 build／dev 就會跟著更新。
 */
import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

/* package.json 沒寫 license 欄位、或寫的是一段說明文字而不是 SPDX 代號的套件 */
const LICENSE_OVERRIDES = {
  "react-vnc": "MIT",
  gsap: "GSAP Standard License",
};

function readJson(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}

function normalizeRepository(repository) {
  const raw = typeof repository === "string" ? repository : repository?.url;
  if (!raw) return "";
  return raw
    .replace(/^git\+/, "")
    .replace(/^git:\/\//, "https://")
    .replace(/^ssh:\/\/git@/, "https://")
    .replace(/\.git$/, "")
    .replace(/#.*$/, "");
}

function resolveCommit(root) {
  try {
    return execSync("git rev-parse --short HEAD", { cwd: root, stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    /* Docker build context 沒有 .git：由 compose／CI 以 SOURCE_COMMIT 帶入 */
    const fromEnv = process.env.SOURCE_COMMIT || process.env.VITE_SENTRY_RELEASE || "";
    return fromEnv.slice(0, 12);
  }
}

export function buildAboutInfo(root) {
  const pkg = readJson(path.join(root, "package.json"));
  const dependencies = Object.keys(pkg.dependencies ?? {})
    .sort((a, b) => a.localeCompare(b))
    .map((name) => {
      const metaFile = path.join(root, "node_modules", name, "package.json");
      let version = String(pkg.dependencies[name]).replace(/^[\^~]/, "");
      let license = "";
      let repository = "";
      if (existsSync(metaFile)) {
        const meta = readJson(metaFile);
        version = meta.version ?? version;
        license = typeof meta.license === "string" ? meta.license : meta.license?.type ?? "";
        repository = normalizeRepository(meta.repository) || meta.homepage || "";
      }
      if (LICENSE_OVERRIDES[name]) license = LICENSE_OVERRIDES[name];
      return { name, version, license, repository };
    });

  return {
    name: "SkyLab",
    version: pkg.version ?? "",
    commit: resolveCommit(root),
    builtAt: new Date().toISOString(),
    license: "AGPL-3.0",
    repository: process.env.SKYLAB_REPO_URL || "https://github.com/ntubclass/SkyLab",
    dependencies,
  };
}
