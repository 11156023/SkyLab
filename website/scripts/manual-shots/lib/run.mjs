// Browser, static server and the session object the scene files drive.

import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright-core";
import { NOW, createMock } from "./mock.mjs";
import { registerCommon, fakeJwt } from "../mocks/common.mjs";
import { SHOTS_DIR, WEBSITE, shotName } from "./manual.mjs";

export const VIEWPORT = { width: 1440, height: 900 };
export const DEBUG_DIR = path.join(WEBSITE, "scripts", "manual-shots", ".debug");

// ---------------------------------------------------------------- static server

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
  ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".wasm": "application/wasm",
  ".glb": "model/gltf-binary", ".mp4": "video/mp4", ".webmanifest": "application/manifest+json",
};

// Serves the built frontend; unknown paths get index.html (client-side routes).
export function startServer(dist) {
  if (!fs.existsSync(path.join(dist, "index.html"))) {
    throw new Error(`No frontend build at ${dist}. Run "bun run build" in frontend/ first, or pass --dist.`);
  }
  const server = http.createServer((req, res) => {
    const url = decodeURIComponent(new URL(req.url, "http://x").pathname);
    let file = path.join(dist, url);
    if (!file.startsWith(dist) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, "index.html");
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ url: `http://127.0.0.1:${server.address().port}`, close: () => server.close() }));
  });
}

// ---------------------------------------------------------------- browser

function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const roots = [
    process.env.PLAYWRIGHT_BROWSERS_PATH,
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "ms-playwright"),
    path.join(os.homedir(), ".cache", "ms-playwright"),
    path.join(os.homedir(), "Library", "Caches", "ms-playwright"),
  ].filter(Boolean);
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    const dirs = fs.readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse();
    for (const d of dirs) {
      for (const rel of ["chrome-win64/chrome.exe", "chrome-linux64/chrome", "chrome-linux/chrome", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"]) {
        const p = path.join(root, d, rel);
        if (fs.existsSync(p)) return p;
      }
    }
  }
  return null; // fall back to an installed Google Chrome
}

export async function launchBrowser() {
  const executablePath = findChrome();
  return chromium.launch({
    ...(executablePath ? { executablePath } : { channel: "chrome" }),
    headless: true,
    args: ["--hide-scrollbars", "--font-render-hinting=none", "--disable-gpu-vsync"],
  });
}

// Turns off first-visit tours, help bubbles and the notification prompt, picks
// the UI language and moves toasts to the top centre (away from header buttons).
const initScript = (lang, role) => `(() => {
  try {
    const g = Storage.prototype.getItem;
    Storage.prototype.getItem = function (k) {
      if (typeof k === "string") {
        if (k.startsWith("skylab:page-help-hint:")) return "seen";
        if (k.startsWith("skylab:user-guide:seen:")) return "completed";
        if (k === "jobs:desktopNotificationsPromptDismissed") return "1";
        if (k === "skylab.lang") return ${JSON.stringify(lang)};
      }
      return g.call(this, k);
    };
    ${role ? `if (!localStorage.getItem("auth_session_id")) {
      localStorage.setItem("access_token", ${JSON.stringify(fakeJwt(role))});
      localStorage.setItem("refresh_token", "rt.${role}");
    }` : ""}
  } catch {}
  const addStyle = () => {
    if (!document.head) return setTimeout(addStyle, 10);
    const st = document.createElement("style");
    st.textContent = "[data-sonner-toaster]{left:50% !important;right:auto !important;transform:translateX(-50%) !important}" +
      "*,*::before,*::after{caret-color:transparent !important}";
    document.head.appendChild(st);
  };
  addStyle();
})();`;

// ---------------------------------------------------------------- webp encoder

let encoderPage = null;
async function toWebp(browser, png, quality) {
  if (!encoderPage) {
    const ctx = await browser.newContext();
    encoderPage = await ctx.newPage();
  }
  const dataUrl = await encoderPage.evaluate(async ({ b64, q }) => {
    const img = new Image();
    img.src = "data:image/png;base64," + b64;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    c.getContext("2d").drawImage(img, 0, 0);
    return c.toDataURL("image/webp", q);
  }, { b64: png.toString("base64"), q: quality });
  return Buffer.from(dataUrl.split(",")[1], "base64");
}
export const resetEncoder = () => { encoderPage = null; };

// ---------------------------------------------------------------- session

