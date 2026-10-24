import { describe, expect, test } from "vitest";
import { buildHead, buildRobots, buildSitemap } from "./seo";
import { languageFromPath } from "./languages";

const SITE = "https://example.test";

const head = (overrides = {}) =>
  buildHead({
    lang: "en",
    title: "SkyLab — Private cloud",
    description: 'Campus "private" cloud & more',
    imageAlt: "Night campus",
    siteUrl: SITE,
    contactEmail: "team@example.test",
    githubUrl: "https://github.com/example/skylab",
    ...overrides,
  });

describe("buildHead", () => {
  test("canonical 指向自己的語言頁,hreflang 列出三種語言與 x-default", () => {
    const html = head();
    expect(html).toContain(`<link rel="canonical" href="${SITE}/en/" />`);
    expect(html).toContain(`hreflang="zh-TW" href="${SITE}/"`);
    expect(html).toContain(`hreflang="en" href="${SITE}/en/"`);
    expect(html).toContain(`hreflang="ja" href="${SITE}/ja/"`);
    expect(html).toContain(`hreflang="x-default" href="${SITE}/"`);
  });

  test("Open Graph 用該語言、絕對網址的分享圖,locale 標出自己與其他語言", () => {
    const html = head({ lang: "ja" });
    expect(html).toContain(`<meta property="og:url" content="${SITE}/ja/" />`);
    expect(html).toContain(`<meta property="og:image" content="${SITE}/og-image-ja.jpg" />`);
    expect(html).toContain(`<meta name="twitter:image" content="${SITE}/og-image-ja.jpg" />`);
    expect(html).toContain('<meta property="og:locale" content="ja_JP" />');
    expect(html).toContain('<meta property="og:locale:alternate" content="zh_TW" />');
    expect(html).toContain('<meta property="og:locale:alternate" content="en_US" />');
    expect(html).not.toContain('og:locale:alternate" content="ja_JP"');
  });

  test("屬性值會轉義,不會被引號或 & 弄壞", () => {
    expect(head()).toContain('content="Campus &quot;private&quot; cloud &amp; more"');
  });

  test("JSON-LD 可以解析,而且不含會提前關閉 <script> 的字元", () => {
    const html = head({ description: "a </script><b>" });
    const json = html.match(/<script type="application\/ld\+json">(.*)<\/script>/)[1];
    expect(json).not.toContain("<");
    const data = JSON.parse(json);
    const app = data["@graph"].find((node) => node["@type"] === "SoftwareApplication");
    expect(app.description).toBe("a </script><b>");
    expect(app.author.email).toBe("team@example.test");
    expect(app.sameAs).toEqual(["https://github.com/example/skylab"]);
  });

  test("不支援的語言直接報錯", () => {
    expect(() => head({ lang: "fr" })).toThrow("Unsupported language: fr");
  });
});

test("sitemap 每個語言一筆,各自帶齊所有語言的對應網址", () => {
  const xml = buildSitemap(SITE, "2026-10-23");
  const urls = xml.match(/<url>[\s\S]*?<\/url>/g);
  expect(urls).toHaveLength(3);
  expect(urls.map((u) => u.match(/<loc>(.*)<\/loc>/)[1])).toEqual([`${SITE}/`, `${SITE}/en/`, `${SITE}/ja/`]);
  for (const url of urls) {
    expect(url.match(/<xhtml:link /g)).toHaveLength(4);
    expect(url).toContain("<lastmod>2026-10-23</lastmod>");
  }
});

test("robots.txt 全站開放並指向 sitemap", () => {
  expect(buildRobots(SITE)).toBe(`User-agent: *\nAllow: /\n\nSitemap: ${SITE}/sitemap.xml\n`);
});

test.each([
  ["/", "zh-TW"],
  ["/en/", "en"],
  ["/ja", "ja"],
  ["/ja/index.html", "ja"],
  ["/zh-TW/", "zh-TW"],
  ["/somewhere/", "zh-TW"],
])("網址 %s 的語言是 %s", (pathname, lang) => {
  expect(languageFromPath(pathname)).toBe(lang);
});
