// @ts-expect-error
import rawData from "virtual:templates"

import type { FastTemplate } from "@/components/Applications/FastTemplatesTab"

const allData = rawData as Record<string, FastTemplate>

const allTemplates = Object.entries(allData)
  .filter(
    ([path]) =>
      !path.endsWith("metadata.json") &&
      !path.endsWith("versions.json") &&
      !path.endsWith("github-versions.json"),
  )
  .map(([_, value]) => value)

export const QUICK_START_TEMPLATE_SLUGS = [
  "mysql",
  "postgresql",
  "mongodb",
  "mariadb",
  "redis",
  "grafana",
  "homepage",
  "openwebui",
  "wordpress",
  "jupyternotebook",
] as const

export type QuickStartTemplateSlug = (typeof QUICK_START_TEMPLATE_SLUGS)[number]

type QuickStartTemplateSummary = {
  slug: QuickStartTemplateSlug
  fallbackName: string
}

export type QuickStartTemplateCategory = {
  id: string
  title: string
  description: string
  templates: QuickStartTemplateSummary[]
}

export const QUICK_START_TEMPLATE_CATEGORIES: QuickStartTemplateCategory[] = [
  {
    id: "databases",
    title: "資料庫",
    description: "關聯式資料庫與 NoSQL",
    templates: [
      { slug: "postgresql", fallbackName: "PostgreSQL" },
      { slug: "mongodb", fallbackName: "MongoDB" },
      { slug: "mariadb", fallbackName: "MariaDB" },
      { slug: "redis", fallbackName: "Redis" },
    ],
  },
  {
    id: "data-science",
    title: "資料科學",
    description: "資料分析與機器學習環境",
    templates: [{ slug: "jupyternotebook", fallbackName: "Jupyter Notebook" }],
  },
  {
    id: "monitoring",
    title: "監控與分析",
    description: "系統指標收集與視覺化",
    templates: [{ slug: "grafana", fallbackName: "Grafana" }],
  },
  {
    id: "ai-devtools",
    title: "AI / 開發工具",
    description: "LLM 部署與 AI 應用開發",
    templates: [{ slug: "openwebui", fallbackName: "Open WebUI" }],
  },
  {
    id: "webservers",
    title: "網站服務",
    description: "網頁應用與入口儀表板",
    templates: [
      { slug: "wordpress", fallbackName: "WordPress" },
      { slug: "homepage", fallbackName: "Homepage" },
    ],
  },
]

export function getQuickStartTemplate(
  slug?: QuickStartTemplateSlug | null,
): FastTemplate | null {
  if (!slug) return null
  return allTemplates.find((template) => template.slug === slug) ?? null
}

export function getQuickStartTemplateName(
  slug: QuickStartTemplateSlug,
  fallbackName: string,
): string {
  return getQuickStartTemplate(slug)?.name || fallbackName
}

export function getQuickStartTemplateDescription(
  slug: QuickStartTemplateSlug,
): string {
  const template = getQuickStartTemplate(slug)
  return (
    template?.description_zh ||
    template?.description ||
    "直接使用模板預設配置建立。"
  )
}

export function getQuickStartTemplateLogo(
  slug: QuickStartTemplateSlug,
): string | undefined {
  return getQuickStartTemplate(slug)?.logo || undefined
}

// ── Course presets ────────────────────────────────────────────────────────────

export type QuickStartCourse = {
  id: string
  title: string
  description: string
  subjects: string[]
  iconKey: "database" | "terminal" | "flask" | "monitor" | "globe"
}

export const QUICK_START_COURSES: QuickStartCourse[] = [
  {
    id: "db-design",
    title: "資料庫設計與應用",
    description:
      "學習關聯式資料庫設計、SQL 語法、資料正規化與交易控制，部署 MySQL、PostgreSQL 或 MariaDB 進行上機實作。",
    subjects: ["資料庫設計", "後端開發", "SQL"],
    iconKey: "database",
  },
  {
    id: "linux-ops",
    title: "Linux 系統實作",
    description:
      "掌握 Linux 指令列操作、檔案系統管理、程序控制與基礎網路設定，在獨立容器環境中安全練習。",
    subjects: ["作業系統", "系統管理", "DevOps"],
    iconKey: "terminal",
  },
  {
    id: "data-science",
    title: "資料科學與機器學習",
    description:
      "使用 Jupyter Notebook 進行資料清理、視覺化分析與機器學習模型訓練，支援 Python 完整科學運算環境。",
    subjects: ["資料科學", "機器學習", "Python"],
    iconKey: "flask",
  },
  {
    id: "web-dev",
    title: "網頁應用開發",
    description:
      "建立完整的網站開發環境，部署前後端應用、CMS 或靜態網站，適合 Web 開發實作課程。",
    subjects: ["Web 開發", "網站架設", "前後端整合"],
    iconKey: "globe",
  },
]

export function generateQuickStartHostname(
  slug: QuickStartTemplateSlug,
): string {
  const suffix = new Date()
    .toISOString()
    .replace(/[-:TZ.]/g, "")
    .slice(4, 10)

  return `${slug}-${suffix}`.slice(0, 63)
}
