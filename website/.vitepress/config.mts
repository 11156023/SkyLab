import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { defineConfig, type DefaultTheme, type HeadConfig } from 'vitepress'
import { languageRedirectScript } from './theme/languagePreference'

// All documentation of the repository lives in this directory:
//   guides/, operations/, reference/, development/, manual/, project/  (English)
//   zh-TW/<same>                                                        (繁體中文)
// Pages that are generated at build time (environment variables, changelog)
// are produced by scripts/generate.mjs; the REST API page renders
// public/openapi.json exported by backend/scripts/export_openapi.py.

const REPO = 'https://github.com/SkyLab-tw/SkyLab'

// Production address: canonical URLs, hreflang, sitemap.xml, robots.txt and share cards.
// DOCS_URL overrides it (an environment variable of the Cloudflare Pages project).
const SITE_URL = (process.env.DOCS_URL || 'https://docs.skylab-tw.com').replace(/\/+$/, '')

// ── SEO helpers ──────────────────────────────────────────────────────────────

const ZH = 'zh-TW/'

/** Path of a source file: guides/x.md → /guides/x, manual/index.md → /manual/ */
function pagePath(relativePath: string): string {
  return `/${relativePath.replace(/(^|\/)index\.md$/, '$1').replace(/\.md$/, '')}`
}

function pageUrl(relativePath: string): string {
  return `${SITE_URL}${pagePath(relativePath)}`
}

/** The same page in the other language, when it exists (hreflang must point both ways). */
function counterpart(relativePath: string, pages: string[]): string | undefined {
  const other = relativePath.startsWith(ZH) ? relativePath.slice(ZH.length) : ZH + relativePath
  return pages.includes(other) ? other : undefined
}

// Search results show about 160 Latin characters, or about 80–90 CJK characters.
const DESCRIPTION_LENGTH = { en: 160, 'zh-TW': 90 }

