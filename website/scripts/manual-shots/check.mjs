// Coverage of the manual screenshots (one picture per section).
//
//   node scripts/manual-shots/check.mjs              table of sections / pictures per page
//   node scripts/manual-shots/check.mjs --list page  the sections and steps of one page, both languages
//   node scripts/manual-shots/check.mjs --strict     exit 1 if a section has no picture or the languages differ

import fs from "node:fs";
import path from "node:path";
import { LANGS, SHOTS_DIR, listPages, parsePage, sectionName } from "./lib/manual.mjs";

const args = process.argv.slice(2);
const has = (lang, page, s) => fs.existsSync(path.join(SHOTS_DIR, lang, page, `${sectionName(s)}.webp`));

if (args[0] === "--list") {
  const page = args[1];
  const docs = Object.fromEntries(LANGS.map((l) => [l, parsePage(l, page).sections]));
  docs.en.forEach((sec, i) => {
    const mark = LANGS.map((l) => (has(l, page, i + 1) ? "x" : ".")).join("");
    console.log(`\n${mark} ${sectionName(i + 1)}  ${sec.heading}  |  ${docs["zh-TW"][i]?.heading ?? "(missing)"}`);
    sec.steps.forEach((st) => {
      const zh = docs["zh-TW"][i]?.steps.find((z) => z.n === st.n);
      console.log(`     ${st.n}. ${st.text.slice(0, 110)}`);
      if (zh) console.log(`        ${zh.text.slice(0, 70)}`);
    });
  });
  process.exit(0);
}

let missing = 0;
let mismatch = 0;
let sections = 0;
for (const page of listPages()) {
  const docs = Object.fromEntries(LANGS.map((l) => [l, parsePage(l, page).sections]));
  const shape = (secs) => secs.map((s) => s.steps.length).join(",");
  const same = shape(docs.en) === shape(docs["zh-TW"]);
  if (!same) mismatch++;
  sections += docs.en.length;
  const row = LANGS.map((l) => {
    const got = docs[l].filter((_, i) => has(l, page, i + 1)).length;
    missing += docs[l].length - got;
    return `${l} ${got}/${docs[l].length}`;
  });
  console.log(`${page.padEnd(22)} ${row.join("  ")}${same ? "" : "  LANGUAGES DIFFER: " + shape(docs.en) + " vs " + shape(docs["zh-TW"])}`);
}
console.log(`\n${sections} sections per language, ${missing} picture(s) missing, ${mismatch} page(s) with different structure`);
if (args.includes("--strict") && (missing || mismatch)) process.exit(1);
