# SkyLab documentation site

> **English** | [繁體中文](./README.zh-TW.md)

All documentation of the repository lives in this directory and is published as a [VitePress](https://vitepress.dev/) site on Cloudflare Pages (see "Deployment" below). The only Markdown files kept outside are the root `README.md` and the files GitHub itself reads (`CONTRIBUTING`, `SECURITY`, `CODE_OF_CONDUCT`, `THIRD_PARTY_NOTICES`, the `.github/` templates).

## Layout

| Directory | Content |
| --- | --- |
| `index.md`, `getting-started.md`, `concepts.md`, `troubleshooting.md` | Home and the introduction pages |
| `manual/` | User manual, one page per feature; `manual/index.md` groups them by role |
| `guides/` | Operations, development and design guides; `guides/index.md` is the index |
| `operations/` | Upgrade and backup guides; `operations/environment.md` is generated |
| `reference/` | REST API (`public/openapi.json` rendered with Scalar), WebSocket endpoints, governance configuration, username policy |
| `development/` | Internationalisation |
| `project/` | License page |
| `changelog.md` | Generated from the git history |
| `zh-TW/` | The same tree in 繁體中文 |
| `public/` | Favicon, `openapi.json`, the home page screenshot under `public/screenshots/` |
| `.vitepress/` | Configuration (navigation and both sidebars in `config.mts`), theme components (Scalar and mermaid renderers) |
| `scripts/generate.mjs` | Builds the generated pages before every build |
| `scripts/manual-shots/` | Takes the user manual's step screenshots (see below) |
| `public/manual-shots/` | Those screenshots, one per section: `<lang>/<page>/s<section>.webp` |

Generated files (`operations/environment.md`, `changelog.md`, `public/openapi.json` and their `zh-TW/` copies) are gitignored.

## Working locally

```bash
# optional: API reference (otherwise the page explains how to generate it)
cd backend
uv run --no-sync python scripts/export_openapi.py ../website/public/openapi.json

cd ../website
bun install
bun run dev        # http://localhost:5173
bun run build      # .vitepress/dist
bun run preview    # restart it after every build: it caches the file list
```

`bun run generate` only produces the generated pages. The export script imports the FastAPI app without starting it; it needs the project `.env` (or the same variables in the environment) but no database, Redis or Proxmox.

## Manual screenshots

Every section of the user manual's steps ends with one picture, taken from the real frontend with a mock API, in the page's language. In it, the element of each step is outlined and labelled with the step's number. Section `n` is the n-th `###` heading under "Step by step"; step `k` is the k-th numbered item in it (a section without numbered steps counts as step 0 and gets no number). Each page has a scene file, `scripts/manual-shots/scenes/<page>.mjs`, that opens the page, sets up the mock data and calls `shot(step, element)` for every step. Consecutive steps on the same screen share one frame; when the screen changes (a dialog opens, the page scrolls) the next frame is added below.

```bash
cd frontend && bun run build                         # the screenshots are taken from frontend/dist
cd ../website
npx playwright-core install chromium                 # once (the browser for the pinned version), or set CHROME_PATH
node scripts/manual-shots/shoot.mjs my-requests      # one page (no name: every page); --lang, --section to narrow
node scripts/manual-shots/preview.mjs my-requests    # contact sheets in scripts/manual-shots/.debug/
node scripts/manual-shots/inject.mjs                 # put the image lines at the end of the sections
node scripts/manual-shots/check.mjs --strict         # every section has a picture in both languages
```

The browser clock is frozen at 2026-10-12 10:00 (Taipei), so re-running a scene produces the same pictures. When you change a manual page's steps, update its scene, re-shoot the page and re-run `inject.mjs`; it removes and re-inserts only its own image lines. When a page's UI changes, re-shoot that page.

Things to know when writing or fixing a scene:

- `hasText` and `getByRole({ name })` match a case-insensitive substring, so "Name" also matches a field whose list has a "CNAME" option. Anchor the match with `` new RegExp(`^${escapeRegExp(text)}`) ``; `escapeRegExp` is in `lib/run.mjs`.
- A mock has to answer with the same shape as the backend. An IP subnet that is not set is `200` with `null`, not `404`; a wrong shape shows up in the picture as if the frontend were broken (for example a list that does not refresh after a delete).
- Native date and time fields follow the browser's language, not the page's. `launchBrowser(lang)` passes `--lang`, and `shoot.mjs` opens one browser per language.
- Consecutive steps whose screens differ by less than 4% of the pixels share a frame. On a busy machine a screen is sometimes split into two frames anyway; shoot fewer pages at a time or re-shoot that page on its own.
- A native `title` tooltip and the open list of a native `<select>` cannot be captured; outline the element that opens them instead.
- The floating AI assistant button can end up in the corner of a frame. Scenes hide it with `s.page.addStyleTag({ content: '[class*="_fab_"]{visibility:hidden !important}' })` where it gets in the way.
- Open each changed picture itself before committing; the contact sheets from `preview.mjs` are too small to judge outlines and text.

### Demo clips

`scripts/manual-shots/clips.mjs` records the short videos of the README and of the public site's showcase, against the same frontend build and mock API:

```bash
cd website
FFMPEG=/path/to/ffmpeg node scripts/manual-shots/clips.mjs        # every clip, en and zh-TW
node scripts/manual-shots/clips.mjs --lang en firewall            # one clip in one language
```

- Each file in `scripts/manual-shots/clips/` scripts one flow. Headless Chrome paints no mouse pointer, so a drawn cursor glides to every click (`rec.click`, `rec.type`, `rec.drag`), and `rec.speed(n)` plays a waiting part n times faster.
- Output: `site/public/demo/<lang>/<clip>.mp4` with a poster `.jpg` for the public site, and for English `.github/readme/<clip>.gif` for the README. ffmpeg needs libx264.
- Clips run with a running clock (`clockRuns`): pages that poll for progress compare when data arrived, which never changes while the clock is frozen as it is for the manual screenshots.
- Watch every clip before committing. The screencast only delivers frames when the page repaints, so a clip that waited for the wrong thing looks like a frozen screen instead of failing.

## Deployment

The Cloudflare Pages project `skylab-docs` is connected to the GitHub repository (Git integration), so Cloudflare builds and deploys the site by itself. Pushes to `main` become the production deployment; pull requests from this repository get a preview at `<branch>.skylab-docs.pages.dev`; pull requests from forks get no preview. The build command is `website/scripts/cloudflare-pages-build.sh`:

1. Fetch the full git history. Pages clones shallowly, and the changelog page is generated from the history.
2. Install uv if it is missing, run `uv sync` in `backend/` and export `openapi.json`.
3. Run `bun install --frozen-lockfile` and `bun run build` in `website/` (the build generates the environment and changelog pages first).

`.github/workflows/docs-site.yml` runs the same script as a check on `main` and on pull requests, including those from forks. It does not deploy.

Setting it up once, in Workers & Pages → Create → Pages → Connect to Git:

1. Install the Cloudflare Workers & Pages GitHub App on the repository and select `SkyLab-tw/SkyLab`. An owner of the organization, or a member with the GitHub Apps Manager role, has to do this.
2. Project name `skylab-docs`, production branch `main`, framework preset None.
3. Build command `bash website/scripts/cloudflare-pages-build.sh`, build output directory `website/.vitepress/dist`, root directory left empty.
4. Environment variables `BUN_VERSION` = `1.4.4`, the Bun version of the lock file and of CI (the build image's default is older), and `SKIP_DEPENDENCY_INSTALL` = `1`, because the script installs the dependencies itself.
5. After the first deploy, open Settings → Build → Build watch paths and set the include paths to `website/*, backend/app/*, backend/scripts/export_openapi.py, .env.example`, so that commits touching only other parts of the repository do not rebuild the documentation.
6. Open Custom domains and add `docs.skylab-tw.com`; the DNS record is created automatically when the zone is on Cloudflare.
7. After the first production deploy, add the site to Google Search Console and submit `https://docs.skylab-tw.com/sitemap.xml`.

A Pages project created with Direct Upload cannot be switched to Git integration later; delete it and create the project again with Connect to Git. The same applies when the repository moves to another owner (as it did from `ntubclass` to `SkyLab-tw` in October 2026): the project keeps pointing at the old location and stops building, so install the GitHub App on the new organization and create the project again, then move the custom domain over.

### SEO

VitePress prerenders every page. `.vitepress/config.mts` adds the rest, with absolute URLs on `https://docs.skylab-tw.com` (the `DOCS_URL` environment variable overrides it):

- `sitemap.xml`, where every page lists its English and 繁體中文 versions as hreflang alternates, and `robots.txt` pointing to it.
- On every page a canonical URL, hreflang links to the other language when the page exists in both, and Open Graph / Twitter card tags with the share image `public/og-image.jpg` or `og-image-zh-TW.jpg` (1200×630 screenshots of the home page; retake them when it changes). The 404 page is `noindex`.
- `public/_headers` sends `X-Robots-Tag: noindex` on `skylab-docs.pages.dev` and the preview addresses, so only `docs.skylab-tw.com` is indexed.
- On every English page that has a 繁體中文 version, an inline script sends readers whose browser prefers Chinese to that version before the page shows (`theme/languagePreference.ts`). A language picked in the language menu is remembered and wins from then on. 繁體中文 pages never redirect and crawlers are not redirected, so both versions stay indexable.
- A description per page: front matter `description` when present, the hero tagline on the home pages, otherwise the first paragraph of the page as plain text, shortened to what search results show.

## Conventions

- English page first, then the `zh-TW/` counterpart at the same path, translated by hand sentence by sentence; identical commands, tables and heading structure.
- A new page needs a sidebar entry in `.vitepress/config.mts` for both languages.
- Mermaid diagrams (```` ```mermaid ```` fences) and the API reference are rendered in the browser from CDN scripts; the site has no build dependency on either.
- Placeholders that look like HTML tags must be in backticks; `{{` outside code is read as a Vue expression.
