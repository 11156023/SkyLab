// Demo clips: records a scripted session with Chrome's screencast and encodes it.
//
// Headless Chrome paints no mouse pointer, so a drawn cursor follows the mouse and
// shows a ripple on every click; the pointer helpers below glide to their target
// instead of jumping. Screencast frames arrive only when the page repaints, each
// with its own timestamp, and the encoder turns them into a constant frame rate.
// speed(n) plays everything after it n times faster (waiting for a build), until
// the next speed() call.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// The smallest laptop window at which the pages show their full tables (narrower, the
// machine list scrolls sideways and hides the names), so text stays as large as possible
// when the video is shown smaller (the GIF is 960 px wide). The screencast captures CSS
// pixels: a device scale factor would not add resolution, and screenshots at a higher
// scale are too slow (about 15 per second).
export const CLIP_VIEWPORT = { width: 1280, height: 800 };

// Runs in every page of the clip's browser context (addInitScript).
export function installCursor() {
  const ID = "__clip_cursor";
  const KEY = "__clip_cursor_pos";
  const place = (x, y) => {
    const el = document.getElementById(ID);
    if (el) el.style.transform = `translate(${x - 4}px, ${y - 2}px)`;
  };
  const mount = () => {
    if (!document.body) return setTimeout(mount, 20);
    if (document.getElementById(ID)) return;
    const el = document.createElement("div");
    el.id = ID;
    el.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="26" viewBox="0 0 24 26">' +
      '<path d="M4 2.5V20.5L8.6 16.4L11.7 23.3L15.1 21.8L12.1 15H18.3Z" fill="#111827" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(el.style, {
      position: "fixed", left: "0", top: "0", zIndex: "2147483647", pointerEvents: "none",
      transform: "translate(-100px, -100px)", filter: "drop-shadow(0 2px 3px rgba(0,0,0,.28))",
    });
    document.documentElement.appendChild(el);
    try {
      const saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
      if (saved) place(saved.x, saved.y);
    } catch {}
  };
  mount();
  document.addEventListener("mousemove", (e) => {
    place(e.clientX, e.clientY);
    try { sessionStorage.setItem(KEY, JSON.stringify({ x: e.clientX, y: e.clientY })); } catch {}
  }, true);
  document.addEventListener("mousedown", (e) => {
    const ring = document.createElement("div");
    Object.assign(ring.style, {
      position: "fixed", left: `${e.clientX - 22}px`, top: `${e.clientY - 22}px`, width: "44px", height: "44px",
      borderRadius: "50%", background: "rgba(84,113,191,.35)", border: "2px solid rgba(84,113,191,.8)",
      zIndex: "2147483646", pointerEvents: "none",
    });
    document.documentElement.appendChild(ring);
    ring.animate([{ transform: "scale(.3)", opacity: 1 }, { transform: "scale(1)", opacity: 0 }], { duration: 480, easing: "ease-out" })
      .onfinish = () => ring.remove();
  }, true);
}

const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

export class Recorder {
  constructor(page) {
    this.page = page;
    this.frames = [];
    this.speeds = [];
    this.pos = { x: CLIP_VIEWPORT.width * 0.9, y: CLIP_VIEWPORT.height * 0.92 };
    this.posterAt = null;
  }

