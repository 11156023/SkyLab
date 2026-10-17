import { defineConfig, type DefaultTheme } from 'vitepress'

// The documentation site is built from two sources:
//   - ../docs/*.md (and *.zh-TW.md) copied by scripts/sync-docs.mjs into
//     guides/ and zh-TW/guides/ (both directories are gitignored);
//   - the pages written directly in this directory (index, getting started,
//     concepts, reference, troubleshooting).
// Keep the sidebars below in sync with ../docs/README.md.

const REPO = 'https://github.com/ntubclass/SkyLab'

function guides(prefix: string, labels: Record<string, string>): DefaultTheme.SidebarItem[] {
  return Object.entries(labels).map(([file, text]) => ({ text, link: `${prefix}/guides/${file}` }))
}

const guideFilesEn = {
  development: 'Development',
  deployment: 'Deployment',
  monitoring: 'Monitoring',
  'registration-and-google-login': 'Registration and Google login',
  'ai-api-user-manual': 'AI API user manual',
  'wireguard-desktop-architecture': 'WireGuard and SkyLab Connect',
  'multi-machine-environment-sop': 'Multi-machine environment SOP',
  'ai-navigation-teaching-workflows': 'AI navigation in teaching workflows',
  'AI導師檢查流程與不可破壞契約': 'AI Teacher Judge workflow (繁體中文)',
  'frontend-style-guide': 'Frontend style guide',
  'database-design': 'Database design',
  'user-guide-content-audit': 'Page guide content audit (繁體中文)',
}

const guideFilesZh = {
  development: '開發指南',
  deployment: '部署指南',
  monitoring: '系統監控',
  'registration-and-google-login': '註冊設定與 Google 登入',
  'ai-api-user-manual': 'AI API 使用與部署手冊',
  'wireguard-desktop-architecture': 'WireGuard 與 SkyLab Connect',
  'multi-machine-environment-sop': '多機教學環境 SOP',
  'ai-navigation-teaching-workflows': '導覽 AI 與教學流程',
  'AI導師檢查流程與不可破壞契約': 'AI 導師檢查流程與不可破壞契約',
  'frontend-style-guide': '前端樣式規範',
  'database-design': '資料庫設計與正規化',
  'user-guide-content-audit': '頁面導覽文案盤點',
}

const archiveFiles = {
  '2026-09-09-ui-consistency-audit': '2026-09-09 UI consistency audit',
  '2026-10-03-ai-api-concurrency-analysis': '2026-10-03 AI API concurrency analysis',
  '2026-10-06-ai-page-guide': '2026-10-06 AI page guide (繁體中文)',
}

const sidebarEn: DefaultTheme.Sidebar = [
  {
    text: 'Introduction',
    items: [
      { text: 'Getting started', link: '/getting-started' },
      { text: 'Concepts and architecture', link: '/concepts' },
      { text: 'Troubleshooting', link: '/troubleshooting' },
    ],
  },
  {
    text: 'Guides',
    items: [{ text: 'Documentation index', link: '/guides/' }, ...guides('', guideFilesEn)],
  },
  {
    text: 'Reference',
    items: [
      { text: 'REST API', link: '/reference/api' },
      { text: 'WebSocket endpoints', link: '/reference/websocket' },
      { text: 'AI API (OpenAI compatible)', link: '/guides/ai-api-user-manual#_6-requesting-and-calling-the-api-as-a-regular-user' },
    ],
  },
  {
    text: 'Archive',
    collapsed: true,
    items: Object.entries(archiveFiles).map(([file, text]) => ({ text, link: `/guides/archive/${file}` })),
  },
  {
    text: 'Project',
    items: [
      { text: 'Contributing', link: `${REPO}/blob/main/CONTRIBUTING.md` },
      { text: 'Security policy', link: `${REPO}/blob/main/SECURITY.md` },
      { text: 'Code of conduct', link: `${REPO}/blob/main/CODE_OF_CONDUCT.md` },
      { text: 'License (AGPL-3.0)', link: `${REPO}/blob/main/LICENSE` },
      { text: 'Third-party notices', link: `${REPO}/blob/main/THIRD_PARTY_NOTICES.md` },
    ],
  },
]

