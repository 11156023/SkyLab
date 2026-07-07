// @ts-expect-error virtual module supplied by Vite plugin
import rawData from "virtual:templates"

const allData = rawData as Record<string, any>

const slugIndex: Record<string, any> = {}
for (const [path, value] of Object.entries(allData)) {
  if (
    path.endsWith("metadata.json") ||
    path.endsWith("versions.json") ||
    path.endsWith("github-versions.json")
  ) {
    continue
  }
  const tpl = value as { slug?: string }
  if (tpl?.slug) {
    slugIndex[tpl.slug] = tpl
  }
}

export type ServiceTemplate = {
  name?: string
  slug?: string
  description?: string
  description_zh?: string
  description_ja?: string
  documentation?: string
  website?: string
  logo?: string
  interface_port?: number
  config_path?: string
  type?: string
  default_credentials?: { username?: string | null; password?: string | null }
  install_methods?: Array<{
    type?: string
    script?: string
    resources?: {
      cpu?: number
      ram?: number
      hdd?: number
      os?: string
      version?: string | number
    }
  }>
  notes?: Array<{
    text?: string
    text_zh?: string
    text_ja?: string
    type?: string
  }>
}

export function getServiceTemplateBySlug(
  slug: string | null | undefined,
): ServiceTemplate | null {
  if (!slug) return null
  return (slugIndex[slug] as ServiceTemplate | undefined) ?? null
}

/**
 * 從 Proxmox 提供的 LXC volid 列表中，依據服務模板要求的 OS / version
 * 自動挑出最合適的 ostemplate volid。
 *
 * Volid 範例：local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst
 */
export function pickMatchingOsTemplate(
  volids: string[],
  hint: { os?: string; version?: string | number } | undefined,
): string | undefined {
  if (!volids?.length) return undefined
  const os = (hint?.os ?? "").toString().trim().toLowerCase()
  const version = (hint?.version ?? "").toString().trim().toLowerCase()

  if (!os) return volids[0]

  const lowered = volids.map((v) => ({ v, l: v.toLowerCase() }))

  if (os && version) {
    const exact = lowered.find(
      (x) =>
        x.l.includes(`${os}-${version}`) || x.l.includes(`${os}_${version}`),
    )
    if (exact) return exact.v
  }

  const osMatches = lowered.filter((x) => x.l.includes(os))
  if (osMatches.length === 0) return volids[0]

  // 優先選新版本（依檔名字典序倒序，多半就是版本越新）
  osMatches.sort((a, b) => b.l.localeCompare(a.l))
  return osMatches[0].v
}
