/**
 * 預渲染用的 SEO 產生器（純函式）：每個語言頁的 <head> 標籤、sitemap.xml、robots.txt。
 * 網址一律是絕對網址，來源是 site.config.js 的 SITE_URL。
 */
import { DEFAULT_LANGUAGE, LANGUAGES, languageInfo } from "./languages";

const OG_IMAGE_SIZE = { width: 1200, height: 630 };

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function pageUrl(siteUrl, code) {
  return `${siteUrl}${languageInfo(code).path}`;
}

function structuredData({ lang, description, siteUrl, contactEmail, githubUrl }) {
  const data = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${siteUrl}/#website`,
        url: `${siteUrl}/`,
        name: "SkyLab",
        inLanguage: LANGUAGES.map((l) => l.code),
      },
      {
        "@type": "SoftwareApplication",
        name: "SkyLab",
        description,
        url: pageUrl(siteUrl, lang),
        inLanguage: lang,
        applicationCategory: "EducationalApplication",
        operatingSystem: "Linux",
        license: "https://www.gnu.org/licenses/agpl-3.0.html",
        sameAs: [githubUrl],
        author: { "@type": "Organization", name: "SkyLab", url: `${siteUrl}/`, email: contactEmail },
      },
    ],
  };
  // 內容放進 <script>，"<" 一律轉義，避免 "</script>" 提前結束標籤
  return JSON.stringify(data).replace(/</g, "\\u003c");
}

/** 一個語言頁的 SEO 標籤（title、description、canonical、hreflang、Open Graph、Twitter、JSON-LD） */
export function buildHead({ lang, title, description, imageAlt, siteUrl, contactEmail, githubUrl }) {
  const url = pageUrl(siteUrl, lang);
  const current = languageInfo(lang);
  const image = `${siteUrl}${current.ogImage}`;
  const meta = (attr, key, content) => `<meta ${attr}="${key}" content="${escapeHtml(content)}" />`;

  return [
    `<title>${escapeHtml(title)}</title>`,
    meta("name", "description", description),
    `<link rel="canonical" href="${url}" />`,
    ...LANGUAGES.map((l) => `<link rel="alternate" hreflang="${l.code}" href="${pageUrl(siteUrl, l.code)}" />`),
    `<link rel="alternate" hreflang="x-default" href="${pageUrl(siteUrl, DEFAULT_LANGUAGE)}" />`,
    meta("property", "og:type", "website"),
    meta("property", "og:site_name", "SkyLab"),
    meta("property", "og:title", title),
    meta("property", "og:description", description),
    meta("property", "og:url", url),
    meta("property", "og:image", image),
    meta("property", "og:image:width", OG_IMAGE_SIZE.width),
    meta("property", "og:image:height", OG_IMAGE_SIZE.height),
    meta("property", "og:image:alt", imageAlt),
    meta("property", "og:locale", current.ogLocale),
    ...LANGUAGES.filter((l) => l.code !== lang).map((l) => meta("property", "og:locale:alternate", l.ogLocale)),
    meta("name", "twitter:card", "summary_large_image"),
    meta("name", "twitter:title", title),
    meta("name", "twitter:description", description),
    meta("name", "twitter:image", image),
    `<script type="application/ld+json">${structuredData({ lang, description, siteUrl, contactEmail, githubUrl })}</script>`,
  ].join("\n    ");
}

/** 每個語言一筆 <url>，各自列出全部語言的對應網址（Google 對 sitemap hreflang 的要求） */
export function buildSitemap(siteUrl, lastmod) {
  const alternates = [
    ...LANGUAGES.map((l) => `    <xhtml:link rel="alternate" hreflang="${l.code}" href="${pageUrl(siteUrl, l.code)}" />`),
    `    <xhtml:link rel="alternate" hreflang="x-default" href="${pageUrl(siteUrl, DEFAULT_LANGUAGE)}" />`,
  ].join("\n");
  const urls = LANGUAGES.map(
    (l) => `  <url>\n    <loc>${pageUrl(siteUrl, l.code)}</loc>\n    <lastmod>${lastmod}</lastmod>\n${alternates}\n  </url>`,
  ).join("\n");
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    urls,
    "</urlset>",
    "",
  ].join("\n");
}

export function buildRobots(siteUrl) {
  return `User-agent: *\nAllow: /\n\nSitemap: ${siteUrl}/sitemap.xml\n`;
}
