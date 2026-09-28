import { describe, expect, test } from "vitest";
import { DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, currentLanguage } from "./index";

describe("currentLanguage", () => {
  test("支援的語系原樣回傳", () => {
    for (const lang of SUPPORTED_LANGUAGES) expect(currentLanguage(lang)).toBe(lang);
  });

  test("不支援或空值退回預設語言", () => {
    expect(currentLanguage("fr")).toBe(DEFAULT_LANGUAGE);
    expect(currentLanguage("zh")).toBe(DEFAULT_LANGUAGE);
    expect(currentLanguage("")).toBe(DEFAULT_LANGUAGE);
    expect(currentLanguage(null)).toBe(DEFAULT_LANGUAGE);
  });

  test("不帶參數時看 i18n 目前的語言，結果一定在支援清單內", () => {
    expect(SUPPORTED_LANGUAGES).toContain(currentLanguage());
  });
});
