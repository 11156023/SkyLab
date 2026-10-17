# SkyLab documentation site

> **English** | [繁體中文](./README.zh-TW.md)

The public documentation site, built with [VitePress](https://vitepress.dev/) and deployed to Cloudflare Pages by `.github/workflows/docs-site.yml`.

## What is on the site

| Section | Source |
| --- | --- |
| Home, Getting started, Concepts, Troubleshooting, WebSocket reference | Written here: `index.md`, `getting-started.md`, `concepts.md`, `troubleshooting.md`, `reference/websocket.md`, and their `zh-TW/` counterparts |
| Guides | Copied from [`../docs/`](../docs/README.md) by `scripts/sync-docs.mjs` into `guides/` and `zh-TW/guides/` (gitignored). Edit the files under `docs/`, never the copies. |
| REST API | `reference/api.md` renders `public/openapi.json` with [Scalar](https://github.com/scalar/scalar). The schema is exported from the backend by `backend/scripts/export_openapi.py` (gitignored). |

`.vitepress/config.mts` holds the navigation and both sidebars; a new guide under `docs/` needs an entry there. Mermaid diagrams and the API reference are rendered in the browser from CDN scripts, so the site has no build dependency on either.

## Working locally

```bash
# optional: API reference (otherwise the page explains how to generate it)
cd backend
uv run --no-sync python scripts/export_openapi.py ../website/public/openapi.json

cd ../website
bun install
bun run dev        # http://localhost:5173, re-syncs docs/ on start
bun run build      # .vitepress/dist
bun run preview
```

`bun run sync` copies the guides without starting anything. The export script imports the FastAPI app without starting it; it needs the project `.env` (or the same variables in the environment) but no database, Redis or Proxmox.

## Deployment

The workflow runs on pushes to `main` and on pull requests that touch `docs/`, `website/`, `backend/app/` or the export script:

1. `uv sync` in `backend/` and export `openapi.json`.
2. `bun install --frozen-lockfile` and `bun run build` in `website/`.
3. `wrangler pages deploy` to the Cloudflare Pages project `skylab-docs`. Pushes to `main` become the production deployment; pull requests from this repository get a preview at `<branch>.skylab-docs.pages.dev`. Pull requests from forks are built but not deployed.

Setting it up once:

1. In the Cloudflare dashboard create an API token with the "Cloudflare Pages: Edit" permission and note the account id.
2. Add `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` as repository secrets. Without them the workflow builds the site and skips the deploy.
3. The first deploy creates the project. Then open Workers & Pages → `skylab-docs` → Custom domains and add the documentation domain; the DNS record is created automatically when the zone is on Cloudflare.
4. Set the production branch of the project to `main` so preview and production do not swap.

To change the project name, edit `CLOUDFLARE_PAGES_PROJECT` in the workflow.

## Conventions

- Pages written here follow the same rule as `docs/`: English first, a `zh-TW/` counterpart translated by hand, identical commands and tables.
- Keep the sidebars in `config.mts` in the same order as `docs/README.md`.
- The `editLink` of a synced guide points at the file under `docs/` through the `source` frontmatter that the sync script writes.
