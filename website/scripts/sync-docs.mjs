// Copies the guides under ../docs into the VitePress source tree.
//
//   docs/<name>.md         -> website/guides/<name>.md          (English)
//   docs/<name>.zh-TW.md   -> website/zh-TW/guides/<name>.md    (繁體中文)
//   docs/archive/<name>.md -> both locales (reports exist in one language only)
//   docs/README.md         -> guides/index.md
//
// A guide that exists in one language only is copied to both locales so that
// links never break. While copying, the language switch line under the H1 is
// removed (VitePress has its own locale menu), relative links are rewritten to
// site routes, and links that leave docs/ point at the file on GitHub. Each copy
// gets a `source` frontmatter so the "Edit this page" link goes back to docs/.
//
// The output directories are gitignored. Never edit the copies; edit docs/.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const siteDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(siteDir, '..')
const docsDir = path.join(repoRoot, 'docs')
const GITHUB_BLOB = 'https://github.com/ntubclass/SkyLab/blob/main/'
const ZH_SUFFIX = '.zh-TW.md'
const OUT = {
  en: path.join(siteDir, 'guides'),
  'zh-TW': path.join(siteDir, 'zh-TW', 'guides'),
}

function listDocs() {
  const files = []
  for (const name of fs.readdirSync(docsDir)) {
    if (name.endsWith('.md')) files.push(name)
  }
  const archiveDir = path.join(docsDir, 'archive')
  if (fs.existsSync(archiveDir)) {
    for (const name of fs.readdirSync(archiveDir)) {
      if (name.endsWith('.md')) files.push(`archive/${name}`)
    }
  }
  return files
}

// base name (English file name) -> { en?: rel, zh?: rel }
const groups = new Map()
for (const rel of listDocs()) {
  if (rel.endsWith(ZH_SUFFIX)) {
    const base = rel.slice(0, -ZH_SUFFIX.length) + '.md'
    groups.set(base, { ...(groups.get(base) ?? {}), zh: rel })
  } else {
    groups.set(rel, { ...(groups.get(rel) ?? {}), en: rel })
  }
}

function outputName(base) {
  return base === 'README.md' ? 'index.md' : base
}

function siteLink(base, locale) {
  const prefix = locale === 'en' ? '/guides/' : '/zh-TW/guides/'
  return base === 'README.md' ? prefix : prefix + base.replace(/\.md$/, '')
}

function rewriteLinks(text, srcRel, locale) {
  const srcDir = path.posix.dirname(srcRel)
  return text.replace(/\]\(([^)\s]+)\)/g, (match, target) => {
    if (/^(https?:|mailto:|#)/.test(target)) return match
    const hashIndex = target.indexOf('#')
    const file = hashIndex === -1 ? target : target.slice(0, hashIndex)
    const hash = hashIndex === -1 ? '' : target.slice(hashIndex)
    const resolved = path.posix.normalize(path.posix.join(srcDir, file))
    if (!resolved.startsWith('../') && resolved.endsWith('.md')) {
      let base = resolved
      let wantLocale = locale
      if (base.endsWith(ZH_SUFFIX)) {
        base = base.slice(0, -ZH_SUFFIX.length) + '.md'
        if (locale === 'en') wantLocale = 'zh-TW'
      }
      if (groups.has(base)) return `](${siteLink(base, wantLocale)}${hash})`
      return `](${GITHUB_BLOB}docs/${resolved}${hash})`
    }
    const repoRel = path.posix.normalize(path.posix.join('docs', srcDir, file))
    return `](${GITHUB_BLOB}${repoRel}${hash})`
  })
}

function stripLanguageSwitch(lines) {
  const limit = Math.min(lines.length, 8)
  for (let i = 0; i < limit; i++) {
    const line = lines[i]
    if (line.startsWith('> ') && line.includes('English') && line.includes('繁體中文')) {
      lines.splice(i, 1)
      if (lines[i] !== undefined && lines[i].trim() === '') lines.splice(i, 1)
      return lines
    }
  }
  return lines
}

function convert(srcRel, locale) {
  let text = fs.readFileSync(path.join(docsDir, srcRel), 'utf8')
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1)
  const eol = text.includes('\r\n') ? '\r\n' : '\n'
  const lines = stripLanguageSwitch(text.split(/\r?\n/))
  const body = rewriteLinks(lines.join('\n'), srcRel, locale)
  const frontmatter = `---\nsource: docs/${srcRel}\n---\n\n`
  return (frontmatter + body).replace(/\n/g, eol)
}

function write(locale, base, content) {
  const target = path.join(OUT[locale], outputName(base))
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.writeFileSync(target, content)
}

for (const dir of Object.values(OUT)) {
  fs.rmSync(dir, { recursive: true, force: true })
}

let count = 0
for (const [base, group] of groups) {
  const enSrc = group.en ?? group.zh
  const zhSrc = group.zh ?? group.en
  write('en', base, convert(enSrc, 'en'))
  write('zh-TW', base, convert(zhSrc, 'zh-TW'))
  count++
}

console.log(`sync-docs: ${count} guides copied into guides/ and zh-TW/guides/`)
