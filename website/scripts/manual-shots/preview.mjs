// Contact sheets for reviewing a page's screenshots at a glance.
//
//   node scripts/manual-shots/preview.mjs <page> [--lang zh-TW] [--section 3]
//
// Writes .debug/sheet-<lang>-<page>-<n>.png, twelve screenshots per sheet,
// each labelled with its section, step and the start of the step text.

import fs from "node:fs";
import path from "node:path";
import { SHOTS_DIR, parsePage, shotName } from "./lib/manual.mjs";
import { DEBUG_DIR, launchBrowser } from "./lib/run.mjs";

const args = process.argv.slice(2);
const opt = (name) => {
  const i = args.indexOf(name);
  if (i < 0) return null;
  const v = args[i + 1];
  args.splice(i, 2);
  return v;
};
const lang = opt("--lang") ?? "zh-TW";
const only = opt("--section");
const page = args[0];

const items = [];
parsePage(lang, page).sections.forEach((sec, i) => {
  if (only && Number(only) !== i + 1) return;
  for (const st of sec.steps) {
    const file = path.join(SHOTS_DIR, lang, page, `${shotName(i + 1, st.n)}.webp`);
    items.push({ label: `${shotName(i + 1, st.n)}  ${sec.heading} — ${st.text.slice(0, 80)}`, file: fs.existsSync(file) ? file : null });
  }
});

const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
fs.mkdirSync(DEBUG_DIR, { recursive: true });
const browser = await launchBrowser();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
const p = await ctx.newPage();
for (let k = 0; k * 12 < items.length; k++) {
  const chunk = items.slice(k * 12, k * 12 + 12);
  const cells = chunk.map((it) => `<figure><figcaption>${esc(it.label)}</figcaption>${
    it.file ? `<img src="data:image/webp;base64,${fs.readFileSync(it.file).toString("base64")}">` : `<div class="miss">MISSING</div>`
  }</figure>`).join("");
  await p.setContent(`<style>body{margin:8px;font:13px sans-serif;display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
    figure{margin:0;border:1px solid #ccc;padding:4px}img{width:100%;display:block}.miss{height:120px;background:#fee;color:#c00;display:grid;place-items:center;font-weight:bold}
    figcaption{margin-bottom:4px;height:32px;overflow:hidden}</style>${cells}`);
  const out = path.join(DEBUG_DIR, `sheet-${lang}-${page}-${k + 1}.png`);
  await p.screenshot({ path: out, fullPage: true });
  console.log(out);
}
await browser.close();
