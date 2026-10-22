// Takes the user manual screenshots against the built frontend and a mock API.
//
//   node scripts/manual-shots/shoot.mjs [options] [page ...]     (run in website/)
//     --dist <dir>       frontend build to serve (default ../frontend/dist)
//     --lang en|zh-TW    only one language (default both)
//     --section 2,5      only these sections (1-based, as in check.mjs --list)
//
// With no page names every scene in scenes/ runs. Each section runs in its own
// browser context and ends up as one picture, s<NN>.webp; a failing section is
// reported, saved to .debug/ and skipped.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { LANGS, SHOTS_DIR, WEBSITE, parsePage } from "./lib/manual.mjs";
import { debugShot, launchBrowser, runSection, startServer } from "./lib/run.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const opt = (name) => {
  const i = args.indexOf(name);
  if (i < 0) return null;
  const v = args[i + 1];
  args.splice(i, 2);
  return v;
};
const dist = path.resolve(WEBSITE, opt("--dist") ?? "../frontend/dist");
const langs = opt("--lang")?.split(",") ?? LANGS;
const onlySections = opt("--section")?.split(",").map(Number) ?? null;
const pages = args.length
  ? args
  : fs.readdirSync(path.join(HERE, "scenes")).filter((f) => f.endsWith(".mjs")).map((f) => f.slice(0, -4)).sort();

// Pictures used to be one per step (s01-1.webp); drop any left over.
for (const lang of LANGS) {
  for (const page of pages) {
    const dir = path.join(SHOTS_DIR, lang, page);
    if (fs.existsSync(dir)) for (const f of fs.readdirSync(dir)) if (/^s\d+-\d+\.webp$/.test(f)) fs.rmSync(path.join(dir, f));
  }
}

const server = await startServer(dist);
// One browser per UI language (see launchBrowser), started when first needed.
const browsers = {};
const browserFor = async (lang) => (browsers[lang] ??= await launchBrowser(lang));
const failures = [];
let shots = 0;
const t0 = Date.now();

for (const page of pages) {
  const scene = (await import(pathToFileURL(path.join(HERE, "scenes", `${page}.mjs`)).href)).default;
  for (const lang of langs) {
    const expected = parsePage(lang, page).sections;
    for (let i = 0; i < scene.sections.length; i++) {
      const section = i + 1;
      if (onlySections && !onlySections.includes(section)) continue;
      const def = scene.sections[i];
      if (!def) continue;
      const run = typeof def === "function" ? def : def.run;
      const role = def.role !== undefined ? def.role : scene.role;
      const label = `${lang} ${page} s${section}`;
      const browser = await browserFor(lang);
      const { s, context, mock } = await runSection({
        browser, baseUrl: server.url, lang, page, section, role,
        mocks: (m) => { scene.mocks?.(m); def.mocks?.(m); },
      });
      let ok = true;
      try {
        await run(s);
        await s.finish();
      } catch (e) {
        ok = false;
        const file = await debugShot(s, `${lang}-${page}-s${section}`);
        failures.push(`${label}: ${e.message.split("\n")[0]}  (see ${path.relative(WEBSITE, file)})`);
      }
      const want = expected[i]?.steps.map((st) => st.n) ?? [];
      const lacking = want.filter((n) => !s.marked.has(n));
      if (ok && s.frames) shots++;
      const um = [...mock.unmatched.keys()];
      console.log(`${label}: ${ok ? `${s.marked.size} step(s) in ${s.frames ?? 0} frame(s)` : "FAILED"}${lacking.length ? `, missing step ${lacking.join(",")}` : ""}${um.length ? `  unmatched: ${um.join(" | ")}` : ""}`);
      await context.close();
    }
    if (scene.sections.length < expected.length) {
      console.log(`${lang} ${page}: the page has ${expected.length} sections, the scene covers ${scene.sections.length}`);
    }
  }
}

for (const b of Object.values(browsers)) await b.close();
server.close();
console.log(`\n${shots} section picture(s) in ${Math.round((Date.now() - t0) / 1000)} s`);
if (failures.length) {
  console.log(`\n${failures.length} failed section(s):\n  ${failures.join("\n  ")}`);
  process.exitCode = 1;
}
