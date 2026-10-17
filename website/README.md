# SkyLab documentation site

> **English** | [繁體中文](./README.zh-TW.md)

All documentation of the repository lives in this directory and is published as a [VitePress](https://vitepress.dev/) site on Cloudflare Pages by `.github/workflows/docs-site.yml`. The only Markdown files kept outside are the root `README.md` and the files GitHub itself reads (`CONTRIBUTING`, `SECURITY`, `CODE_OF_CONDUCT`, `THIRD_PARTY_NOTICES`, the `.github/` templates).

## Layout

| Directory | Content |
| --- | --- |
| `index.md`, `getting-started.md`, `concepts.md`, `troubleshooting.md` | Home and the introduction pages |
| `manual/` | User manual, one page per feature; `manual/index.md` groups them by role |
| `guides/` | Operations, development and design guides (formerly `docs/`), `guides/index.md` is the index, `guides/archive/` the dated reports |
| `operations/` | Upgrade and backup guides; `operations/environment.md` is generated |
| `reference/` | REST API (`public/openapi.json` rendered with Scalar), WebSocket endpoints, governance configuration, username policy |
| `development/` | Internationalisation |
| `project/` | License page |
| `changelog.md` | Generated from the git history |
| `zh-TW/` | The same tree in 繁體中文 |
| `public/` | Favicon, `openapi.json`, the SkyLab Connect demo under `public/demos/` |
| `.vitepress/` | Configuration (navigation and both sidebars in `config.mts`), theme components (Scalar and mermaid renderers) |
| `scripts/generate.mjs` | Builds the generated pages before every build |

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

## Deployment

The workflow runs on pushes to `main` and on pull requests that touch `website/`, `backend/app/`, `.env.example` or the export script:

1. `uv sync` in `backend/` and export `openapi.json`.
2. `bun install --frozen-lockfile` and `bun run build` in `website/` (the build generates the environment and changelog pages first; the checkout uses the full git history for the changelog).
3. `wrangler pages deploy` to the Cloudflare Pages project `skylab-docs`. Pushes to `main` become the production deployment; pull requests from this repository get a preview at `<branch>.skylab-docs.pages.dev`. Pull requests from forks are built but not deployed.

Setting it up once:

1. In the Cloudflare dashboard create an API token with the "Cloudflare Pages: Edit" permission and note the account id.
2. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` as repository secrets. Without them the workflow builds the site and skips the deploy.
3. The first deploy creates the project. Then open Workers & Pages → `skylab-docs` → Custom domains and add the documentation domain; the DNS record is created automatically when the zone is on Cloudflare.
4. Set the production branch of the project to `main` so preview and production do not swap.

To change the project name, edit `CLOUDFLARE_PAGES_PROJECT` in the workflow.

## Conventions

- English page first, then the `zh-TW/` counterpart at the same path, translated by hand sentence by sentence; identical commands, tables and heading structure.
- A new page needs a sidebar entry in `.vitepress/config.mts` for both languages.
- Mermaid diagrams (```` ```mermaid ```` fences) and the API reference are rendered in the browser from CDN scripts; the site has no build dependency on either.
- Placeholders that look like HTML tags must be in backticks; `{{` outside code is read as a Vue expression.
