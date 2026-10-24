import { afterAll, beforeAll, expect, test } from "vitest";

import i18n from "../i18n";
import { joinClauses, joinList, joinSentence } from "./joinList";

let original;

beforeAll(async () => {
  original = i18n.language;
  await i18n.loadLanguages(["en", "ja"]);
});

afterAll(async () => {
  await i18n.changeLanguage(original);
});

test.each([
  ["zh-TW", "a、b", "已加入 3 位；找不到：x", "已加入 3 位；找不到：x。"],
  ["en", "a, b", "Added 3; not found: x", "Added 3; not found: x."],
  ["ja", "a、b", "已加入 3 位；找不到：x", "已加入 3 位；找不到：x。"],
])("%s joins lists and clauses with its own punctuation", async (lng, list, clauses, sentence) => {
  await i18n.changeLanguage(lng);
  expect(joinList(["a", "b"])).toBe(list);
  const parts = lng === "en" ? ["Added 3", "not found: x"] : ["已加入 3 位", "找不到：x"];
  expect(joinClauses(parts)).toBe(clauses);
  expect(joinSentence(parts)).toBe(sentence);
});

test("English never gets full-width punctuation", async () => {
  await i18n.changeLanguage("en");
  expect(joinSentence(["Added 3 students", "Not found: a@x.edu"])).not.toMatch(/[；。]/);
});
