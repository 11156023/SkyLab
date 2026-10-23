// Records the demo clips of the README and the public site against the built
// frontend and the mock API of the manual screenshots.
//
//   node scripts/manual-shots/clips.mjs [options] [clip ...]     (run in website/)
//     --dist <dir>       frontend build to serve (default ../frontend/dist)
//     --lang en|zh-TW    only one language (default both)
//   FFMPEG=<path>        the ffmpeg executable, when it is not on PATH (needs libx264)
//
// Output, per clip:
//   site/public/demo/<lang>/<clip>.mp4 and .jpg (poster)   the public site's showcase
//   .github/readme/<clip>.gif                               English only, for README.md

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { LANGS, WEBSITE } from "./lib/manual.mjs";
import { CLIP_VIEWPORT, Recorder, installCursor } from "./lib/record.mjs";
import { debugShot, launchBrowser, runSection, startServer } from "./lib/run.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(WEBSITE, "..");
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
const ffmpeg = process.env.FFMPEG || "ffmpeg";
const clips = args.length
  ? args
  : fs.readdirSync(path.join(HERE, "clips")).filter((f) => f.endsWith(".mjs")).map((f) => f.slice(0, -4)).sort();

const server = await startServer(dist);
const failures = [];

for (const lang of langs) {
  const browser = await launchBrowser(lang);
  for (const name of clips) {
    const clip = (await import(pathToFileURL(path.join(HERE, "clips", `${name}.mjs`)).href)).default;
    const label = `${lang} ${name}`;
    const { s, context, mock } = await runSection({
      browser, baseUrl: server.url, lang, page: `clip-${name}`, section: 0, role: clip.role, mocks: clip.mocks,
      contextOptions: { viewport: CLIP_VIEWPORT, reducedMotion: "no-preference" }, clockRuns: true,
    });
    await context.addInitScript(installCursor);
    // The floating AI assistant button sits in the corner of every page; keep the frame clean.
    await context.addInitScript(() => {
      const add = () => {
        if (!document.head) return setTimeout(add, 10);
        const st = document.createElement("style");
        st.textContent = '[class*="_fab_"]{visibility:hidden !important}';
        document.head.appendChild(st);
      };
      add();
    });
    const rec = new Recorder(s.page);
    const t0 = Date.now();
    try {
      await clip.run(s, rec);
      const dir = path.join(REPO, "site", "public", "demo", lang);
      rec.encode({
        mp4: path.join(dir, `${name}.mp4`),
        poster: path.join(dir, `${name}.jpg`),
        ...(lang === "en" ? { gif: path.join(REPO, ".github", "readme", `${name}.gif`) } : {}),
      }, ffmpeg);
      const size = (f) => `${Math.round(fs.statSync(f).size / 1024)} KB`;
      const um = [...mock.unmatched.keys()];
      console.log(`${label}: ${rec.frames.length} frames, ${Math.round((Date.now() - t0) / 1000)} s; ` +
        `mp4 ${size(path.join(dir, `${name}.mp4`))}${lang === "en" ? `, gif ${size(path.join(REPO, ".github", "readme", `${name}.gif`))}` : ""}` +
        `${um.length ? `  unmatched: ${um.join(" | ")}` : ""}`);
    } catch (e) {
      const file = await debugShot(s, `clip-${lang}-${name}`);
      failures.push(`${label}: ${e.message.split("\n")[0]}  (see ${path.relative(WEBSITE, file)})`);
      console.log(`${label}: FAILED`);
    }
    await context.close();
  }
  await browser.close();
}

server.close();
if (failures.length) {
  console.log(`\n${failures.length} failed clip(s):\n  ${failures.join("\n  ")}`);
  process.exitCode = 1;
}
