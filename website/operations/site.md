# Public site

The public introduction site lives in `site/` and is published at `https://skylab-tw.com`. It is separate from the platform: each school's SkyLab (for example `ntub.skylab-tw.com`) opens straight on the sign-in page, and schools that want to try SkyLab find the contact e-mail and the GitHub link on the public site.

## What is in it

- The animated introduction (React + GSAP, 繁體中文 / English / 日本語, day and night mode) that used to be the platform's `/` page.
- "Contact us" buttons that open an e-mail to the address in `site/site.config.js`, a link to the GitHub repository and one to the deployment guide on the documentation site.
- A "See it in action" showcase that plays four short clips of the real interface in a browser frame. The clips are recorded with mock data by `website/scripts/manual-shots/clips.mjs` (see `website/README.md`, "Demo clips") into `site/public/demo/<lang>/`; the 繁體中文 page plays the 繁體中文 recordings, the English and 日本語 pages the English ones. They load only when the showcase is near the screen and do not play by themselves for visitors who prefer reduced motion.
- The figures on the page (nodes, classroom seats, firewall rules and so on) are illustrative and come from `site/src/stats.json`.

| Path | Purpose |
| --- | --- |
| `site/src/landing/` | Page components and styles |
| `site/src/locales/{zh-TW,en,ja}.json` | Texts, including the SEO title and description |
| `site/site.config.js` | Site address, documentation address, contact e-mail, GitHub URL (single source) |
| `site/src/seo.js` | Head tags, `sitemap.xml`, `robots.txt` |
| `site/src/entry-server.jsx`, `site/scripts/prerender.mjs` | Prerendering of the three language pages |
| `site/public/` | Favicons, share images, `404.html`, Cloudflare `_headers` |

The styles reuse the frontend's design tokens (`frontend/src/assets/styles`: variables, mixins, themes, reset) through Sass `loadPaths`, so a change there also rebuilds the site.

## Running it locally

```bash
cd site
bun install
bun run dev        # http://127.0.0.1:5180, rendered in the browser; /en/ and /ja/ work too
bun run test       # Vitest
bun run build      # dist/: three prerendered pages, sitemap.xml, robots.txt
bun run preview    # serves dist/; restart it after every build
```

## SEO

- Every language is a prerendered page with its full text in the HTML: `/` (繁體中文), `/en/` and `/ja/`. `bun run build` runs the client build, an SSR build of `src/entry-server.jsx`, then `scripts/prerender.mjs`, which writes the rendered page and its head tags into each `index.html`. The browser hydrates the prerendered HTML.
- Each page has its own title and description, a canonical URL, `hreflang` links to all three languages plus `x-default`, Open Graph and Twitter card tags with a share image in that language, and JSON-LD (`WebSite`, `SoftwareApplication`).
- `sitemap.xml` lists the three pages with their alternates; `robots.txt` allows everything and points to the sitemap.
- Every absolute URL comes from `SITE_URL`, default `https://skylab-tw.com`. To build for another address, set the `SITE_URL` environment variable (on Cloudflare Pages, an environment variable of the project).
- The share images `public/og-image.jpg`, `og-image-en.jpg` and `og-image-ja.jpg` are 1200×630 screenshots of the opening screen without the top bar. Retake them when the opening screen changes; a test fails if one is missing.
- The first render in the browser must match the prerendered HTML, which is drawn for a desktop with motion in night mode. Anything that depends on the browser (window width, reduced motion, a saved day mode) is applied in a layout effect after hydration. `LandingPage.test.jsx` hydrates prerendered HTML and fails on any mismatch.
- Unknown paths get `404.html` with status 404 from Cloudflare Pages.

## Deploying to Cloudflare Pages

The Cloudflare Pages project `skylab-site` is connected to the GitHub repository (Git integration), so Cloudflare runs the tests, builds and deploys by itself. Pushes to `main` become the production deployment; pull requests from this repository get a preview at `<branch>.skylab-site.pages.dev` (previews send `X-Robots-Tag: noindex`); pull requests from forks get no preview. `.github/workflows/site.yml` runs the tests and the build as a check, including for pull requests from forks; it does not deploy.

One-time setup, in Workers & Pages → Create → Pages → Connect to Git:

1. Select `ntubclass/SkyLab`. The Cloudflare Workers & Pages GitHub App has to be installed on the repository; this is the same app the documentation site uses.
2. Project name `skylab-site`, production branch `main`, framework preset None.
3. Build command `cd site && bun install --frozen-lockfile && bun run test && bun run build`, build output directory `site/dist`, root directory left empty (the build reads `frontend/src/assets/styles` outside `site/`).
4. Environment variables `BUN_VERSION` = `1.4.4` and `SKIP_DEPENDENCY_INSTALL` = `1`. Optional: `SITE_URL` if the address is not `https://skylab-tw.com`, and `DOCS_URL` if the documentation site is not at `https://docs.skylab-tw.com`.
5. After the first deploy, open Settings → Build → Build watch paths and set the include paths to `site/*, frontend/src/assets/styles/*`.
6. Open Custom domains and add `skylab-tw.com`. An apex domain needs the `skylab-tw.com` zone to be on Cloudflare. If you also add `www.skylab-tw.com`, redirect it to `https://skylab-tw.com` with a redirect rule so that search engines index a single address.
7. After the first production deploy, add the site to Google Search Console and submit `https://skylab-tw.com/sitemap.xml`.
8. Set up the contact address, `contact@skylab-tw.com`, with Cloudflare Email Routing: in the `skylab-tw.com` zone open Email → Email Routing and enable it (it adds the MX and TXT records), add the team's mailbox under Destination addresses and confirm it from the verification mail, then create a routing rule that sends `contact@skylab-tw.com` to it. Email Routing only receives mail: a reply sent from that mailbox shows the mailbox's own address unless it is set up to send as `contact@skylab-tw.com`.

A Pages project created with Direct Upload cannot be switched to Git integration later; delete it and create the project again with Connect to Git.

A school's platform, such as `ntub.skylab-tw.com`, is not deployed from here; it is a SkyLab installation exposed through its platform entry (see [Deployment](/guides/deployment)).

## Changing the contact e-mail or links

Edit `site/site.config.js`. The buttons, the footer and the JSON-LD all read the contact e-mail and the GitHub URL from it, and it holds the defaults of `SITE_URL` and `DOCS_URL`. Keep a role address there, not a personal one: the page and the git history are public. To hand the inbox to someone else, change the routing rule in Cloudflare; the site does not change.

## Related

- [Internationalisation](/development/i18n): the site's catalogs
- [Deployment](/guides/deployment): installing SkyLab itself
