// @vitest-environment happy-dom
import { afterEach, describe, expect, test, vi } from "vitest";
import { DEFAULT_LANGUAGE, LANGUAGE_STORAGE_KEY, detectLanguage } from "./index";

describe("detectLanguage", () => {
  test.each([
    [["zh-TW", "en"], "zh-TW"],
    [["zh-CN"], "zh-TW"],
    [["zh-Hant-HK"], "zh-TW"],
    [["ja-JP", "zh-TW"], "ja"],
    [["en-US", "zh-TW"], "en"],
    [["ko-KR", "ja"], "ja"],
    [["fr-FR", "ko"], "en"],
  ])("瀏覽器偏好 %j → %s", (languages, expected) => {
    expect(detectLanguage(languages)).toBe(expected);
  });

  test("完全讀不到偏好時用預設語言", () => {
    expect(detectLanguage([])).toBe(DEFAULT_LANGUAGE);
  });
});

describe("初始語言", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
    localStorage.removeItem(LANGUAGE_STORAGE_KEY);
  });

  async function startWith(languages) {
    vi.resetModules();
    vi.stubGlobal("navigator", { ...navigator, languages, language: languages[0] });
    const { default: i18n, i18nReady } = await import("./index");
    await i18nReady;
    return i18n;
  }

  test("沒選過語言時跟著瀏覽器，<html lang> 一起改", async () => {
    const i18n = await startWith(["ja-JP", "en"]);
    expect(i18n.language).toBe("ja");
    expect(document.documentElement.lang).toBe("ja");
  });

  test("在語言選單選過的語言優先於瀏覽器偏好", async () => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-TW");
    const i18n = await startWith(["en-US"]);
    expect(i18n.language).toBe("zh-TW");
  });
});