// Runs one manual section in a fresh browser context.
// opts: { browser, baseUrl, lang, page, section, role, mocks, scene }
export async function runSection({ browser, baseUrl, lang, page, section, role, mocks }) {
  const m = createMock(lang);
  registerCommon(m);
  if (role) m.state.role = role;
  if (mocks) mocks(m);

  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    locale: lang === "en" ? "en-US" : "zh-TW",
    timezoneId: "Asia/Taipei",
    colorScheme: "light",
    reducedMotion: "reduce",
  });
  await context.clock.setFixedTime(new Date(NOW));
  await context.addInitScript({ content: initScript(lang, role ? m.users[role].id : null) });
  await context.route(/\/api\/v1\//, async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    const method = req.method();
    if (method === "OPTIONS") return route.fulfill({ status: 204 });
    const hit = m.match(method, u.pathname);
    if (!hit) {
      const k = `${method} ${u.pathname}`;
      m.unmatched.set(k, (m.unmatched.get(k) || 0) + 1);
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "mock: not found" }) });
    }
    let body = null;
    const raw = req.postData();
    if (raw) {
      try { body = JSON.parse(raw); } catch { body = Object.fromEntries(new URLSearchParams(raw)); }
    }
    let res;
    try {
      res = await hit.handler({ mt: hit.mt, url: u, body, query: Object.fromEntries(u.searchParams), headers: req.headers(), method, state: m.state });
    } catch (e) {
      console.log(`  mock handler error ${method} ${u.pathname}: ${e.message}`);
      return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: e.message }) });
    }
    if (res && typeof res === "object" && ("__status" in res || "__body" in res || "__delay" in res || "__text" in res)) {
      if (res.__delay) await new Promise((r) => setTimeout(r, res.__delay));
      if (res.__text !== undefined) return route.fulfill({ status: res.__status ?? 200, contentType: res.__contentType ?? "text/plain; charset=utf-8", body: res.__text });
      return route.fulfill({ status: res.__status ?? 200, contentType: "application/json", body: JSON.stringify(res.__body ?? null) });
    }
    return route.fulfill({ status: res === undefined ? 204 : 200, contentType: "application/json", body: res === undefined ? "" : JSON.stringify(res) });
  });
  await context.routeWebSocket(/\/ws\//, (ws) => { if (m.ws) m.ws(new URL(ws.url()).pathname, ws); });

  const p = await context.newPage();
  p.setDefaultTimeout(10000);
  const s = new Session({ browser, page: p, baseUrl, lang, pageName: page, section, mock: m });
  return { s, context, mock: m };
}

const HL_ID = "__manual_shot_overlay";

export class Session {
  constructor({ browser, page, baseUrl, lang, pageName, section, mock }) {
    this.browser = browser;
    this.page = page;
    this.baseUrl = baseUrl;
    this.lang = lang;
    this.pageName = pageName;
    this.section = section;
    this.m = mock;
    this.written = [];
  }

  // Text for the current UI language, for locating elements by their label.
  t(en, zh) {
    return this.lang === "en" ? en : zh;
  }

  // Waits until requests stop, fonts are loaded and the layout is still.
  async settle(ms = 350) {
    await this.page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
    await this.page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
    await this.page.waitForTimeout(ms);
  }

  async go(route, { wait = 500 } = {}) {
    await this.page.goto(this.baseUrl + route);
    await this.settle(wait);
  }

  async click(target, { wait = 350 } = {}) {
    await target.click();
    await this.settle(wait);
  }

  async fill(target, value, { wait = 150 } = {}) {
    await target.fill(value);
    await this.settle(wait);
  }

  async press(key, { wait = 250 } = {}) {
    await this.page.keyboard.press(key);
    await this.settle(wait);
  }

