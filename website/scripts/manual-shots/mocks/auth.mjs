// Shared data for the "auth" group (login, account, home): stand-ins for the
// Google sign-in and Cloudflare Turnstile scripts, TOTP setup data, and a
// generated avatar image.

const G_LOGO = `<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>`;

// Renders a button that looks like the Google button and calls the page's
// callback with a fake credential when clicked (no request leaves the browser).
export function gsiScript(label) {
  return `(() => {
  let cfg = null;
  window.google = { accounts: { id: {
    initialize(c) { cfg = c; },
    prompt() {}, disableAutoSelect() {}, cancel() {},
    renderButton(el, opts) {
      const w = Math.max(200, Math.min(400, opts && opts.width || el.clientWidth || 360));
      el.innerHTML = '<div role="button" tabindex="0" data-fake-gsi style="box-sizing:border-box;width:' + w + 'px;max-width:100%;height:40px;margin:0 auto;display:flex;align-items:center;gap:12px;padding:0 12px;border:1px solid #dadce0;border-radius:4px;background:#fff;color:#3c4043;font:500 14px Roboto,Arial,sans-serif;letter-spacing:.25px;cursor:pointer">' +
        ${JSON.stringify(G_LOGO)} + '<span style="flex:1;text-align:center;margin-right:18px">' + ${JSON.stringify(label)} + '</span></div>';
      el.querySelector('[data-fake-gsi]').addEventListener('click', () => cfg && cfg.callback({ credential: 'mock.google.id-token' }));
    },
  } } };
})();`;
}

// A Turnstile stand-in: an unchecked box until clicked, then "Success!".
export function turnstileScript(lang) {
  const text = lang === "en"
    ? { ask: "Verify you are human", ok: "Success!" }
    : { ask: "確認您是人類", ok: "成功！" };
  return `(() => {
  const widgets = {};
  let n = 0;
  const draw = (w) => {
    const done = w.done;
    w.el.innerHTML = '<div data-fake-turnstile style="box-sizing:border-box;width:100%;height:65px;display:flex;align-items:center;gap:12px;padding:0 14px;border:1px solid #e0e0e0;border-radius:4px;background:#fafafa;color:#232323;font:14px -apple-system,Segoe UI,Roboto,sans-serif;cursor:pointer">' +
      (done
        ? '<span style="width:28px;height:28px;border-radius:50%;background:#1a9e4a;display:grid;place-items:center;color:#fff;font-weight:700">&#10003;</span><span>' + ${JSON.stringify(text.ok)} + '</span>'
        : '<span data-fake-turnstile-box style="width:24px;height:24px;border:2px solid #767676;border-radius:3px;background:#fff"></span><span>' + ${JSON.stringify(text.ask)} + '</span>') +
      '<span style="margin-left:auto;text-align:right;line-height:1.25"><b style="display:block;font-size:11px;letter-spacing:.06em;color:#4b4b4b">CLOUDFLARE</b><small style="font-size:10px;color:#6b6b6b">Privacy · Terms</small></span></div>';
    if (!done) w.el.firstChild.addEventListener('click', () => { w.done = true; draw(w); w.opts.callback && w.opts.callback('mock-turnstile-token-' + w.id); });
  };
  window.turnstile = {
    render(el, opts) { const id = 'w' + (++n); widgets[id] = { id, el, opts, done: false }; draw(widgets[id]); return id; },
    reset(id) { const w = widgets[id]; if (w) { w.done = false; draw(w); } },
    remove(id) { const w = widgets[id]; if (w) { w.el.innerHTML = ''; delete widgets[id]; } },
    getResponse() { return ''; },
  };
})();`;
}

// Serves both third-party scripts from the scene so nothing external is loaded.
export async function fakeExternals(s) {
  const label = s.t("Sign in with Google", "使用 Google 帳戶登入");
  await s.page.route(/accounts\.google\.com\/gsi\/client/, (r) =>
    r.fulfill({ contentType: "text/javascript", body: gsiScript(label) }));
  await s.page.route(/challenges\.cloudflare\.com\/turnstile/, (r) =>
    r.fulfill({ contentType: "text/javascript", body: turnstileScript(s.lang) }));
}

// s.shot() that always begins a new frame (the state change is inside a small card).
export async function shotNew(s, step, target, opts = {}) {
  await s.shot(step, target, { ...opts, newFrame: true });
}

export const TOTP_SECRET = "K5QW4ZBAMNSXEZDJNZTSA2LOEB2GQZJA";

export function totpSetup(email) {
  return {
    secret: TOTP_SECRET,
    otpauth_uri: `otpauth://totp/SkyLab:${encodeURIComponent(email)}?secret=${TOTP_SECRET}&issuer=SkyLab&digits=6&period=30`,
    account: email,
    issuer: "SkyLab",
  };
}

// Draws a simple portrait-style avatar in the page and returns it as a PNG buffer.
export async function avatarPng(s) {
  const dataUrl = await s.page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = c.height = 512;
    const g = c.getContext("2d");
    const bg = g.createLinearGradient(0, 0, 512, 512);
    bg.addColorStop(0, "#7dd3fc");
    bg.addColorStop(1, "#6366f1");
    g.fillStyle = bg;
    g.fillRect(0, 0, 512, 512);
    g.fillStyle = "#fde7d6";
    g.beginPath(); g.arc(256, 210, 96, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#3b2f2f";
    g.beginPath(); g.arc(256, 180, 100, Math.PI, 0); g.fill();
    g.fillStyle = "#1e3a8a";
    g.beginPath(); g.ellipse(256, 520, 190, 190, 0, Math.PI, 0); g.fill();
    return c.toDataURL("image/png");
  });
  return { dataUrl, buffer: Buffer.from(dataUrl.split(",")[1], "base64") };
}

// A wide landscape-style picture used as a custom background image.
export async function landscapePng(s) {
  const dataUrl = await s.page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 1600; c.height = 900;
    const g = c.getContext("2d");
    const sky = g.createLinearGradient(0, 0, 0, 900);
    sky.addColorStop(0, "#fbc2a4"); sky.addColorStop(0.55, "#f7d9c4"); sky.addColorStop(1, "#c7d9e8");
    g.fillStyle = sky; g.fillRect(0, 0, 1600, 900);
    g.fillStyle = "rgba(255,255,255,.75)";
    g.beginPath(); g.arc(1180, 260, 90, 0, Math.PI * 2); g.fill();
    const hill = (y, color, amp) => {
      g.fillStyle = color; g.beginPath(); g.moveTo(0, 900);
      for (let x = 0; x <= 1600; x += 20) g.lineTo(x, y + Math.sin(x / 210) * amp + Math.cos(x / 90) * amp * 0.3);
      g.lineTo(1600, 900); g.fill();
    };
    hill(560, "#93a8c4", 50); hill(650, "#6f88a8", 40); hill(760, "#4c6688", 30);
    return c.toDataURL("image/png");
  });
  return { dataUrl, buffer: Buffer.from(dataUrl.split(",")[1], "base64") };
}
