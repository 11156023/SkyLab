/* 單一來源是 site.config.js；vite.config.js 建置時把它注入成 __SITE_CONFIG__ */
/* global __SITE_CONFIG__ */
export const {
  siteUrl: SITE_URL,
  docsUrl: DOCS_URL,
  contactEmail: CONTACT_EMAIL,
  githubUrl: GITHUB_URL,
} = __SITE_CONFIG__;
