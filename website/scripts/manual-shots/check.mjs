// Coverage of the manual screenshots.
//
//   node scripts/manual-shots/check.mjs              table of steps / images per page
//   node scripts/manual-shots/check.mjs --list page  the sections and steps of one page, both languages
//   node scripts/manual-shots/check.mjs --strict     exit 1 if any step has no image or the languages differ

import fs from "node:fs";
import path from "node:path";
import { LANGS, SHOTS_DIR, listPages, parsePage, shotName } from "./lib/manual.mjs";

const args = process.argv.slice(2);
const has = (lang, page, s, n) =>
  ["webp", "gif"].some((e) => fs.existsSync(path.join(SHOTS_DIR, lang, page, `${shotName(s, n)}.${e}`)));

if (args[0] === "--list") {
  const page = args[1];
  const docs = Object.fromEntries(LANGS.map((l) => [l, parsePage(l, page).sections]));
  docs.en.forEach((sec, i) => {
    console.log(`\ns${String(i + 1).padStart(2, "0")}  ${sec.heading}  |  ${docs["zh-TW"][i]?.heading ?? "(missing)"}`);
    sec.steps.forEach((st) => {
      const zh = docs["zh-TW"][i]?.steps[st.n - 1];
      const mark = LANGS.map((l) => (has(l, page, i + 1, st.n) ? "x" : ".")).join("");
      console.log(`  ${mark} ${st.n}. ${st.text.slice(0, 110)}`);
      if (zh) console.log(`       ${zh.text.slice(0, 70)}`);
    });
  });
  process.exit(0);
}

let missing = 0;
let mismatch = 0;
let steps = 0;
for (const page of listPages()) {
  const docs = Object.fromEntries(LANGS.map((l) => [l, parsePage(l, page).sections]));
  const shape = (secs) => secs.map((s) => s.steps.length).join(",");
  const same = shape(docs.en) === shape(docs["zh-TW"]);
  if (!same) mismatch++;
  const row = LANGS.map((l) => {
    let n = 0;
    let got = 0;
    docs[l].forEach((sec, i) => sec.steps.forEach((st) => { n++; if (has(l, page, i + 1, st.n)) got++; }));
    missing += n - got;
    if (l === "en") steps += n;
    return `${l} ${got}/${n}`;
  });
  console.log(`${page.padEnd(22)} ${row.join("  ")}${same ? "" : "  LANGUAGES DIFFER: " + shape(docs.en) + " vs " + shape(docs["zh-TW"])}`);
}
console.log(`\n${steps} steps per language, ${missing} image(s) missing, ${mismatch} page(s) with different structure`);
if (args.includes("--strict") && (missing || mismatch)) process.exit(1);
