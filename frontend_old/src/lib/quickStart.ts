import type { VmTemplateSchema } from "@/client"

export type QuickStartPresetId = "postgres-vm" | "python-vm"
export type QuickStartMode = "template" | "ai"

export type QuickStartPreset = {
  id: QuickStartPresetId
  title: string
  subtitle: string
  description: string
  resourceType: "vm"
  defaultCores: number
  defaultDiskGb: number
  defaultEnvironmentType: string
  defaultMemoryMb: number
  defaultReason: string
  defaultUsername: string
  osInfo: string
  preferredVmKeywords: string[]
  quickStartHint: string
  aiPrompt: string
  tags: string[]
}

export const QUICK_START_PRESETS: Record<QuickStartPresetId, QuickStartPreset> =
  {
    "postgres-vm": {
      id: "postgres-vm",
      title: "PostgreSQL 練習 VM",
      subtitle: "建立已預設好資料庫用途的虛擬機",
      description:
        "適合 SQL 練習、資料表設計、查詢測試與課程作業。系統會先帶入 VM 與標準規格。",
      resourceType: "vm",
      defaultCores: 2,
      defaultDiskGb: 40,
      defaultEnvironmentType: "PostgreSQL 練習環境",
      defaultMemoryMb: 4096,
      defaultReason:
        "PostgreSQL 練習 VM：用於 SQL 語法練習、資料庫課程作業與資料表操作測試，先以標準教學規格建立可立即使用的 VM 環境。",
      defaultUsername: "student",
      osInfo: "Ubuntu 24.04 LTS（PostgreSQL 練習用途）",
      preferredVmKeywords: ["ubuntu 24", "ubuntu24", "ubuntu 22", "ubuntu22"],
      quickStartHint: "只要填 VM 名稱與密碼，其餘先用標準教學配置。",
      aiPrompt:
        "我要建立一台用來練習 PostgreSQL 的 VM。請明確以 VM 為前提，不要改成 LXC。用途是 SQL 練習、資料庫課程與作業測試，優先選 Ubuntu 類型的 VM template，並提供適合教學用途的 CPU、RAM、Disk 與申請理由。",
      tags: ["VM", "PostgreSQL", "SQL", "資料庫"],
    },
    "python-vm": {
      id: "python-vm",
      title: "Python 練習 VM",
      subtitle: "建立已預設好開發用途的虛擬機",
      description:
        "適合 Python 基礎練習、腳本開發、課程作業與小型程式測試。系統會先帶入 VM 與標準規格。",
      resourceType: "vm",
      defaultCores: 2,
      defaultDiskGb: 30,
      defaultEnvironmentType: "Python 練習環境",
      defaultMemoryMb: 4096,
      defaultReason:
        "Python 練習 VM：用於 Python 程式設計課程、腳本測試與作業開發，先以標準教學規格建立可立即使用的 VM 環境。",
      defaultUsername: "student",
      osInfo: "Ubuntu 24.04 LTS（Python 開發用途）",
      preferredVmKeywords: ["ubuntu 24", "ubuntu24", "ubuntu 22", "ubuntu22"],
      quickStartHint: "只要填 VM 名稱與密碼，其餘先用標準教學配置。",
      aiPrompt:
        "我要建立一台用來練習 Python 的 VM。請明確以 VM 為前提，不要改成 LXC。用途是 Python 程式設計、腳本測試與作業開發，優先選 Ubuntu 類型的 VM template，並提供適合教學用途的 CPU、RAM、Disk 與申請理由。",
      tags: ["VM", "Python", "程式設計", "開發"],
    },
  }

function normalizeTemplateName(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "")
}

export function getQuickStartPreset(
  presetId?: QuickStartPresetId | null,
): QuickStartPreset | null {
  if (!presetId) return null
  return QUICK_START_PRESETS[presetId] ?? null
}

export function pickQuickStartVmTemplateId(
  templates: VmTemplateSchema[],
  preset: QuickStartPreset,
): number | undefined {
  if (!templates.length) return undefined

  for (const keyword of preset.preferredVmKeywords) {
    const normalizedKeyword = normalizeTemplateName(keyword)
    const match = templates.find((template) =>
      normalizeTemplateName(template.name).includes(normalizedKeyword),
    )
    if (match) return match.vmid
  }

  const ubuntuTemplate = templates.find((template) =>
    normalizeTemplateName(template.name).includes("ubuntu"),
  )
  if (ubuntuTemplate) return ubuntuTemplate.vmid

  return templates[0]?.vmid
}
