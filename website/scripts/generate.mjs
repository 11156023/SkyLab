// Generates the pages that are derived from other files of the repository.
// Runs before every build (see package.json); the outputs are gitignored.
//
//   operations/environment.md, zh-TW/operations/environment.md
//       table of every variable in ../.env.example, grouped by its section
//   changelog.md, zh-TW/changelog.md
//       conventional-commit subjects from the git history, grouped by month

import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const siteDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(siteDir, '..')

function write(rel, content) {
  const file = path.join(siteDir, rel)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, content)
}

// What each context must escape so every character of a table cell renders
// literally:
// - outside inline code: backslashes (so the escapes below stay escapes), pipes
//   (the row is split on them before inline parsing) and `<`, `>`, `{{`, which the
//   Vue compiler would otherwise read as tags and interpolations;
// - inside inline code: only pipes — a backslash there is already literal, and
//   the table splitter drops the one in front of an escaped pipe.
const ESCAPES_OUTSIDE_CODE = { '\\': '\\\\', '|': '\\|', '<': '&lt;', '>': '&gt;', '{{': '&#123;&#123;' }
const ESCAPES_INSIDE_CODE = { '|': '\\|' }

// One pass over the text, so an escape never gets re-escaped by a later step.
function escapeWith(text, escapes) {
  return text.replace(/\{\{|[\\|<>]/g, (match) => escapes[match] ?? match)
}

function cell(text) {
  return text
    .replace(/\s+/g, ' ')
    .trim()
    .split('`')
    .map((part, i) => escapeWith(part, i % 2 === 0 ? ESCAPES_OUTSIDE_CODE : ESCAPES_INSIDE_CODE))
    .join('`')
}

// ── Environment variables ────────────────────────────────────────────────────

const SECTION_TITLES_EN = {
  1: 'Required: identity and secrets',
  2: 'Environment',
  3: 'Database (PostgreSQL in the stack)',
  4: 'Compose internals',
  5: 'Redis',
  6: 'CORS',
  7: 'Proxmox connection (not configured here)',
  8: 'System AI / vLLM (optional)',
  9: 'AI API through LiteLLM (optional)',
  10: 'SSH remote execution / PVE log collector (optional)',
  11: 'Email / SMTP',
  12: 'Other optional settings',
  13: 'Monitoring stack (optional)',
}

function parseEnvExample() {
  const text = fs.readFileSync(path.join(repoRoot, '.env.example'), 'utf8')
  const sections = []
  let current = null
  let buffer = []
  let bufferUsed = false
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trimEnd()
    const header = line.match(/^#\s*【(\d+)】\s*(.*)$/)
    if (header) {
      current = { number: Number(header[1]), title: header[2].trim(), vars: [] }
      sections.push(current)
      buffer = []
      bufferUsed = false
      continue
    }
    if (!current) continue
    if (line === '' || /^#\s*-{5,}/.test(line) || /^#\s*={5,}/.test(line)) {
      buffer = []
      bufferUsed = false
      continue
    }
    const variable = line.match(/^(#\s*)?([A-Z][A-Z0-9_]*)=(.*)$/)
    if (variable) {
      current.vars.push({
        name: variable[2],
        value: variable[3].trim(),
        commented: Boolean(variable[1]),
        description: buffer.map((l) => l.replace(/^#\s?/, '')).join(' '),
      })
      bufferUsed = true
      continue
    }
    if (line.startsWith('#')) {
      if (bufferUsed) {
        buffer = []
        bufferUsed = false
      }
      buffer.push(line)
    }
  }
  return sections
}

function environmentPage(locale) {
  const zh = locale === 'zh-TW'
  const sections = parseEnvExample()
  const lines = []
  lines.push(zh ? '# 環境變數對照表' : '# Environment variables')
  lines.push('')
  lines.push(
    zh
      ? '本頁在建置時由 repo 根目錄的 `.env.example` 自動產生；說明欄是該檔案裡的註解原文。`.env` 由 Docker Compose 與後端的 `app/core/config.py` 共同讀取。「選填」表示該變數在範本中是註解掉的，有合理預設值。'
      : 'This page is generated at build time from `.env.example` at the repository root; the description column is the comment text from that file, verbatim (mostly in Traditional Chinese). `.env` is read by Docker Compose and by the backend\'s `app/core/config.py`. "Optional" means the variable is commented out in the template and has a sensible default.',
  )
  lines.push('')
  lines.push(
    zh
      ? '完整說明見[部署指南](/zh-TW/guides/deployment)；只有少數值必須在第一次啟動前修改，見[快速開始](/zh-TW/getting-started)。'
      : 'The [Deployment guide](/guides/deployment) explains the important ones in context; the few values that must change before the first start are listed in [Getting started](/getting-started).',
  )
  for (const section of sections) {
    lines.push('')
    const title = zh ? section.title : SECTION_TITLES_EN[section.number] ?? section.title
    lines.push(`## ${section.number}. ${title}`)
    lines.push('')
    lines.push(zh ? '| 變數 | 範本值 | 必填 | 說明 |' : '| Variable | Template value | Required | Description |')
    lines.push('| --- | --- | --- | --- |')
    for (const v of section.vars) {
      const value = v.value === '' ? '' : `\`${cell(v.value)}\``
      const required = v.commented ? (zh ? '選填' : 'Optional') : zh ? '有預設' : 'Set in template'
      lines.push(`| \`${v.name}\` | ${value} | ${required} | ${cell(v.description)} |`)
    }
  }
  lines.push('')
  return lines.join('\n')
}

// ── Changelog ────────────────────────────────────────────────────────────────

const TYPE_LABELS = {
  en: { feat: 'Features', fix: 'Fixes', perf: 'Performance', docs: 'Documentation', other: 'Maintenance' },
  'zh-TW': { feat: '新功能', fix: '修正', perf: '效能', docs: '文件', other: '維護' },
}
const TYPE_ORDER = ['feat', 'fix', 'perf', 'docs', 'other']

function readCommits() {
  try {
    const out = execFileSync('git', ['log', '--no-merges', '--date=short', '--format=%ad%x09%s'], {
      cwd: repoRoot,
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
    })
    return out.split('\n').filter(Boolean)
  } catch {
    return null
  }
}

function changelogPage(locale, commits) {
  const zh = locale === 'zh-TW'
  const lines = []
  lines.push(zh ? '# 更新紀錄' : '# Changelog')
  lines.push('')
  lines.push(
    zh
      ? '本頁在建置時由 git 歷史產生：列出主分支上採用慣例前綴（`feat`、`fix`、`perf`、`docs` 等）的 commit 標題，依月份分組；合併 commit 與沒有前綴的 commit 不列入。完整歷史見 [GitHub](https://github.com/ntubclass/SkyLab/commits/main)。'
      : 'This page is generated at build time from the git history: commit subjects on the main branch that use a conventional prefix (`feat`, `fix`, `perf`, `docs`, …), grouped by month. Merge commits and commits without a prefix are not listed. The full history is on [GitHub](https://github.com/ntubclass/SkyLab/commits/main).',
  )
  lines.push('')
  if (!commits) {
    lines.push(zh ? '（這次建置沒有 git 歷史可用。）' : '(No git history was available for this build.)')
    lines.push('')
    return lines.join('\n')
  }
  const months = new Map()
  const pattern = /^(feat|fix|perf|docs|refactor|chore|test|ci|build|style)(\([^)]*\))?!?:\s*(.+)$/
  for (const entry of commits) {
    const [date, subject] = entry.split('\t')
    const m = subject?.match(pattern)
    if (!m) continue
    const month = date.slice(0, 7)
    const type = ['feat', 'fix', 'perf', 'docs'].includes(m[1]) ? m[1] : 'other'
    const scope = m[2] ? m[2].slice(1, -1) : ''
    const text = scope ? `**${scope}:** ${m[3]}` : m[3]
    if (!months.has(month)) months.set(month, new Map())
    const byType = months.get(month)
    if (!byType.has(type)) byType.set(type, new Set())
    byType.get(type).add(text)
  }
  for (const [month, byType] of months) {
    lines.push(`## ${month}`)
    lines.push('')
    for (const type of TYPE_ORDER) {
      if (!byType.has(type)) continue
      lines.push(`### ${TYPE_LABELS[locale][type]}`)
      lines.push('')
      for (const text of byType.get(type)) lines.push(`- ${cell(text)}`)
      lines.push('')
    }
  }
  return lines.join('\n')
}

write('operations/environment.md', environmentPage('en'))
write('zh-TW/operations/environment.md', environmentPage('zh-TW'))
const commits = readCommits()
write('changelog.md', changelogPage('en', commits))
write('zh-TW/changelog.md', changelogPage('zh-TW', commits))
console.log(`generate: environment (${parseEnvExample().length} sections), changelog (${commits ? commits.length : 0} commits scanned)`)
