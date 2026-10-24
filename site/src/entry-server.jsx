/**
 * 預渲染入口（vite build --ssr）：scripts/prerender.mjs 對每個語言呼叫 render()，
 * 把頁面 HTML 與 SEO 標籤寫進該語言的 index.html。
 */
import { renderToString } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
import LandingPage from "./landing/LandingPage";
import { createI18n } from "./i18n";
import { CONTACT_EMAIL, GITHUB_URL, SITE_URL } from "./config";
import { DEFAULT_LANGUAGE } from "./languages";
import { languageRedirectScript } from "./languagePreference";
import { buildHead } from "./seo";

export { LANGUAGES } from "./languages";
export { buildRobots, buildSitemap } from "./seo";

export function render(lang) {
  const i18n = createI18n(lang);
  const t = i18n.getFixedT(lang, "landing");
  const html = renderToString(
    <I18nextProvider i18n={i18n}>
      <LandingPage />
    </I18nextProvider>,
  );
  const head = buildHead({
    lang,
    title: t("seo.title"),
    description: t("seo.description"),
    imageAlt: t("seo.imageAlt"),
    siteUrl: SITE_URL,
    contactEmail: CONTACT_EMAIL,
    githubUrl: GITHUB_URL,
  });
  // 根網址依瀏覽器語言轉到 /en/ 或 /ja/（languagePreference.js）
  if (lang === DEFAULT_LANGUAGE) return { html, head: `${head}\n    <script>${languageRedirectScript()}</script>` };
  return { html, head };
}
