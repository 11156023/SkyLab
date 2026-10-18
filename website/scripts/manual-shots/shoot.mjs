// Takes the user manual screenshots against the built frontend and a mock API.
//
//   node scripts/manual-shots/shoot.mjs [options] [page ...]     (run in website/)
//     --dist <dir>       frontend build to serve (default ../frontend/dist)
//     --lang en|zh-TW    only one language (default both)
//     --section 2,5      only these sections (1-based, as in check.mjs --list)
//
// With no page names every scene in scenes/ runs. Each section runs in its own
// browser context; a failing section is reported, saved to .debug/ and skipped.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { LANGS, WEBSITE, parsePage } from "./lib/manual.mjs";
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

const server = await startServer(dist);
const browser = await launchBrowser();
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
      const { s, context, mock } = await runSection({
        browser, baseUrl: server.url, lang, page, section, role,
        mocks: (m) => { scene.mocks?.(m); def.mocks?.(m); },
      });
      try {
        await run(s);
      } catch (e) {
        const file = await debugShot(s, `${lang}-${page}-s${section}`);
        failures.push(`${label}: ${e.message.split("\n")[0]}  (see ${path.relative(WEBSITE, file)})`);
      }
      const want = expected[i]?.steps.map((st) => st.n) ?? [];
      const got = new Set(s.written.map((f) => Number(path.basename(f).match(/-(\d+)\./)[1])));
      const lacking = want.filter((n) => !got.has(n));
      shots += s.written.length;
      const um = [...mock.unmatched.keys()];
      console.log(`${label}: ${s.written.length} shot(s)${lacking.length ? `, missing step ${lacking.join(",")}` : ""}${um.length ? `  unmatched: ${um.join(" | ")}` : ""}`);
      await context.close();
    }
    if (scene.sections.length < expected.length) {
      console.log(`${lang} ${page}: the page has ${expected.length} sections, the scene covers ${scene.sections.length}`);
    }
  }
}

await browser.close();
server.close();
console.log(`\n${shots} screenshot(s) in ${Math.round((Date.now() - t0) / 1000)} s`);
if (failures.length) {
  console.log(`\n${failures.length} failed section(s):\n  ${failures.join("\n  ")}`);
  process.exitCode = 1;
}
