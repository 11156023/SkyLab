import { createFileRoute, Link } from "@tanstack/react-router"
import {
  Activity,
  Database,
  FlaskConical,
  Globe,
  GraduationCap,
  LayoutTemplate,
  Plus,
  Terminal,
  Zap,
  type LucideIcon,
} from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import useAuth from "@/hooks/useAuth"
import {
  getQuickStartTemplateDescription,
  getQuickStartTemplateLogo,
  getQuickStartTemplateName,
  QUICK_START_COURSES,
  QUICK_START_TEMPLATE_CATEGORIES,
  type QuickStartCourse,
  type QuickStartTemplateCategory,
  type QuickStartTemplateSlug,
} from "@/lib/templateQuickStart"

// Tailwind full-string classes required for v4 scanner
type CourseIconConfig = { Icon: LucideIcon; wrap: string; color: string }
const COURSE_ICON: Record<string, CourseIconConfig> = {
  database: { Icon: Database,     wrap: "bg-blue-500/15",   color: "text-blue-400" },
  terminal: { Icon: Terminal,     wrap: "bg-zinc-500/15",   color: "text-zinc-400" },
  flask:    { Icon: FlaskConical, wrap: "bg-orange-500/15", color: "text-orange-400" },
  globe:    { Icon: Globe,        wrap: "bg-emerald-500/15",color: "text-emerald-400" },
  monitor:  { Icon: Activity,     wrap: "bg-amber-500/15",  color: "text-amber-400" },
}

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [{ title: "Dashboard - Campus Cloud" }],
  }),
})

const FALLBACK_LOGO =
  "https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp/proxmox.webp"

// All templates flattened with their category info
const ALL_TEMPLATES = QUICK_START_TEMPLATE_CATEGORIES.flatMap((cat) =>
  cat.templates.map((t) => ({ ...t, category: cat })),
)

// ── Course card ───────────────────────────────────────────────────────────────