  // Saves the screenshot for step `step` of this section.
  //   target: Locator | Locator[] | null   elements to outline (null: no outline)
  //   opts.crop: "auto" (default) | "viewport" | Locator | { x, y, width, height }
  //     auto = the open dialog the target is in, else an area of at least
  //     opts.min ([width, height], default [960, 560]) around the targets.
  //   opts.pad: margin around the crop area in px (default 24)
  //   opts.scroll: scroll the first target into view first (default true)
  async shot(step, target = null, opts = {}) {
    const targets = (Array.isArray(target) ? target : target ? [target] : []).filter(Boolean);
    if (targets.length && opts.scroll !== false) await targets[0].scrollIntoViewIfNeeded().catch(() => {});
    await this.page.waitForTimeout(150);
    const boxes = [];
    for (const t of targets) {
      const b = await t.boundingBox();
      if (!b) throw new Error(`shot ${step}: target not visible (${t})`);
      boxes.push(b);
    }

    const vp = this.page.viewportSize();
    const pad = opts.pad ?? 24;
    let region;
    if (opts.crop === "viewport") region = { x: 0, y: 0, width: vp.width, height: vp.height };
    else if (opts.crop && typeof opts.crop === "object" && "x" in opts.crop) region = { ...opts.crop };
    else if (opts.crop) {
      const b = await opts.crop.boundingBox();
      if (!b) throw new Error(`shot ${step}: crop element not visible`);
      region = grow(b, pad);
    } else {
      const dialog = await this.dialogBox(targets[0]);
      if (dialog) region = grow(union([dialog, ...boxes]), 16);
      else if (boxes.length) {
        const [mw, mh] = opts.min ?? [960, 560];
        const u = grow(union(boxes), pad);
        const w = Math.max(u.width, mw);
        const h = Math.max(u.height, mh);
        region = { x: u.x + u.width / 2 - w / 2, y: u.y + u.height / 2 - h / 2, width: w, height: h };
      } else region = { x: 0, y: 0, width: vp.width, height: vp.height };
    }
    region = clamp(region, vp);

    if (boxes.length && opts.highlight !== false) await this.drawOutlines(boxes);
    const png = await this.page.screenshot({ clip: region, animations: "disabled", caret: "hide" });
    await this.page.evaluate((id) => document.getElementById(id)?.remove(), HL_ID);

    const dir = path.join(SHOTS_DIR, this.lang, this.pageName);
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${shotName(this.section, step)}.webp`);
    fs.writeFileSync(file, await toWebp(this.browser, png, opts.quality ?? 0.82));
    this.written.push(file);
  }

  async dialogBox(target) {
    const handle = target
      ? await target.evaluateHandle((el) => el.closest('[role="dialog"],[role="alertdialog"],[aria-modal="true"]'))
      : await this.page.evaluateHandle(() => {
          const all = [...document.querySelectorAll('[role="dialog"],[role="alertdialog"],[aria-modal="true"]')];
          return all.filter((d) => d.getBoundingClientRect().width > 0).pop() || null;
        });
    const el = handle.asElement();
    if (!el) return null;
    const b = await el.boundingBox();
    // A dialog that fills the viewport is a page-like panel: frame the target instead.
    if (!b || (b.width > 1300 && b.height > 800)) return null;
    return b;
  }

  async drawOutlines(boxes) {
    await this.page.evaluate(({ id, boxes }) => {
      const root = document.createElement("div");
      root.id = id;
      root.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
      for (const b of boxes) {
        const d = document.createElement("div");
        d.style.cssText = `position:absolute;left:${b.x - 5}px;top:${b.y - 5}px;width:${b.width + 10}px;height:${b.height + 10}px;` +
          "border:3px solid #f97316;border-radius:10px;box-shadow:0 0 0 4px rgba(249,115,22,.22),0 6px 18px rgba(249,115,22,.25);box-sizing:border-box";
        root.appendChild(d);
      }
      document.body.appendChild(root);
    }, { id: HL_ID, boxes });
  }
}

function union(bs) {
  const x = Math.min(...bs.map((b) => b.x));
  const y = Math.min(...bs.map((b) => b.y));
  return { x, y, width: Math.max(...bs.map((b) => b.x + b.width)) - x, height: Math.max(...bs.map((b) => b.y + b.height)) - y };
}
const grow = (b, p) => ({ x: b.x - p, y: b.y - p, width: b.width + 2 * p, height: b.height + 2 * p });
function clamp(r, vp) {
  const width = Math.min(r.width, vp.width);
  const height = Math.min(r.height, vp.height);
  const x = Math.min(Math.max(r.x, 0), vp.width - width);
  const y = Math.min(Math.max(r.y, 0), vp.height - height);
  return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) };
}

// Saves a full-viewport PNG when a section fails, for debugging.
export async function debugShot(s, name) {
  fs.mkdirSync(DEBUG_DIR, { recursive: true });
  const file = path.join(DEBUG_DIR, `${name}.png`);
  await s.page.screenshot({ path: file }).catch(() => {});
  return file;
}
