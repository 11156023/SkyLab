import { describe, expect, test, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, languageRedirectScript } from "./languagePreference";

/** 在假的瀏覽器裡跑根網址的轉址腳本，回傳它轉去的網址（沒轉回 null） */
function run({ languages = [], stored = null, storageThrows = false, userAgent = "Mozilla/5.0 Chrome/140", search = "", hash = "" }) {
  const replace = vi.fn();
  const navigator = { userAgent, languages, language: languages[0] };
  const localStorage = {
    getItem: (key) => {
      if (storageThrows) throw new Error("blocked");
      return key === LANGUAGE_STORAGE_KEY ? stored : null;
    },
  };
  const location = { search, hash, replace };
  new Function("navigator", "localStorage", "location", languageRedirectScript())(navigator, localStorage, location);
  return replace.mock.calls.length ? replace.mock.calls[0][0] : null;
}

describe("根網址依瀏覽器語言轉頁", () => {
  test.each([
    [["ja-JP", "en"], "/ja/"],
    [["en-US", "zh-TW"], "/en/"],
    [["ko-KR", "ja"], "/ja/"],
    [["fr-FR", "ko"], "/en/"],
  ])("偏好 %j 轉到 %s", (languages, target) => {
    expect(run({ languages })).toBe(target);
  });

  test.each([[["zh-TW", "en"]], [["zh-CN"]], [["zh-Hant-HK", "ja"]], [[]]])("偏好 %j 留在中文", (languages) => {
    expect(run({ languages })).toBeNull();
  });

  test("點過的語言優先於瀏覽器偏好", () => {
    expect(run({ languages: ["en-US"], stored: "zh-TW" })).toBeNull();
    expect(run({ languages: ["zh-TW"], stored: "ja" })).toBe("/ja/");
  });

  test("記錄的值不是支援的語言時當作沒選過", () => {
    expect(run({ languages: ["zh-TW"], stored: "fr" })).toBeNull();
    expect(run({ languages: ["en"], stored: "fr" })).toBe("/en/");
  });

  test("localStorage 不能用時仍依瀏覽器偏好", () => {
    expect(run({ languages: ["en"], storageThrows: true })).toBe("/en/");
  });

  test("搜尋引擎爬蟲不轉，每個語言都爬得到", () => {
    expect(run({ languages: ["ja"], userAgent: "Mozilla/5.0 (compatible; Googlebot/2.1)" })).toBeNull();
  });

  test("保留查詢字串與錨點", () => {
    expect(run({ languages: ["en"], search: "?utm_source=x", hash: "#contact" })).toBe("/en/?utm_source=x#contact");
  });
});
