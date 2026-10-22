// Reads the step structure of the user manual pages.
//
// Under the "## Step by step" heading (zh-TW: "## 操作步驟") every "### " heading
// is a section, numbered from 1 in page order. Inside a section every top-level
// numbered list item ("1. ", "2. " at column 0) is a step, numbered from 1 in
// order of appearance, even when the section has more than one list. Each
// section has one picture, public/manual-shots/<lang>/<page>/s01.webp, placed at
// the end of the section, with the steps marked by their numbers. A section
// with no numbered steps has a single "step 0".
// Pages with several step chapters ("## Step by step: teacher home") count
// their sections across all of them.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const WEBSITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
export const SHOTS_DIR = path.join(WEBSITE, "public", "manual-shots");
export const LANGS = ["en", "zh-TW"];
const STEPS_HEADING = { en: "## Step by step", "zh-TW": "## 操作步驟" };

export const manualFile = (lang, page) =>
  path.join(WEBSITE, ...(lang === "en" ? [] : [lang]), "manual", `${page}.md`);

export const sectionName = (section) => `s${String(section).padStart(2, "0")}`;
export const sectionUrl = (lang, page, section) => `/manual-shots/${lang}/${page}/${sectionName(section)}.webp`;

export function listPages() {
  return fs
    .readdirSync(path.join(WEBSITE, "manual"))
    .filter((f) => f.endsWith(".md") && f !== "index.md")
    .map((f) => f.slice(0, -3))
    .sort();
}

const IMAGE_LINE = /^\s*!\[[^\]]*\]\(\/manual-shots\/[^)]*\)\s*$/;

// Returns { lines, eol, sections: [{ heading, line, steps: [{ n, line, end, indent, text }] }] }.
// `end` is the index of the last line that belongs to the step's list item.
// Image lines this tool inserted are left out of the item so they can be replaced.
export function parsePage(lang, page) {
  const raw = fs.readFileSync(manualFile(lang, page), "utf8");
  const eol = raw.includes("\r\n") ? "\r\n" : "\n";
  const lines = raw.split(/\r?\n/);
  const sections = [];
  let inSteps = false;
  let fence = false;
  let current = null;
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*(```|~~~)/.test(l)) fence = !fence;
    if (fence) continue;
    if (/^## /.test(l)) {
      inSteps = l.startsWith(STEPS_HEADING[lang]);
      current = null;
      continue;
    }
    if (!inSteps) continue;
    if (/^### /.test(l)) {
      current = { heading: l.replace(/^###\s+/, "").trim(), line: i, steps: [] };
      sections.push(current);
      continue;
    }
    const mt = l.match(/^(\d+)\.\s+/);
    if (mt && current) {
      current.steps.push({ n: current.steps.length + 1, line: i, end: itemEnd(lines, i), indent: mt[0].length, text: l.slice(mt[0].length) });
    }
  }
  for (const sec of sections) {
    // Last content line before the next heading; the section picture goes after it.
    sec.end = sec.line;
    for (let j = sec.line + 1; j < lines.length && !/^#{1,3} /.test(lines[j]); j++) {
      if (lines[j].trim() !== "" && !IMAGE_LINE.test(lines[j])) sec.end = j;
    }
    if (!sec.steps.length) sec.steps.push({ n: 0, line: sec.line, end: sec.end, indent: 0, text: "(whole section)" });
  }
  return { lines, eol, sections };
}

function itemEnd(lines, start) {
  let end = start;
  for (let j = start + 1; j < lines.length; j++) {
    const l = lines[j];
    if (/^#{1,6} /.test(l)) break;
    if (l.trim() === "") continue;
    if (!/^\s/.test(l)) break;
    if (IMAGE_LINE.test(l)) continue;
    end = j;
  }
  return end;
}

export const isImageLine = (l) => IMAGE_LINE.test(l);

// Plain text of a heading for alt text: drop markdown emphasis and code marks.
export const plain = (s) => s.replace(/[*_`]/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").trim();
