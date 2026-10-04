/**
 * 把章魚掛到一張 canvas 上：跑動畫迴圈、接頁面與滑鼠事件、跟著深色模式換色。
 * 只有長相變了才重畫（sceneKey），常駐在畫面上也不太耗電。
 */
import { LAYOUT, brainEvent, createBrain, poseOf, stepBrain } from "./octoBrain";
import { drawScene, sceneKey } from "./octoDraw";

// 游標在這個距離內才盯著看，太遠就自己東張西望
const LOOK_RANGE_PX = 360;

const NOOP = { setActivity() {}, destroy() {} };

function readTheme(el) {
  const cs = getComputedStyle(el);
  const v = (name) => cs.getPropertyValue(name).trim();
  return {
    cap: v("--octo-cap"),
    lid: v("--octo-lid"),
    fx: v("--octo-fx"),
    heart: v("--octo-heart"),
    ink: v("--octo-ink"),
    shadow: v("--octo-shadow"),
    bubbleLine: v("--octo-bubble-line"),
    bubbleFill: v("--octo-bubble-fill"),
    bubbleDot: v("--octo-bubble-dot"),
  };
}

export function mountOctoPet(canvas, root, { scale = 3, activity = "idle", walkRange = 0, quiet = false } = {}) {
  // 測試環境（jsdom）或不支援 canvas 時就靜靜地不畫
  if (!canvas || !root || typeof window.requestAnimationFrame !== "function") return NOOP;
  let ctx = null;
  try {
    ctx = canvas.getContext("2d");
  } catch {
    ctx = null;
  }
  if (!ctx) return NOOP;

  const { CW, CH, OX, OY } = LAYOUT;
  const reducedMq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const brain = createBrain(performance.now());
  let current = activity;
  let theme = readTheme(root);
  let pointer = null;
  let lastKey = "";
  let lastX = 0;
  let dpr = 0;

  function fitBackingStore() {
    const next = Math.max(1, window.devicePixelRatio || 1);
    if (next === dpr) return;
    dpr = next;
    canvas.width = Math.round(CW * scale * dpr);
    canvas.height = Math.round(CH * scale * dpr);
    lastKey = "";
  }
  fitBackingStore();
  brainEvent(brain, "mount", performance.now());

  const markActive = () => brainEvent(brain, "active", performance.now());
  const onPagePointer = (e) => {
    pointer = { x: e.clientX, y: e.clientY };
    markActive();
  };
  let rubFrom = null;
  const onEnter = () => brainEvent(brain, "hover", performance.now(), true);
  const onLeave = () => {
    rubFrom = null;
    brainEvent(brain, "hover", performance.now(), false);
  };
  // 在章魚身上來回滑動 = 摸頭
  const onRub = (e) => {
    if (rubFrom) brainEvent(brain, "rub", performance.now(), Math.abs(e.clientX - rubFrom.x) + Math.abs(e.clientY - rubFrom.y));
    rubFrom = { x: e.clientX, y: e.clientY };
  };
  const onThemeChange = () => {
    theme = readTheme(root);
    lastKey = "";
  };

  document.addEventListener("pointermove", onPagePointer, { passive: true });
  document.addEventListener("keydown", markActive);
  document.addEventListener("scroll", markActive, { passive: true, capture: true });
  window.addEventListener("resize", fitBackingStore);
  root.addEventListener("pointerenter", onEnter);
  root.addEventListener("pointerleave", onLeave);
  root.addEventListener("pointermove", onRub);
  // 深色模式切的是 body.dark
  const themeObserver = typeof MutationObserver === "function" ? new MutationObserver(onThemeChange) : null;
  themeObserver?.observe(document.body, { attributes: true, attributeFilter: ["class"] });

  function pointerLook() {
    if (!pointer) return null;
    const rect = canvas.getBoundingClientRect();
    const dx = pointer.x - (rect.left + (OX + 10) * scale);
    const dy = pointer.y - (rect.top + (OY + 7) * scale);
    if (Math.hypot(dx, dy) > LOOK_RANGE_PX) return null;
    const threshold = 6 * scale;
    return dx > threshold ? 1 : dx < -threshold ? -1 : 0;
  }

  let raf = 0;
  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, Math.max(0, (now - last) / 1000));
    last = now;
    stepBrain(brain, dt, now, { activity: current, reduced: Boolean(reducedMq?.matches), pointerLook: pointerLook(), walkRange, quiet });
    const pose = poseOf(brain);
    if (pose.x !== lastX) {
      lastX = pose.x;
      root.style.transform = pose.x ? `translateX(${pose.x * scale}px)` : "";
    }
    const key = sceneKey(pose, brain.parts);
    if (key !== lastKey) {
      lastKey = key;
      drawScene(ctx, { u: scale * dpr, pose, parts: brain.parts, theme });
    }
    raf = window.requestAnimationFrame(frame);
  }
  raf = window.requestAnimationFrame(frame);

  return {
    setActivity(next) {
      current = next;
    },
    destroy() {
      window.cancelAnimationFrame(raf);
      document.removeEventListener("pointermove", onPagePointer);
      document.removeEventListener("keydown", markActive);
      document.removeEventListener("scroll", markActive, { capture: true });
      window.removeEventListener("resize", fitBackingStore);
      root.removeEventListener("pointerenter", onEnter);
      root.removeEventListener("pointerleave", onLeave);
      root.removeEventListener("pointermove", onRub);
      themeObserver?.disconnect();
    },
  };
}