/** The first prose paragraph of a page as plain text, or undefined when it has none (e.g. the home page). */
function firstParagraph(source: string): string | undefined {
  const lines = source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').split(/\r?\n/)
  const paragraph: string[] = []
  let inFence = false
  for (const raw of lines) {
    const line = raw.trim()
    if (/^(```|~~~)/.test(line)) inFence = !inFence
    // Headings, tables, lists, quotes, containers, images, badges, includes and HTML end or skip a paragraph.
    const prose = !inFence && line !== '' && !/^(```|~~~|#|\||<|>|:::|!\[|\[!\[|[-*+]\s|\d+\.\s|-{3,}|\[\[toc\]\])/.test(line)
    if (prose) paragraph.push(line)
    else if (paragraph.length) break
  }
  if (!paragraph.length) return undefined
  return paragraph
    .join(' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  const cut = text.slice(0, max - 1)
  const space = cut.lastIndexOf(' ')
  return `${space > max * 0.6 ? cut.slice(0, space) : cut}…`
}

type Labels = Record<string, string>

function items(prefix: string, labels: Labels): DefaultTheme.SidebarItem[] {
  return Object.entries(labels).map(([slug, text]) => ({ text, link: `${prefix}${slug}` }))
}

// ── Sidebar contents (keep both languages in the same order) ─────────────────

const manualStudents = {
  en: {
    login: 'Signing in',
    home: 'Home page',
    account: 'Account settings',
    'my-requests': 'Requesting a machine',
    'my-resources': 'My resources',
    'resource-detail': 'Machine details and console',
    firewall: 'Firewall',
    'quick-practice': 'Quick practice',
    courses: 'Courses',
    'ai-api': 'AI API credentials',
  },
  'zh-TW': {
    login: '登入',
    home: '首頁',
    account: '帳號設定',
    'my-requests': '申請機器',
    'my-resources': '我的資源',
    'resource-detail': '機器詳情與主控台',
    firewall: '防火牆',
    'quick-practice': '快速練習',
    courses: '課程',
    'ai-api': 'AI API 憑證',
  },
}

const manualTeachers = {
  en: {
    templates: 'Templates',
    'class-setup': 'Creating a class',
    'class-management': 'Class list',
    'class-workspace': 'Class workspace',
    'ai-judge': 'AI-assisted checking',
    'course-templates': 'Multi-machine environments',
    'course-cms': 'Course content',
  },
  'zh-TW': {
    templates: '範本',
    'class-setup': '建立班級',
    'class-management': '班級清單',
    'class-workspace': '班級工作頁',
    'ai-judge': 'AI 導師檢查',
    'course-templates': '多機教學環境',
    'course-cms': '課程內容',
  },
}

const manualAdmins = {
  en: {
    'admin-users': 'Users and registration',
    'pve-connections': 'PVE connections',
    'ip-management': 'IP management',
    gateway: 'Gateway VM',
    domain: 'Domains',
    scheduler: 'Scheduler',
    governance: 'Governance',
    ldap: 'LDAP / Active Directory',
    storage: 'Storage',
    'gpu-management': 'GPU management',
    'resource-management': 'Resource management',
    'request-review': 'Request review',
    'batch-review': 'Batch review',
    monitoring: 'Monitoring and alerts',
    jobs: 'Background jobs',
    audit: 'Audit log',
    'ai-api-review': 'AI API review',
    'ai-api-keys': 'AI API keys',
    'ai-monitoring': 'AI monitoring',
  },
  'zh-TW': {
    'admin-users': '使用者與註冊',
    'pve-connections': 'PVE 連線',
    'ip-management': 'IP 管理',
    gateway: '閘道 VM',
    domain: '網域管理',
    scheduler: '排程器',
    governance: '治理',
    ldap: 'LDAP／Active Directory',
    storage: '儲存',
    'gpu-management': 'GPU 管理',
    'resource-management': '資源管理',
    'request-review': '申請審核',
    'batch-review': '批次審核',
    monitoring: '監控與告警',
    jobs: '背景任務',
    audit: '稽核日誌',
    'ai-api-review': 'AI API 審核',
    'ai-api-keys': 'AI API 金鑰',
    'ai-monitoring': 'AI 監控',
  },
}

const operations = {
  en: {
    'guides/deployment': 'Deployment',
    'operations/upgrade': 'Upgrading and migrations',
    'operations/backup': 'Backup and restore',
    'operations/environment': 'Environment variables',
    'operations/site': 'Public site',
    'guides/monitoring': 'Monitoring',
    'guides/registration-and-google-login': 'Registration and Google login',
    'guides/ai-api-user-manual': 'AI API: LiteLLM and vLLM',
    'guides/wireguard-desktop-architecture': 'WireGuard and SkyLab Connect',
    'guides/vllm-service': 'vLLM service',
    'guides/vllm-sharegpt-quickstart': 'vLLM service: ShareGPT benchmark',
    'guides/litellm': 'LiteLLM gateway',
  },
  'zh-TW': {
    'guides/deployment': '部署指南',
    'operations/upgrade': '升級與 migration',
    'operations/backup': '備份與還原',
    'operations/environment': '環境變數對照表',
    'operations/site': '官網',
    'guides/monitoring': '系統監控',
    'guides/registration-and-google-login': '註冊設定與 Google 登入',
    'guides/ai-api-user-manual': 'AI API：LiteLLM 與 vLLM',
    'guides/wireguard-desktop-architecture': 'WireGuard 與 SkyLab Connect',
    'guides/vllm-service': 'vLLM 服務',
    'guides/vllm-sharegpt-quickstart': 'vLLM 服務：ShareGPT 基準測試',
    'guides/litellm': 'LiteLLM 閘道',
  },
}

const development = {
  en: {
    'guides/development': 'Development environment',
    'guides/backend': 'Backend architecture',
    'guides/database-design': 'Database design',
    'guides/frontend-style-guide': 'Frontend style guide',
    'development/i18n': 'Internationalisation',
  },
  'zh-TW': {
    'guides/development': '開發環境',
    'guides/backend': '後端架構',
    'guides/database-design': '資料庫設計',
    'guides/frontend-style-guide': '前端樣式規範',
    'development/i18n': '多語系',
  },
}

const design = {
  en: {
    'guides/multi-machine-environment-sop': 'Multi-machine environment SOP',
    'guides/ai-navigation-teaching-workflows': 'AI navigation in teaching workflows',
  },
  'zh-TW': {
    'guides/multi-machine-environment-sop': '多機教學環境 SOP',
    'guides/ai-navigation-teaching-workflows': '導覽 AI 與教學流程',
    'guides/ai-teacher-judge-contract': 'AI 導師檢查流程與不可破壞契約',
    'guides/user-guide-content-audit': '頁面導覽文案盤點',
  },
}

function sidebar(locale: 'en' | 'zh-TW'): DefaultTheme.Sidebar {
  const zh = locale === 'zh-TW'
  const p = zh ? '/zh-TW/' : '/'
  return [
    {
      text: zh ? '入門' : 'Introduction',
      items: [
        { text: zh ? '快速開始' : 'Getting started', link: `${p}getting-started` },
        { text: zh ? '概念與架構' : 'Concepts and architecture', link: `${p}concepts` },
        zh
          ? { text: '專案總覽', link: '/zh-TW/overview' }
          : { text: 'Project overview (README)', link: `${REPO}#readme` },
      ],
    },
    {
      text: zh ? '使用手冊' : 'User manual',
      items: [
        { text: zh ? '手冊總覽' : 'Overview', link: `${p}manual/` },
        { text: zh ? '學生' : 'Students', collapsed: false, items: items(`${p}manual/`, manualStudents[locale]) },
        { text: zh ? '老師' : 'Teachers', collapsed: false, items: items(`${p}manual/`, manualTeachers[locale]) },
        { text: zh ? '管理員' : 'Administrators', collapsed: false, items: items(`${p}manual/`, manualAdmins[locale]) },
      ],
    },
    {
      text: zh ? '維運' : 'Operations',
      collapsed: true,
      items: items(p, operations[locale]),
    },
    {
      text: zh ? '參考' : 'Reference',
      collapsed: true,
      items: [
        { text: 'REST API', link: `${p}reference/api` },
        { text: zh ? 'WebSocket 端點' : 'WebSocket endpoints', link: `${p}reference/websocket` },
        {
          text: zh ? 'AI API（OpenAI 相容）' : 'AI API (OpenAI compatible)',
          link: zh
            ? '/zh-TW/guides/ai-api-user-manual#_6-一般使用者申請與呼叫-api'
            : '/guides/ai-api-user-manual#_6-requesting-and-calling-the-api-as-a-regular-user',
        },
        { text: zh ? '治理設定欄位' : 'Governance configuration', link: `${p}reference/governance-config` },
        { text: zh ? '登入帳號命名政策' : 'Username policy', link: `${p}reference/username-policy` },
      ],
    },
    {
      text: zh ? '開發' : 'Development',
      collapsed: true,
      items: [
        ...items(p, development[locale]),
        { text: zh ? '貢獻指南與 DCO' : 'Contributing and DCO', link: `${REPO}/blob/main/CONTRIBUTING${zh ? '.zh-TW' : ''}.md` },
      ],
    },
    {
      text: zh ? '產品與設計' : 'Product and design',
      collapsed: true,
      items: [
        ...items(p, design[locale]),
        ...(zh
          ? []
          : [
              { text: 'AI Teacher Judge contract (繁體中文)', link: '/zh-TW/guides/ai-teacher-judge-contract' },
              { text: 'Page guide content audit (繁體中文)', link: '/zh-TW/guides/user-guide-content-audit' },
            ]),
      ],
    },
    { text: zh ? '疑難排解' : 'Troubleshooting', link: `${p}troubleshooting` },
    {
      text: zh ? '專案' : 'Project',
      collapsed: true,
      items: [
        { text: zh ? '更新紀錄' : 'Changelog', link: `${p}changelog` },
        { text: zh ? '授權' : 'License', link: `${p}project/license` },
        { text: zh ? '貢獻指南' : 'Contributing', link: `${REPO}/blob/main/CONTRIBUTING${zh ? '.zh-TW' : ''}.md` },
        { text: zh ? '安全政策' : 'Security policy', link: `${REPO}/blob/main/SECURITY${zh ? '.zh-TW' : ''}.md` },
        { text: zh ? '行為準則' : 'Code of conduct', link: `${REPO}/blob/main/CODE_OF_CONDUCT${zh ? '.zh-TW' : ''}.md` },
        { text: zh ? '第三方元件授權' : 'Third-party notices', link: `${REPO}/blob/main/THIRD_PARTY_NOTICES.md` },
      ],
    },
  ]
}

export default defineConfig({
  title: 'SkyLab',
  description: 'A self-hosted cloud for schools built on Proxmox VE.',
  cleanUrls: true,
  srcExclude: ['README.md', 'README.zh-TW.md'],
  // The guides link to local development URLs; those are not pages of this site.
  ignoreDeadLinks: [/^https?:\/\/localhost/, /^https?:\/\/127\.0\.0\.1/],
  lastUpdated: true,
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    ['meta', { name: 'theme-color', content: '#5471bf' }],
  ],
  // The sitemap pairs every English page with its 繁體中文 version as hreflang alternates.
  sitemap: {
    hostname: `${SITE_URL}/`,
    transformItems: (items) =>
      items.map((item) => {
        const english = item.links?.find((link) => link.lang === 'en')
        return english ? { ...item, links: [...item.links!, { lang: 'x-default', url: english.url }] } : item
      }),
  },
  // Pages have no description front matter; use their first paragraph so search results
  // and share cards describe the page instead of repeating the site tagline.
  transformPageData(pageData, { siteConfig }) {
    if (pageData.frontmatter.description || pageData.isNotFound) return
    const source = readFileSync(path.join(siteConfig.srcDir, pageData.relativePath), 'utf8')
    // The home pages describe themselves in the hero tagline.
    const text = pageData.frontmatter.hero?.tagline ?? firstParagraph(source)
    if (!text) return
    const lang = pageData.relativePath.startsWith(ZH) ? 'zh-TW' : 'en'
    return { description: truncate(text, DESCRIPTION_LENGTH[lang]) }
  },
  transformHead({ page, pageData, siteConfig, title, description }) {
    if (page === '404.md') return [['meta', { name: 'robots', content: 'noindex' }]]
    const zh = pageData.relativePath.startsWith(ZH)
    const url = pageUrl(pageData.relativePath)
    const head: HeadConfig[] = [['link', { rel: 'canonical', href: url }]]
    const other = counterpart(pageData.relativePath, siteConfig.pages)
    if (other) {
      const english = pageUrl(zh ? other : pageData.relativePath)
      head.push(
        ['link', { rel: 'alternate', hreflang: 'en', href: english }],
        ['link', { rel: 'alternate', hreflang: 'zh-TW', href: pageUrl(zh ? pageData.relativePath : other) }],
        ['link', { rel: 'alternate', hreflang: 'x-default', href: english }],
      )
      // Chinese-reading visitors go straight to the 繁體中文 version (theme/languagePreference.ts).
      if (!zh) head.push(['script', {}, languageRedirectScript(pagePath(other))])
    }
    const image = `${SITE_URL}/og-image${zh ? '-zh-TW' : ''}.jpg`
    head.push(
      ['meta', { property: 'og:type', content: 'website' }],
      ['meta', { property: 'og:site_name', content: 'SkyLab' }],
      ['meta', { property: 'og:title', content: title }],
      ['meta', { property: 'og:description', content: description }],
      ['meta', { property: 'og:url', content: url }],
      ['meta', { property: 'og:image', content: image }],
      ['meta', { property: 'og:image:width', content: '1200' }],
      ['meta', { property: 'og:image:height', content: '630' }],
      ['meta', { property: 'og:locale', content: zh ? 'zh_TW' : 'en_US' }],
      ['meta', { property: 'og:locale:alternate', content: zh ? 'en_US' : 'zh_TW' }],
      ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    )
    return head
  },
  buildEnd(siteConfig) {
    writeFileSync(
      path.join(siteConfig.outDir, 'robots.txt'),
      `User-agent: *\nAllow: /\n\nSitemap: ${SITE_URL}/sitemap.xml\n`,
    )
  },
  markdown: {
    // The manual has a screenshot under every step; load them as they scroll in.
    image: { lazyLoading: true },
    config(md) {
      // Inline code such as `{{.SecurityOptions}}` must not be read as a Vue
      // interpolation (fenced blocks are already protected by VitePress).
      const codeInline = md.renderer.rules.code_inline!
      md.renderer.rules.code_inline = (tokens, idx, options, env, self) =>
        codeInline(tokens, idx, options, env, self).replace(/^<code/, '<code v-pre')
      // ```mermaid fences are rendered in the browser by theme/Mermaid.vue.
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
          { text: 'User manual', link: '/manual/' },
          { text: 'Operations', link: '/guides/deployment' },
          { text: 'Reference', link: '/reference/api' },
          { text: 'Changelog', link: '/changelog' },
        ],
        sidebar: sidebar('en'),
        editLink: { pattern: `${REPO}/edit/main/website/:path`, text: 'Edit this page on GitHub' },
        lastUpdated: { text: 'Last updated' },
        outline: { level: [2, 3], label: 'On this page' },
        docFooter: { prev: 'Previous', next: 'Next' },
        footer: { message: 'Released under the AGPL-3.0 License.', copyright: 'SkyLab contributors' },
      },
    },
    'zh-TW': {
      label: '繁體中文',
      lang: 'zh-TW',
      link: '/zh-TW/',
      description: '以 Proxmox VE 打造、學校可自行架設的雲端平台。',
      themeConfig: {
        nav: [
          { text: '快速開始', link: '/zh-TW/getting-started' },
          { text: '使用手冊', link: '/zh-TW/manual/' },
          { text: '維運', link: '/zh-TW/guides/deployment' },
          { text: '參考', link: '/zh-TW/reference/api' },
          { text: '更新紀錄', link: '/zh-TW/changelog' },
        ],
        sidebar: sidebar('zh-TW'),
        editLink: { pattern: `${REPO}/edit/main/website/:path`, text: '在 GitHub 上編輯此頁' },
        lastUpdated: { text: '最後更新' },
        outline: { level: [2, 3], label: '本頁目錄' },
        docFooter: { prev: '上一頁', next: '下一頁' },
        footer: { message: '以 AGPL-3.0 授權釋出。', copyright: 'SkyLab contributors' },
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