function CourseCard({ course }: { course: QuickStartCourse }) {
  const cfg = COURSE_ICON[course.iconKey] ?? COURSE_ICON.database
  const { Icon } = cfg

  return (
    <li className="h-full">
      <Link
        to="/resources-create"
        aria-label={`${course.title} — 建立學習環境`}
        className="group flex h-full items-start gap-4 rounded-xl border border-border/60 bg-card p-5 transition-all duration-200 hover:border-primary/25 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      >
        {/* Icon */}
        <div
          aria-hidden="true"
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl ${cfg.wrap}`}
        >
          <Icon className={`h-5 w-5 ${cfg.color}`} />
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-snug transition-colors duration-200 group-hover:text-primary">
            {course.title}
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground line-clamp-2">
            {course.description}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {course.subjects.map((s) => (
              <span
                key={s}
                className="rounded-full bg-muted/60 px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
              >
                {s}
              </span>
            ))}
            <span className="ml-auto text-[11px] font-medium text-primary opacity-0 transition-opacity duration-200 group-hover:opacity-100">
              建立環境 →
            </span>
          </div>
        </div>
      </Link>
    </li>
  )
}

// ── Template card ─────────────────────────────────────────────────────────────

function TemplateCard({
  slug,
  fallbackName,
  category,
}: {
  slug: QuickStartTemplateSlug
  fallbackName: string
  category: QuickStartTemplateCategory
}) {
  const logo = getQuickStartTemplateLogo(slug)
  const name = getQuickStartTemplateName(slug, fallbackName)
  const desc = getQuickStartTemplateDescription(slug)

  return (
    <li>
      <Link
        to="/resources-create"
        search={{ quickStartTemplate: slug }}
        aria-label={`快速建立 ${name}`}
        className="group flex h-full flex-col gap-4 rounded-xl border border-border/60 bg-card p-5 transition-all duration-200 hover:border-primary/25 hover:shadow-[0_4px_24px_rgba(0,0,0,0.08)] dark:hover:shadow-[0_4px_24px_rgba(0,0,0,0.3)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      >
        {/* Header: logo + name + category badge */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div
              aria-hidden="true"
              className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-border/50 bg-background"
            >
              {logo ? (
                <img
                  src={logo}
                  alt=""
                  width={26}
                  height={26}
                  className="h-[26px] w-[26px] object-contain"
                  loading="lazy"
                  onError={(e) => {
                    e.currentTarget.src = FALLBACK_LOGO
                  }}
                />
              ) : (
                <LayoutTemplate
                  className="h-4.5 w-4.5 text-muted-foreground"
                  aria-hidden="true"
                />
              )}
            </div>
            <p className="truncate text-sm font-semibold leading-snug transition-colors duration-200 group-hover:text-primary">
              {name}
            </p>
          </div>
          <span className="shrink-0 rounded-full border border-border/50 bg-muted/40 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
            {category.title}
          </span>
        </div>

        {/* Description */}
        <p className="flex-1 text-xs leading-relaxed text-muted-foreground line-clamp-3">
          {desc}
        </p>

        {/* Footer */}
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-muted-foreground/40">
            {category.description}
          </span>
          <span className="text-xs font-medium text-primary opacity-0 transition-opacity duration-200 group-hover:opacity-100">
            立即建立 →
          </span>
        </div>
      </Link>
    </li>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

function Dashboard() {
  const { user: currentUser } = useAuth()
  const { t } = useTranslation("navigation")
  const [activeCategory, setActiveCategory] = useState("all")

  const isAdmin = currentUser?.is_superuser || currentUser?.role === "admin"
  const canApply =
    isAdmin || currentUser?.role === "teacher" || currentUser?.role === "student"

  const filteredTemplates =
    activeCategory === "all"
      ? ALL_TEMPLATES
      : ALL_TEMPLATES.filter((t) => t.category.id === activeCategory)

  return (
    <div className="space-y-10">
      {/* Welcome */}
      <header>
        <h1
          className="text-3xl font-bold tracking-tight leading-tight"
          style={{ color: "#5471BF" }}
        >
          {t("dashboard.welcome", {
            name: currentUser?.full_name || currentUser?.email,
          })}
        </h1>
        <p
          className="mt-1.5 text-sm leading-relaxed"
          style={{ color: "#5471BF", opacity: 0.75 }}
        >
          {t("dashboard.description")}
        </p>
      </header>

      {/* Quick actions — non-admin */}
      {canApply && !isAdmin && (
        <nav aria-label="快速操作" className="flex flex-wrap gap-3">
          <Link
            to="/applications-create"
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-primary bg-primary/10 px-4 text-sm font-medium text-primary transition-colors duration-150 hover:bg-primary/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            申請新資源
          </Link>
          <Link
            to="/applications"
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-border px-4 text-sm font-medium text-muted-foreground transition-colors duration-150 hover:bg-muted/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          >
            查看我的申請
          </Link>
        </nav>
      )}

      {/* 課程推薦 — admin */}
      {isAdmin && (
        <section aria-labelledby="courses-heading">
          <div className="mb-4 flex items-center gap-2">
            <GraduationCap className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2
              id="courses-heading"
              className="text-sm font-semibold tracking-tight text-foreground"
            >
              課程推薦
            </h2>
            <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              {QUICK_START_COURSES.length}
            </span>
          </div>
          <ul className="grid gap-3 sm:grid-cols-2" role="list" aria-label="課程推薦">
            {QUICK_START_COURSES.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </ul>
        </section>
      )}

      {/* 快速入門 template grid — admin */}
      {isAdmin && (
        <section aria-labelledby="quick-start-heading">
          <div className="mb-4 flex items-center gap-2">
            <Zap className="h-4 w-4 text-primary" aria-hidden="true" />
            <h2
              id="quick-start-heading"
              className="text-sm font-semibold tracking-tight text-foreground"
            >
              快速入門
            </h2>
            <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              {ALL_TEMPLATES.length}
            </span>
          </div>

          {/* Category filter tabs */}
          <div
            className="mb-5 flex flex-wrap gap-2"
            role="tablist"
            aria-label="篩選類別"
          >
            {[{ id: "all", title: "全部" }, ...QUICK_START_TEMPLATE_CATEGORIES].map(
              (cat) => {
                const isActive = activeCategory === cat.id
                return (
                  <button
                    key={cat.id}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    onClick={() => setActiveCategory(cat.id)}
                    className={`inline-flex h-8 items-center rounded-full px-3.5 text-xs font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 ${
                      isActive
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "border border-border/60 bg-transparent text-muted-foreground hover:border-primary/30 hover:text-foreground"
                    }`}
                  >
                    {cat.title}
                  </button>
                )
              },
            )}
          </div>

          {/* Template grid */}
          <ul
            className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
            role="list"
          >
            {filteredTemplates.map((template) => (
              <TemplateCard
                key={template.slug}
                slug={template.slug}
                fallbackName={template.fallbackName}
                category={template.category}
              />
            ))}
          </ul>
        </section>
      )}

      {/* Category browse — non-admin */}
      {!isAdmin && canApply && (
        <section aria-labelledby="browse-heading">
          <div className="mb-4">
            <h2
              id="browse-heading"
              className="text-sm font-semibold tracking-tight text-foreground"
            >
              申請資源
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              選擇類別後提交申請，核准後系統自動佈建。
            </p>
          </div>

          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" role="list">
            {QUICK_START_TEMPLATE_CATEGORIES.map((category) => (
              <li key={category.id}>
                <Link
                  to="/applications-create"
                  aria-label={`申請 ${category.title} 資源`}
                  className="group flex min-h-[52px] items-center gap-3 rounded-xl border border-border/60 bg-card px-4 py-3 transition-all duration-150 hover:border-primary/25 hover:bg-muted/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium leading-snug transition-colors duration-150 group-hover:text-primary">
                      {category.title}
                    </p>
                    <p className="text-xs text-muted-foreground truncate">
                      {category.description}
                    </p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