const sidebarZh: DefaultTheme.Sidebar = [
  {
    text: '入門',
    items: [
      { text: '快速開始', link: '/zh-TW/getting-started' },
      { text: '概念與架構', link: '/zh-TW/concepts' },
      { text: '疑難排解', link: '/zh-TW/troubleshooting' },
    ],
  },
  {
    text: '指南',
    items: [{ text: '文件索引', link: '/zh-TW/guides/' }, ...guides('/zh-TW', guideFilesZh)],
  },
  {
    text: '參考',
    items: [
      { text: 'REST API', link: '/zh-TW/reference/api' },
      { text: 'WebSocket 端點', link: '/zh-TW/reference/websocket' },
      { text: 'AI API（OpenAI 相容）', link: '/zh-TW/guides/ai-api-user-manual#_6-一般使用者申請與呼叫-api' },
    ],
  },
  {
    text: '歷史報告',
    collapsed: true,
    items: Object.entries(archiveFiles).map(([file, text]) => ({ text, link: `/zh-TW/guides/archive/${file}` })),
  },
  {
    text: '專案',
    items: [
      { text: '貢獻指南', link: `${REPO}/blob/main/CONTRIBUTING.md` },
      { text: '安全政策', link: `${REPO}/blob/main/SECURITY.md` },
      { text: '行為準則', link: `${REPO}/blob/main/CODE_OF_CONDUCT.md` },
      { text: '授權（AGPL-3.0）', link: `${REPO}/blob/main/LICENSE` },
      { text: '第三方元件授權', link: `${REPO}/blob/main/THIRD_PARTY_NOTICES.md` },
    ],
  },
]

export default defineConfig({
  title: 'SkyLab',
  description: 'A self-hosted cloud for schools built on Proxmox VE.',
  cleanUrls: true,
  // The READMEs describe this directory for developers; they are not pages.
  srcExclude: ['README.md', 'README.zh-TW.md'],
  // The guides link to local development URLs; those are not pages of this site.
  ignoreDeadLinks: [/^https?:\/\/localhost/, /^https?:\/\/127\.0\.0\.1/],
  // Synced guides are not tracked by git, so "last updated" would be empty for them.
  lastUpdated: false,
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    ['meta', { name: 'theme-color', content: '#5471bf' }],
  ],
  markdown: {
    // ```mermaid fences are rendered in the browser by theme/Mermaid.vue.
    config(md) {
      // Inline code such as `{{.SecurityOptions}}` must not be read as a Vue
      // interpolation (fenced blocks are already protected by VitePress).
      const codeInline = md.renderer.rules.code_inline!
      md.renderer.rules.code_inline = (tokens, idx, options, env, self) =>
        codeInline(tokens, idx, options, env, self).replace(/^<code/, '<code v-pre')
      const fence = md.renderer.rules.fence!
      md.renderer.rules.fence = (tokens, idx, options, env, self) => {
        const token = tokens[idx]
        if (token.info.trim() === 'mermaid') {
          return `<Mermaid code="${encodeURIComponent(token.content)}" />`
        }
        return fence(tokens, idx, options, env, self)
      }
    },
  },
  locales: {
    root: {
      label: 'English',
      lang: 'en',
      themeConfig: {
        nav: [
          { text: 'Getting started', link: '/getting-started' },
          { text: 'Guides', link: '/guides/' },
          { text: 'Reference', link: '/reference/api' },
        ],
        sidebar: sidebarEn,
        editLink: {
          pattern: editLinkPattern,
          text: 'Edit this page on GitHub',
        },
        outline: { level: [2, 3], label: 'On this page' },
        docFooter: { prev: 'Previous', next: 'Next' },
        footer: {
          message: 'Released under the AGPL-3.0 License.',
          copyright: 'SkyLab contributors',
        },
      },
    },
    'zh-TW': {
      label: '繁體中文',
      lang: 'zh-TW',
      link: '/zh-TW/',
      themeConfig: {
        nav: [
          { text: '快速開始', link: '/zh-TW/getting-started' },
          { text: '指南', link: '/zh-TW/guides/' },
          { text: '參考', link: '/zh-TW/reference/api' },
        ],
        sidebar: sidebarZh,
        editLink: {
          pattern: editLinkPattern,
          text: '在 GitHub 上編輯此頁',
        },
        outline: { level: [2, 3], label: '本頁目錄' },
        docFooter: { prev: '上一頁', next: '下一頁' },
        footer: {
          message: '以 AGPL-3.0 授權釋出。',
          copyright: 'SkyLab contributors',
        },
        sidebarMenuLabel: '選單',
        returnToTopLabel: '回到頂部',
        darkModeSwitchLabel: '外觀',
        langMenuLabel: '語言',
      },
    },
  },
  themeConfig: {
    logo: '/favicon.svg',
    socialLinks: [{ icon: 'github', link: REPO }],
    search: { provider: 'local' },
  },
})

// Synced guides carry a `source` frontmatter pointing at the file under docs/;
// pages authored in this directory are edited in website/. The function is
// serialised into the client bundle, so it must not reference module constants.
function editLinkPattern({ filePath, frontmatter }: { filePath: string; frontmatter: Record<string, unknown> }): string {
  const source = typeof frontmatter.source === 'string' ? frontmatter.source : `website/${filePath}`
  return `https://github.com/ntubclass/SkyLab/edit/main/${source}`
}
