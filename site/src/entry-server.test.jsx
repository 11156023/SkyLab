/* 預渲染實際跑在 Node 上、載入真的 gsap:這裡不 mock,確保 build 時的 SSR 能畫出每個語言 */
import { existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, test } from "vitest";
import { LANGUAGES, render } from "./entry-server";
import { CONTACT_EMAIL } from "./config";
import zhTW from "./locales/zh-TW.json";
import en from "./locales/en.json";
import ja from "./locales/ja.json";

describe.each([
  ["zh-TW", "SkyLab — 校園私有雲管理平台", "聯繫我們"],
  ["en", "SkyLab — Private cloud platform for campuses", "Contact us"],
  ["ja", "SkyLab — キャンパス向けプライベートクラウド基盤", "お問い合わせ"],
])("render(%s)", (lang, title, contact) => {
  test("頁面內容與 SEO 標籤都是該語言", () => {
    const { html, head } = render(lang);
    expect(head).toContain(`<title>${title}</title>`);
    expect(html).toContain(contact);
    expect(html).toContain(CONTACT_EMAIL);
  });
});

test("三個語言都有預渲染,分享縮圖也都在 public/", () => {
  expect(LANGUAGES.map((l) => l.code)).toEqual(["zh-TW", "en", "ja"]);
  for (const { ogImage } of LANGUAGES) {
    expect(existsSync(path.join(import.meta.dirname, "..", "public", ogImage)), ogImage).toBe(true);
  }
});

/* 三語要一起改:任何一個語言少了 key,畫面就會露出 key 名稱 */
test("三個語系檔的 key 完全一致", () => {
  const keys = (obj, prefix = "") =>
    Object.entries(obj).flatMap(([k, v]) =>
      v && typeof v === "object" && !Array.isArray(v) ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`],
    );
  expect(keys(en).sort()).toEqual(keys(zhTW).sort());
  expect(keys(ja).sort()).toEqual(keys(zhTW).sort());
});
