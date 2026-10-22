// Contact sheets for reviewing a page's screenshots at a glance.
//
//   node scripts/manual-shots/preview.mjs <page> [--lang zh-TW] [--section 3]
//
// Writes .debug/sheet-<lang>-<page>-<n>.png, four section pictures per sheet,
// each with its heading and the start of its steps beside it.

import fs from "node:fs";
import path from "node:path";
import { SHOTS_DIR, parsePage, sectionName } from "./lib/manual.mjs";
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
  const file = path.join(SHOTS_DIR, lang, page, `${sectionName(i + 1)}.webp`);
  items.push({
    title: `${sectionName(i + 1)}  ${sec.heading}`,
    steps: sec.steps.map((st) => `${st.n}. ${st.text.slice(0, 90)}`),
    file: fs.existsSync(file) ? file : null,
  });
});

const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
fs.mkdirSync(DEBUG_DIR, { recursive: true });
const browser = await launchBrowser();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
const p = await ctx.newPage();
for (let k = 0; k * 4 < items.length; k++) {
  const chunk = items.slice(k * 4, k * 4 + 4);
  const rows = chunk.map((it) => `<section><div class="t"><b>${esc(it.title)}</b>${it.steps.map((s) => `<p>${esc(s)}</p>`).join("")}</div>${
    it.file ? `<img src="data:image/webp;base64,${fs.readFileSync(it.file).toString("base64")}">` : `<div class="miss">MISSING</div>`
  }</section>`).join("");
  await p.setContent(`<style>body{margin:8px;font:13px sans-serif}section{display:grid;grid-template-columns:330px 1fr;gap:10px;border:1px solid #ccc;padding:6px;margin-bottom:8px}
    p{margin:3px 0}img{max-width:100%;display:block}.miss{height:120px;background:#fee;color:#c00;display:grid;place-items:center;font-weight:bold}</style>${rows}`);
  const out = path.join(DEBUG_DIR, `sheet-${lang}-${page}-${k + 1}.png`);
  await p.screenshot({ path: out, fullPage: true });
  console.log(out);
}
await browser.close();
