import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, expect, test } from "vitest";

import i18n from "../i18n";

/*
 * 英文單複數：數量是 1 時用 key_one（手寫的單數），其餘用沒有後綴的鍵（複數）。
 * 中文、日文沒有複數形，不加後綴。這裡檢查每個 _one 都有對應的基底鍵、都保留 {{count}}，
 * 而且 i18next 真的依數量挑到不同的句子。
 */
const EN_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "en");
const PARAMS = { name: "x", host: "h", problem: 0, shown: 1, label: "L", version: 2, today: "T", week: 1, date: "D", className: "C", applicant: "A", machines: "m", nodes: 2, list: "a", storage: "s", page: 1, totalPages: 2, skipped: 0, selected: 0 };

const singulars = fs.readdirSync(EN_DIR)
  .filter((file) => file.endsWith(".json"))
  .flatMap((file) => {
    const ns = file.replace(/\.json$/, "");
    const strings = JSON.parse(fs.readFileSync(path.join(EN_DIR, file), "utf8"));
    return Object.keys(strings)
      .filter((key) => key.endsWith("_one"))
      .map((key) => [ns, key.slice(0, -"_one".length), strings]);
  });

beforeAll(() => i18n.loadLanguages(["en"]));

test("there are English singular forms to check", () => {
  expect(singulars.length).toBeGreaterThan(50);
});

test.each(singulars.map(([ns, key, strings]) => [ns, key, strings]))("%s %s", (ns, key, strings) => {
  const base = strings[key] ?? strings[`${key}_other`];
  expect(base, `${key} needs a plural (base or _other) next to ${key}_one`).toBeTypeOf("string");
  expect(strings[`${key}_one`]).toContain("{{count}}");
  const t = i18n.getFixedT("en", ns);
  const one = t(key, { ...PARAMS, count: 1 });
  const many = t(key, { ...PARAMS, count: 3 });
  expect(one).toContain("1");
  expect(many).toContain("3");
  expect(many).not.toBe(one.replace("1", "3"));
});
