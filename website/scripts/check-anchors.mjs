// Verifies that every in-site link with a fragment (`/guides/x#section`,
// `#section`) points at a heading id that exists in the built HTML. VitePress
// checks dead pages but not dead fragments. Run after `vitepress build`.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const siteDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const distDir = path.join(siteDir, '.vitepress', 'dist')

function* markdownFiles(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) yield* markdownFiles(full)
    else if (entry.name.endsWith('.md')) yield full
  }
}

function routeOf(mdFile) {
  let rel = path.relative(siteDir, mdFile).replace(/\\/g, '/').replace(/\.md$/, '')
  if (rel.endsWith('/index')) rel = rel.slice(0, -'index'.length)
  if (rel === 'index') rel = ''
  return '/' + rel
}

function htmlFor(route) {
  const clean = route.replace(/^\//, '')
  const candidates = clean === '' || clean.endsWith('/') ? [`${clean}index.html`] : [`${clean}.html`, `${clean}/index.html`]
  for (const c of candidates) {
    const file = path.join(distDir, c)
    if (fs.existsSync(file)) return fs.readFileSync(file, 'utf8')
  }
  return null
}

const idCache = new Map()
function idsOf(route) {
  if (!idCache.has(route)) {
    const html = htmlFor(route)
    const ids = new Set()
    if (html) for (const m of html.matchAll(/\sid="([^"]+)"/g)) ids.add(decodeURIComponent(m[1]))
    idCache.set(route, html ? ids : null)
  }
  return idCache.get(route)
}

let problems = 0
for (const file of markdownFiles(siteDir)) {
  const text = fs.readFileSync(file, 'utf8')
  const here = routeOf(file)
  for (const m of text.matchAll(/\]\(([^)\s]*#[^)\s]+)\)/g)) {
    const target = m[1]
    if (/^https?:/.test(target)) continue
    const [route, fragment] = target.split('#')
    const page = route === '' ? here : route.replace(/\.md$/, '')
    const ids = idsOf(page)
    const wanted = decodeURIComponent(fragment)
    if (ids === null) {
      console.log(`${path.relative(siteDir, file)}: page not built for ${target}`)
      problems++
    } else if (!ids.has(wanted)) {
      console.log(`${path.relative(siteDir, file)}: missing anchor ${target}`)
      problems++
    }
  }
}
console.log(problems === 0 ? 'check-anchors: all fragments resolve' : `check-anchors: ${problems} problem(s)`)
process.exit(problems === 0 ? 0 : 1)
