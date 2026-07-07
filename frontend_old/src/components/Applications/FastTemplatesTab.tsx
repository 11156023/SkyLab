// @ts-expect-error
import rawData from "virtual:templates"
import {
  AlertTriangle,
  ArrowLeft,
  FileText,
  Globe,
  Info,
  LayoutTemplate,
  Rocket,
  Search,
  Server,
  X,
} from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// ScriptDeployDialog is used by ApplicationRequestPage, not here

const allData = rawData as Record<string, any>

const metadataKeys = Object.keys(allData).find((path) =>
  path.endsWith("metadata.json"),
)
const rawMetadata = metadataKeys ? allData[metadataKeys] : { categories: [] }

const templates = Object.entries(allData)
  .filter(
    ([path]) =>
      !path.endsWith("metadata.json") &&
      !path.endsWith("versions.json") &&
      !path.endsWith("github-versions.json"),
  )
  .map(([_, val]) => val)
  .sort((a, b) => (a.name || "").localeCompare(b.name || ""))

export type FastTemplate = (typeof templates)[number]

function renderNoteText(text: string): ReactNode[] {
  return text.split(/\r?\n/).flatMap((line, lineIdx, lines) => {
    const parts: React.ReactNode[] = []
    const segments = line.split(/`([^`]+)`/)
    for (let i = 0; i < segments.length; i++) {
      if (i % 2 === 0) {
        if (segments[i]) parts.push(segments[i])
      } else {
        parts.push(
          <code
            key={`code-${lineIdx}-${i}`}
            className="bg-black/10 dark:bg-white/10 px-1 py-0.5 rounded text-xs"
          >
            {segments[i]}
          </code>,
        )
      }
    }
    if (lineIdx < lines.length - 1) parts.push(<br key={`br-${lineIdx}`} />)
    return parts
  })
}

function NoteBox({ note }: { note: { text: string; type?: string } }) {
  let colorClasses =
    "bg-blue-500/10 border-blue-500/20 text-blue-600 dark:text-blue-400"
  let Icon = Info

  if (note.type === "warning") {
    colorClasses =
      "bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400"
    Icon = AlertTriangle
  } else if (note.type === "alert") {
    colorClasses =
      "bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400"
    Icon = AlertTriangle
  }

  return (
    <div
      className={`p-4 rounded-lg border flex items-start gap-3 ${colorClasses}`}
    >
      <Icon className="h-5 w-5 mt-0.5 shrink-0" />
      <div className="text-sm leading-relaxed">{renderNoteText(note.text)}</div>
    </div>
  )
}

type FastTemplatesTabProps = {
  onSelectTemplate?: (template: FastTemplate) => void
  onBack?: () => void
}

export function FastTemplatesTab({
  onSelectTemplate,
  onBack,
}: FastTemplatesTabProps) {
  const { t } = useTranslation("applications")
  const [searchTerm, setSearchTerm] = useState("")
  const [selectedCategory, setSelectedCategory] = useState<string>("all")
  const [selectedTemplate, setSelectedTemplate] = useState<any | null>(null)

  const categories = useMemo(() => {
    return rawMetadata.categories.sort(
      (a: any, b: any) => a.sort_order - b.sort_order,
    )
  }, [])

  const categoriesMap = useMemo(() => {
    const map = new Map<number, string>()
    for (const cat of rawMetadata.categories) {
      map.set(cat.id, cat.name)
    }
    return map
  }, [])

  const filteredTemplates = useMemo(() => {
    return templates.filter((template) => {
      const matchesSearch =
        template.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        template.description?.toLowerCase().includes(searchTerm.toLowerCase())

      const matchesCategory =
        selectedCategory === "all" ||
        template.categories?.includes(Number(selectedCategory))

      return matchesSearch && matchesCategory
    })
  }, [searchTerm, selectedCategory])

  return (
    <div className="relative flex flex-col gap-4 animate-in fade-in duration-300 overflow-hidden">
      {onBack && (
        <div className="flex justify-start">
          <Button
            type="button"
            variant="ghost"
            onClick={onBack}
            className="-ml-2"
          >
            <ArrowLeft className="h-4 w-4" />
            {t("templates.backToList")}
          </Button>
        </div>
      )}

      <div className="flex flex-row items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            type="search"
            placeholder={t("templates.searchPlaceholder")}
            className="pl-8"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        <Select value={selectedCategory} onValueChange={setSelectedCategory}>
          <SelectTrigger className="w-32 md:w-45 shrink-0">
            <SelectValue placeholder={t("templates.allCategories")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("templates.allCategories")}</SelectItem>
            {categories.map((cat: any) => (
              <SelectItem key={cat.id} value={cat.id.toString()}>
                {cat.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredTemplates.map((template) => (
          <Card
            key={template.slug}
            className="cursor-pointer transition-all hover:shadow-md hover:border-primary/50 group flex flex-col h-full bg-card/50 backdrop-blur-sm"
            onClick={() => setSelectedTemplate(template)}
          >
            <CardHeader className="flex flex-row items-center gap-4 pb-2 p-4">
              <div className="h-10 w-10 rounded-lg bg-background p-1.5 border shadow-sm flex items-center justify-center overflow-hidden shrink-0 group-hover:scale-105 transition-transform">
                {template.logo ? (
                  <img
                    src={template.logo}
                    alt={template.name}
                    className="h-full w-full object-contain"
                    loading="lazy"
                    onError={(e) => {
                      e.currentTarget.src =
                        "https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp/proxmox.webp"
                    }}
                  />
                ) : (
                  <LayoutTemplate className="h-5 w-5 text-muted-foreground" />
                )}
              </div>
              <div className="flex flex-col overflow-hidden">
                <CardTitle className="text-base truncate" title={template.name}>
                  {template.name}
                </CardTitle>
              </div>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <p className="line-clamp-2 text-xs text-muted-foreground mb-3 min-h-[32px]">
                {template.description || t("templates.noDescription")}
              </p>
              <div className="flex flex-wrap gap-1">
                {template.categories?.slice(0, 2).map((catId: number) => (
                  <Badge
                    variant="secondary"
                    key={catId}
                    className="text-[10px] px-1.5 py-0 h-4"
                  >
                    {categoriesMap.get(catId) || "Unknown"}
                  </Badge>
                ))}
                {template.categories?.length > 2 && (
                  <Badge
                    variant="outline"
                    className="text-[10px] px-1.5 py-0 h-4"
                  >
                    +{template.categories.length - 2}
                  </Badge>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {filteredTemplates.length === 0 && (
        <div className="py-12 text-center text-muted-foreground text-sm">
          {t("templates.noTemplatesFound")}
        </div>
      )}

      {selectedTemplate && (
        <>
          <button
            type="button"
            className="absolute inset-0 z-10 bg-background/60 backdrop-blur-[1px]"
            onClick={() => setSelectedTemplate(null)}
            aria-label={t("templates.backToList")}
          />
          <div className="absolute inset-y-0 right-0 z-20 w-full max-w-2xl border-l bg-background shadow-2xl animate-in slide-in-from-right-8 duration-300">
            <div className="flex h-full flex-col">
              <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
                <div className="flex min-w-0 items-center gap-4">
                  <div className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-xl border bg-card p-2 shadow-sm">
                    {selectedTemplate.logo ? (
                      <img
                        src={selectedTemplate.logo}
                        alt={selectedTemplate.name}
                        className="h-full w-full object-contain"
                        onError={(e) => {
                          e.currentTarget.src =
                            "https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp/proxmox.webp"
                        }}
                      />
                    ) : (
                      <LayoutTemplate className="h-7 w-7 text-muted-foreground" />
                    )}
                  </div>
                  <div className="min-w-0">
                    <h2 className="truncate text-xl font-bold">
                      {selectedTemplate.name}
                    </h2>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      {selectedTemplate.updateable && (
                        <Badge variant="secondary">
                          {t("templates.supportsUpdate")}
                        </Badge>
                      )}
                      {selectedTemplate.categories
                        ?.slice(0, 3)
                        .map((catId: number) => (
                          <Badge variant="outline" key={catId}>
                            {categoriesMap.get(catId) || "Unknown"}
                          </Badge>
                        ))}
                    </div>
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => setSelectedTemplate(null)}
                  className="shrink-0"
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>

              <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
                <div>
                  <h4 className="mb-2 text-sm font-medium">
                    {t("templates.description")}
                  </h4>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
                    {selectedTemplate.description ||
                      t("templates.noDescription")}
                  </p>
                </div>

                {selectedTemplate.notes &&
                  selectedTemplate.notes.length > 0 && (
                    <div className="space-y-3">
                      {selectedTemplate.notes.map((note: any, idx: number) => (
                        <NoteBox key={idx} note={note} />
                      ))}
                    </div>
                  )}

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  {selectedTemplate.config_path && (
                    <div className="rounded-lg border bg-card p-3">
                      <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                        <FileText className="h-3.5 w-3.5" />
                        {t("templates.configLocation")}
                      </div>
                      <div className="break-all text-sm font-medium">
                        {selectedTemplate.config_path}
                      </div>
                    </div>
                  )}
                  {selectedTemplate.interface_port && (
                    <div className="rounded-lg border bg-card p-3">
                      <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Globe className="h-3.5 w-3.5" />
                        {t("templates.webInterface")}
                      </div>
                      <div className="text-sm font-medium">
                        {t("templates.port", {
                          port: selectedTemplate.interface_port,
                        })}
                      </div>
                    </div>
                  )}
                  {selectedTemplate.default_credentials?.username && (
                    <div className="rounded-lg border bg-card p-3">
                      <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Server className="h-3.5 w-3.5" />
                        {t("templates.defaultUsername")}
                      </div>
                      <div className="text-sm font-medium">
                        {selectedTemplate.default_credentials.username}
                      </div>
                    </div>
                  )}
                  {selectedTemplate.default_credentials?.password && (
                    <div className="rounded-lg border bg-card p-3">
                      <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Server className="h-3.5 w-3.5" />
                        {t("templates.defaultPassword")}
                      </div>
                      <div className="text-sm font-medium">
                        {selectedTemplate.default_credentials.password}
                      </div>
                    </div>
                  )}
                </div>

                {(selectedTemplate.website ||
                  selectedTemplate.documentation) && (
                  <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row">
                    {selectedTemplate.website && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="flex-1"
                        asChild
                      >
                        <a
                          href={selectedTemplate.website}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <Globe className="mr-2 h-4 w-4" />
                          {t("templates.officialWebsite")}
                        </a>
                      </Button>
                    )}
                    {selectedTemplate.documentation && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="flex-1"
                        asChild
                      >
                        <a
                          href={selectedTemplate.documentation}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <FileText className="mr-2 h-4 w-4" />
                          {t("templates.documentation")}
                        </a>
                      </Button>
                    )}
                  </div>
                )}

                {onSelectTemplate && (
                  <div className="border-t pt-4">
                    <Button
                      type="button"
                      variant="default"
                      className="w-full"
                      onClick={() => {
                        onSelectTemplate(selectedTemplate)
                        setSelectedTemplate(null)
                      }}
                    >
                      {selectedTemplate.type === "ct" &&
                      selectedTemplate.install_methods?.length > 0 ? (
                        <>
                          <Rocket className="mr-2 h-4 w-4" />
                          {t("form.selectTemplate")}
                        </>
                      ) : (
                        t("form.selectTemplate")
                      )}
                    </Button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
