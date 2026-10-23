/**
 * 預渲染（npm run build 的最後一步）：以 vite 建出的 dist/index.html 為模板，
 * 為每個語言寫出含完整頁面內容與 SEO 標籤的 index.html（/、/en/、/ja/），
 * 再產生 sitemap.xml 與 robots.txt。dist-ssr/ 是 vite build --ssr 的產物，用完即刪。
 */
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { SITE_URL } from "../site.config.js";

const root = path.resolve(import.meta.dirname, "..");
const dist = path.join(root, "dist");
const ssrDir = path.join(root, "dist-ssr");

const { render, LANGUAGES, buildSitemap, buildRobots } = await import(
  pathToFileURL(path.join(ssrDir, "entry-server.js")).href
);

// index.html 的 <title> 換成整組 SEO 標籤（開頭就是該語言的 <title>）
const MARKERS = {
  lang: '<html lang="zh-TW">',
  head: "<title>SkyLab</title>",
  app: "<!--app-html-->",
};

const template = readFileSync(path.join(dist, "index.html"), "utf8");
for (const marker of Object.values(MARKERS)) {
  if (!template.includes(marker)) throw new Error(`dist/index.html is missing the prerender marker ${marker}`);
}

for (const { code, path: urlPath } of LANGUAGES) {
  const { html, head } = render(code);
  // 一律用函式替換：頁面內容含 "$"（終端機提示字元），字串替換會把它當成 $& 之類的特殊樣式
  const page = template
    .replace(MARKERS.lang, () => `<html lang="${code}">`)
    .replace(MARKERS.head, () => head)
    .replace(MARKERS.app, () => html);
  const outDir = path.join(dist, urlPath);
  mkdirSync(outDir, { recursive: true });
  writeFileSync(path.join(outDir, "index.html"), page);
  console.log(`prerendered ${urlPath} (${code})`);
}

const lastmod = new Date().toISOString().slice(0, 10);
writeFileSync(path.join(dist, "sitemap.xml"), buildSitemap(SITE_URL, lastmod));
writeFileSync(path.join(dist, "robots.txt"), buildRobots(SITE_URL));
rmSync(ssrDir, { recursive: true, force: true });
console.log(`sitemap.xml and robots.txt written for ${SITE_URL}`);