  async start() {
    this.dir = fs.mkdtempSync(path.join(os.tmpdir(), "skylab-clip-"));
    await this.page.mouse.move(this.pos.x, this.pos.y);
    this.cdp = await this.page.context().newCDPSession(this.page);
    this.cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
      const file = `f${String(this.frames.length).padStart(5, "0")}.jpg`;
      fs.writeFileSync(path.join(this.dir, file), Buffer.from(data, "base64"));
      this.frames.push({ file, t: metadata.timestamp });
      this.cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
    });
    await this.cdp.send("Page.startScreencast", {
      format: "jpeg", quality: 95, everyNthFrame: 1, maxWidth: CLIP_VIEWPORT.width, maxHeight: CLIP_VIEWPORT.height,
    });
  }

  // Plays what follows `factor` times faster.
  speed(factor) {
    this.speeds.push({ t: Date.now() / 1000, factor });
  }

  // The poster image (shown before a video plays) is the screen at this moment.
  poster() {
    this.posterAt = Date.now() / 1000;
  }

  async stop(holdMs = 1500) {
    await this.page.waitForTimeout(holdMs);
    this.end = Date.now() / 1000;
    await this.cdp.send("Page.stopScreencast");
    await this.cdp.detach();
    if (this.frames.length < 2) throw new Error("the screencast produced no frames");
  }

  // ------------------------------------------------------------ pointer

  async moveTo(target, { duration = 650, dx = 0, dy = 0 } = {}) {
    let x;
    let y;
    if (typeof target?.x === "number") ({ x, y } = target);
    else {
      await target.scrollIntoViewIfNeeded();
      const b = await target.boundingBox();
      if (!b) throw new Error(`moveTo: target not visible (${target})`);
      x = b.x + b.width / 2 + dx;
      y = b.y + b.height / 2 + dy;
    }
    const from = this.pos;
    const steps = Math.max(8, Math.round(duration / 16));
    for (let i = 1; i <= steps; i++) {
      const k = ease(i / steps);
      await this.page.mouse.move(from.x + (x - from.x) * k, from.y + (y - from.y) * k);
      await this.page.waitForTimeout(duration / steps);
    }
    this.pos = { x, y };
  }

  async click(target, { wait = 700, ...move } = {}) {
    await this.moveTo(target, move);
    await this.page.waitForTimeout(120);
    await this.page.mouse.down();
    await this.page.waitForTimeout(90);
    await this.page.mouse.up();
    await this.page.waitForTimeout(wait);
  }

  async type(target, text, { delay = 55, wait = 400 } = {}) {
    await this.click(target, { wait: 200 });
    await target.pressSequentially(text, { delay });
    await this.page.waitForTimeout(wait);
  }

  // Drags from one point to another with the button held (React Flow handles).
  async drag(from, to, { duration = 1100 } = {}) {
    await this.moveTo(from);
    await this.page.mouse.down();
    await this.page.waitForTimeout(150);
    await this.moveTo(to, { duration });
    await this.page.waitForTimeout(200);
    await this.page.mouse.up();
  }

  // ------------------------------------------------------------ encoding

  // Seconds of output video between two wall-clock moments, with the speed changes applied.
  outputSeconds(a, b) {
    const cuts = [{ t: -Infinity, factor: 1 }, ...this.speeds];
    let total = 0;
    for (let i = 0; i < cuts.length; i++) {
      const lo = Math.max(a, cuts[i].t);
      const hi = Math.min(b, cuts[i + 1]?.t ?? Infinity);
      if (hi > lo) total += (hi - lo) / cuts[i].factor;
    }
    return total;
  }

  // outputs: { mp4, poster, gif } file paths (any may be missing).
  encode(outputs, ffmpeg) {
    const lines = ["ffconcat version 1.0"];
    this.frames.forEach((f, i) => {
      const next = this.frames[i + 1]?.t ?? this.end;
      lines.push(`file '${f.file}'`, `duration ${Math.max(this.outputSeconds(f.t, next), 0.001).toFixed(4)}`);
    });
    lines.push(`file '${this.frames[this.frames.length - 1].file}'`);
    fs.writeFileSync(path.join(this.dir, "frames.txt"), lines.join("\n"));
    const input = ["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", "frames.txt"];
    const run = (args) => {
      const r = spawnSync(ffmpeg, args, { cwd: this.dir, encoding: "utf8" });
      if (r.error) throw new Error(`ffmpeg could not start (${r.error.message}); set FFMPEG to the ffmpeg executable`);
      if (r.status !== 0) throw new Error(`ffmpeg failed: ${r.stderr.trim().split("\n").slice(-3).join(" ")}`);
    };
    for (const file of Object.values(outputs)) fs.mkdirSync(path.dirname(file), { recursive: true });
    if (outputs.mp4) {
      run([...input, "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "23",
        "-movflags", "+faststart", "-an", outputs.mp4]);
    }
    if (outputs.poster) {
      const at = this.posterAt ? this.outputSeconds(this.frames[0].t, this.posterAt) : 0;
      run([...input, "-ss", at.toFixed(3), "-frames:v", "1", "-q:v", "4", outputs.poster]);
    }
    if (outputs.gif) {
      run([...input, "-vf",
        "fps=12,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
        "-loop", "0", outputs.gif]);
    }
    fs.rmSync(this.dir, { recursive: true, force: true });
  }
}
