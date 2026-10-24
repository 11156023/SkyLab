// Puts each section's screenshot at the end of the section in the manual pages.
//
//   node scripts/manual-shots/inject.mjs [page ...]
//
// Image lines pointing at /manual-shots/ are removed first and inserted again
// for every section whose picture exists, so the script can be re-run at any time.

import fs from "node:fs";
import path from "node:path";
import { LANGS, SHOTS_DIR, listPages, manualFile, parsePage, plain, sectionName, sectionUrl, isImageLine } from "./lib/manual.mjs";

const pages = process.argv.slice(2).length ? process.argv.slice(2) : listPages();
let total = 0;

for (const page of pages) {
  for (const lang of LANGS) {
    const file = manualFile(lang, page);
    if (!fs.existsSync(file)) continue;
    const raw = fs.readFileSync(file, "utf8");
    const eol = raw.includes("\r\n") ? "\r\n" : "\n";
    const lines = raw.split(/\r?\n/);
    // Undo an earlier run: the image line and the blank line put before it.
    for (let i = lines.length - 1; i >= 0; i--) {
      if (!isImageLine(lines[i])) continue;
      const drop = i > 0 && lines[i - 1].trim() === "" ? 2 : 1;
      lines.splice(i - drop + 1, drop);
      i -= drop - 1;
    }
    fs.writeFileSync(file, lines.join(eol));

    const { sections } = parsePage(lang, page);
    const inserts = [];
    sections.forEach((sec, i) => {
      if (!fs.existsSync(path.join(SHOTS_DIR, lang, page, `${sectionName(i + 1)}.webp`))) return;
      const numbered = sec.steps[0].n > 0;
      const alt = lang === "en"
        ? `${plain(sec.heading)}${numbered ? " (numbers match the steps above)" : ""}`
        : `${plain(sec.heading)}${numbered ? "（圖中編號對應上方步驟）" : ""}`;
      inserts.push({ at: sec.end + 1, text: ["", `![${alt}](${sectionUrl(lang, page, i + 1)})`] });
    });
    inserts.sort((a, b) => b.at - a.at);
    for (const ins of inserts) lines.splice(ins.at, 0, ...ins.text);
    fs.writeFileSync(file, lines.join(eol));
    total += inserts.length;
    console.log(`${lang.padEnd(5)} ${page}: ${inserts.length} image(s)`);
  }
}
console.log(`total ${total}`);
