/**
 * 官網的三個語言版本。每個語言是一個預渲染好的網址（搜尋引擎靠 hreflang 對應），
 * 切換語言就是換頁，不存 localStorage。
 * ogImage：分享縮圖（1200×630 的開場畫面，各語言一張，放在 public/）。
 * docsPath：文件站對應語言的根路徑（文件站沒有日文版，日文連英文）。
 */
export const LANGUAGES = [
  { code: "zh-TW", path: "/", label: "中", ogLocale: "zh_TW", ogImage: "/og-image.jpg", docsPath: "/zh-TW/" },
  { code: "en", path: "/en/", label: "EN", ogLocale: "en_US", ogImage: "/og-image-en.jpg", docsPath: "/" },
  { code: "ja", path: "/ja/", label: "日", ogLocale: "ja_JP", ogImage: "/og-image-ja.jpg", docsPath: "/" },
];

export const DEFAULT_LANGUAGE = "zh-TW";

export function languageInfo(code) {
  const info = LANGUAGES.find((lang) => lang.code === code);
  if (!info) throw new Error(`Unsupported language: ${code}`);
  return info;
}

/** 網址的第一段是 en／ja 就是該語言，其餘（含根目錄）都是預設的繁體中文 */
export function languageFromPath(pathname) {
  const segment = pathname.split("/")[1];
  const match = LANGUAGES.find((lang) => lang.code !== DEFAULT_LANGUAGE && lang.code === segment);
  return match ? match.code : DEFAULT_LANGUAGE;
}
