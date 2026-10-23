/**
 * 官網的網址與聯繫方式，只在這裡改。
 *
 * SITE_URL／DOCS_URL 可用環境變數覆寫（Cloudflare Pages 專案的環境變數，
 * 見 website/operations/site.md），換網域時不必改程式。
 * vite.config.js 把這些值注入成 __SITE_CONFIG__ 給頁面用，scripts/prerender.mjs 直接讀。
 */
const trimSlash = (url) => url.replace(/\/+$/, "");

export const SITE_URL = trimSlash(process.env.SITE_URL || "https://skylab-tw.com");
export const DOCS_URL = trimSlash(process.env.DOCS_URL || "https://docs.skylab-tw.com");
export const CONTACT_EMAIL = "contact@skylab-tw.com";
export const GITHUB_URL = "https://github.com/ntubclass/SkyLab";
