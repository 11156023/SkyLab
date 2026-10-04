/**
 * 章魚學士的行為狀態機：純邏輯、不碰 DOM，畫面由 octoDraw 依 poseOf() 的結果去畫。
 *
 * activity 由外部（例如 AI 助手）決定，章魚依此切換動作：
 *   idle      沒事做：呼吸、眨眼、看游標，沒人理就拿書出來看，整頁沒動靜就睡覺
 *   thinking  AI 回覆中：拿出筆電寫程式，飄出 0 和 1
 *   done      回覆好了但還沒被看：^_^ 跳一下，頭上冒「…」對話泡泡直到被看
 *   error     出錯：嚇一跳噴墨，之後一樣冒泡泡（嘴往上拱）提醒有訊息沒看
 * 點擊交給外層按鈕（打開助手），章魚自己只回應 hover 與在頭上來回滑（摸頭）。
 * walkRange > 0 時，待機的空檔會隨機往左走幾格再走回來（只往左：預設貼在畫面右側）。
 * quiet 時只呼吸、眨眼、晃帽穗、看游標（摸頭照樣冒愛心），不會自己看書、走路、睡覺；放在訊息旁邊用。
 */
import { FLIP, STRETCH_LIFT } from "./octoSprites";

/* 畫布的邏輯格數與章魚原點：上方留空間給 Zzz、對話泡泡，左右留給飄出來的 0/1 */
export const LAYOUT = { CW: 26, CH: 26, OX: 3, OY: 10 };

export const READ_AFTER_MS = 8000;
export const SLEEP_AFTER_MS = 30000;
const STEP_S = 0.16;
const G = 230;
const BUSY = ["read", "code", "stow"];

export function createBrain(now, rand = Math.random) {
  return {
    rand,
    state: "idle", t: 0, after: "idle",
    jy: 0, vy: 0, bigHop: false, squash: 0, shake: 0,
    blink: 0, nextBlink: 2.4, look: 0, wander: 3,
    tassel: 0.1, tv: 0, headDy: 0,
    prop: null, lift: 4, flipT: -1, nextFlip: 3, typeT: 0, glyphT: 0, runT: 0,
    z: 0, hearts: 0,
    idleFor: 8 + rand() * 6, readFor: 12, calmT: 0,
    x: 0, walkTo: 0, walkCd: 4 + rand() * 6, stepT: 0, stepPhase: 0, walkRange: 0,
    hovering: false, rubAcc: 0, rubLast: 0, reduced: false,
    lastPlay: now, lastActive: now,
    activity: "idle",
    parts: [],
  };
}

const airborne = (b) => b.jy < 0 || b.vy < 0;

function hop(b, vy, big = false) {
  if (b.reduced) return;
  b.vy = vy;
  b.jy = -0.01;
  b.bigHop = big;
  b.tv += big ? 6 : 3;
}

function setState(b, s, after = "idle") {
  const prev = b.state;
  b.state = s;
  b.t = 0;
  // 走路跟待機之間來回不算「做了別的事」，看書的倒數不重算
  if (prev !== "walk" && s !== "walk") {
    b.calmT = 0;
    if (s === "idle") b.idleFor = 8 + b.rand() * 6;
  }
  if (s === "walk") { b.stepT = 0; b.stepPhase = 0; }
  if (s === "read" || s === "code") {
    b.prop = s === "read" ? "book" : "laptop";
    b.lift = 4;
    b.flipT = -1;
    b.nextFlip = 2 + b.rand() * 2;
    b.typeT = 0;
    b.glyphT = 0.4;
    b.runT = 0;
    b.readFor = 10 + b.rand() * 8;
    b.look = 0;
  }
  if (s === "stow") b.after = after;
  if (s === "sleep") { b.z = 0.5; b.look = 0; }
  if (s === "pet") b.hearts = 0;
  if (s === "ink") { spawnInk(b); b.shake = 0.35; }
  if (s === "cheer" && b.activity === "done") hop(b, -45, true);
}

// 走去哪：離開原位時有一半機率走回家，不然在範圍內另挑一個至少差 3 格的點
function pickWalkTarget(b) {
  const range = b.walkRange;
  if (b.x !== 0 && b.rand() < 0.5) return 0;
  for (let i = 0; i < 4; i++) {
    const target = -Math.round(b.rand() * range);
    if (Math.abs(target - b.x) >= 3) return target;
  }
  return b.x === 0 ? -range : 0;
}

/* ---------- 粒子（座標是畫布邏輯格） ---------- */
function spawnInk(b) {
  const { OX, OY } = LAYOUT;
  const by = OY + Math.round(b.jy);
  for (let i = 0; i < 16; i++) {
    const side = b.rand() < 0.5 ? -1 : 1;
    b.parts.push({
      kind: "ink", x: OX + 10 + side * (2 + b.rand() * 6), y: by + 12.5 + b.rand() * 1.5,
      vx: side * (6 + b.rand() * 10), vy: -(4 + b.rand() * 12),
      life: 0, max: 2.2 + b.rand() * 0.6, rest: OY + 15, landed: false, big: b.rand() < 0.25,
    });
  }
}
function spawnHeart(b) {
  const { OX, OY } = LAYOUT;
  b.parts.push({ kind: "heart", x: OX + 13 + b.rand() * 3, y: OY + Math.round(b.jy) - 3, vx: (b.rand() - 0.3) * 4, vy: -9, life: 0, max: 1.3 });
}
function spawnZ(b) {
  const { OX, OY } = LAYOUT;
  b.parts.push({ kind: "z", x: OX + 16, y: OY - 3, vx: 1.8, vy: -4, life: 0, max: 2.8 });
}
// 0 和 1 從筆電兩側往外、往上飄，不經過臉
function spawnCode(b) {
  const { OX, OY } = LAYOUT;
  const side = b.rand() < 0.5 ? -1 : 1;
  b.parts.push({
    kind: "code", g: b.rand() < 0.5 ? "0" : "1",
    x: OX + (side < 0 ? b.rand() * 2 : 16 + b.rand() * 2), y: OY + 8,
    vx: side * (1.5 + b.rand() * 2), vy: -(6 + b.rand() * 3), life: 0, max: 1.7,
  });
}

/* ---------- 外部事件 ---------- */
export function brainEvent(b, type, now, value) {
  if (type === "active") {
    b.lastActive = now;
  } else if (type === "mount") {
    // 按鈕重新出現時小跳一下打招呼
    hop(b, -28);
  } else if (type === "hover") {
    b.hovering = value;
    if (!value) return;
    b.lastPlay = b.lastActive = now;
    if (b.state === "sleep") { setState(b, "wake"); hop(b, -32); }
    else if (b.state === "read") setState(b, "stow", "idle");
    else if (b.state === "walk") setState(b, "idle");
  } else if (type === "rub") {
    // value：這次移動的距離（px）；停超過 0.45 秒重新累計
    b.rubAcc = now - b.rubLast > 450 ? 0 : b.rubAcc + value;
    b.rubLast = now;
    b.lastPlay = b.lastActive = now;
    if (b.state === "sleep" && b.rubAcc > 120) { b.rubAcc = 0; setState(b, "wake"); hop(b, -32); }
    else if (b.rubAcc > 420 && (b.state === "idle" || b.state === "cheer")) { b.rubAcc = 0; setState(b, "pet"); }
  }
}

function onActivityChange(b, next) {
  const prev = b.activity;
  b.activity = next;
  if (next === "thinking") {
    if (b.state !== "code") setState(b, "code");
  } else if (next === "done") {
    // 正在敲鍵盤就先「程式跑起來了」一下，再轉成提醒
    if (b.state === "code") b.runT = 1.2;
    else setState(b, "cheer");
  } else if (next === "error") {
    setState(b, "ink");
  } else if (prev === "thinking" && b.state === "code") {
    setState(b, "stow", "idle");
  } else if (b.state === "cheer") {
    setState(b, "idle");
  }
}

/* ---------- 每一格 ---------- */
export function stepBrain(b, dt, now, { activity = "idle", reduced = false, pointerLook = null, walkRange = 0, quiet = false } = {}) {
  b.reduced = reduced;
  b.walkRange = walkRange;
  if (activity !== b.activity) onActivityChange(b, activity);
  b.t += dt;

  if (b.state === "idle" || b.state === "walk") b.calmT += dt;

  // 沒事做時的自主行為：偶爾走走、沒人理就看書、整頁沒動靜就收起來睡覺、有動靜就醒來
  if (b.activity === "idle" && !quiet) {
    const idleMs = now - b.lastActive;
    const calm = b.state === "idle" && !b.hovering && !airborne(b);
    if (["idle", "walk", "read"].includes(b.state) && !airborne(b) && idleMs > SLEEP_AFTER_MS) {
      if (b.state === "read") setState(b, "stow", "sleep");
      else setState(b, "sleep");
    } else if (b.state === "sleep" && idleMs < 300) {
      setState(b, "wake");
      hop(b, -32);
    } else if (calm && now - b.lastPlay > READ_AFTER_MS && b.calmT > b.idleFor) {
      setState(b, "read");
    } else if (b.state === "read" && b.t > b.readFor) {
      setState(b, "stow", "idle");
    } else if (calm && b.walkRange > 0 && !reduced) {
      b.walkCd -= dt;
      if (b.walkCd <= 0) {
        b.walkTo = pickWalkTarget(b);
        setState(b, "walk");
      }
    }
  }

  // 小跳
  if (airborne(b)) {
    b.vy += G * dt;
    b.jy += b.vy * dt;
    if (b.jy >= 0) {
      b.jy = 0;
      b.vy = 0;
      b.bigHop = false;
      b.squash = 0.14;
      b.tv -= 3;
    }
  }
  if (b.squash > 0) b.squash -= dt;
  if (b.shake > 0) b.shake -= dt;

  // 各狀態自己的計時
  if (b.state === "pet") {
    const due = [0, 0.5, 1.0];
    while (b.hearts < due.length && b.t >= due[b.hearts]) { spawnHeart(b); b.hearts++; }
    if (b.t > 1.7) setState(b, "idle");
  } else if (b.state === "ink") {
    // 出錯的回覆還沒被看：噴完墨接著冒泡泡提醒
    if (b.t > 1.8) setState(b, b.activity === "error" ? "cheer" : "idle");
  } else if (b.state === "wake") {
    if (b.t > 0.7 && !airborne(b)) setState(b, "idle");
  } else if (b.state === "sleep") {
    b.z -= dt;
    if (b.z <= 0) { spawnZ(b); b.z = 1.7; }
  } else if (b.state === "read") {
    b.lift = Math.max(0, b.lift - 14 * dt);
    if (b.flipT >= 0) {
      b.flipT += dt;
      if (b.flipT > 0.42) b.flipT = -1;
    } else if (b.lift === 0) {
      b.nextFlip -= dt;
      if (b.nextFlip <= 0) { b.flipT = 0; b.nextFlip = 2.6 + b.rand() * 2.4; }
    }
  } else if (b.state === "code") {
    b.lift = Math.max(0, b.lift - 14 * dt);
    if (b.runT > 0) {
      b.runT -= dt;
      if (b.runT <= 0) setState(b, b.activity === "done" ? "cheer" : "idle");
    } else if (b.lift === 0) {
      b.typeT += dt;
      b.glyphT -= dt;
      if (b.glyphT <= 0) { spawnCode(b); b.glyphT = (0.32 + b.rand() * 0.3) * (reduced ? 2 : 1); }
    }
  } else if (b.state === "walk") {
    // 一步走一格，兩側觸手輪流抬起；走到了就站好，過一陣子再走
    b.stepT += dt;
    if (b.stepT >= STEP_S) {
      b.stepT -= STEP_S;
      if (b.x === b.walkTo) {
        setState(b, "idle");
        b.walkCd = 5 + b.rand() * 7;
      } else {
        b.stepPhase ^= 1;
        b.x += Math.sign(b.walkTo - b.x);
      }
    }
  } else if (b.state === "stow") {
    b.lift = Math.min(4, b.lift + 14 * dt);
    if (b.lift >= 4) setState(b, b.after);
  }

  // 眨眼：偶爾連眨兩下
  if (b.blink > 0) b.blink -= dt;
  else {
    b.nextBlink -= dt;
    if (b.nextBlink <= 0) {
      b.blink = 0.12;
      b.nextBlink = b.rand() < 0.2 ? 0.28 : 2.2 + b.rand() * 3.2;
    }
  }

  // 呼吸：頭往下沉一格
  let dy = 0;
  if (b.squash > 0) dy = 1;
  else if (b.state === "walk") dy = b.stepPhase;
  else if (!airborne(b) && !reduced) {
    const period = b.state === "sleep" ? 2.8 : 1.6;
    dy = ((now / 1000) % period) / period > 0.55 ? 1 : 0;
  }
  if (dy !== b.headDy) { b.tv += dy ? 0.9 : -0.6; b.headDy = dy; }

  // 臉只左右轉：游標在附近就看游標，不然自己東張西望
  if (b.state === "sleep" || BUSY.includes(b.state)) b.look = 0;
  else if (b.state === "walk") b.look = Math.sign(b.walkTo - b.x);
  else if (pointerLook !== null) b.look = pointerLook;
  else {
    b.wander -= dt;
    if (b.wander <= 0) {
      const opts = [0, 0, -1, 1];
      b.look = opts[Math.floor(b.rand() * opts.length)];
      b.wander = 1.4 + b.rand() * 2.6;
    }
  }

  // 帽穗擺錘：彈簧 + 阻尼 + 微風
  const s = now / 1000;
  let amb = reduced ? 2 : 4 + 9 * Math.sin(s * 1.9);
  if (b.state === "pet") amb += 10 * Math.sin(s * 16);
  if (b.state === "sleep") amb = 1.5 + 3 * Math.sin(s * 1.1);
  b.tv += (-38 * b.tassel - 3 * b.tv + amb) * dt;
  b.tassel += b.tv * dt;
  if (b.tassel < -0.45) { b.tassel = -0.45; b.tv = Math.max(0, b.tv); }
  if (b.tassel > 2.7) { b.tassel = 2.7; b.tv = Math.min(0, b.tv); }

  for (const p of b.parts) {
    p.life += dt;
    if (p.kind === "ink" && !p.landed) {
      p.vy += 150 * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.vy > 0 && p.y >= p.rest) { p.y = p.rest; p.landed = true; }
    } else if (p.kind !== "ink") {
      p.x += p.vx * dt;
      p.y += p.vy * dt;
    }
  }
  b.parts = b.parts.filter((p) => p.life < p.max && p.x > -6 && p.x < LAYOUT.CW + 6 && p.y > -6);
}

/* ---------- 這一格要畫成什麼樣子 ---------- */
export function poseOf(b) {
  const air = airborne(b);
  const busy = BUSY.includes(b.state);
  const running = b.state === "code" && b.runT > 0;
  let eyes = "open";
  let mouth = "flat";
  // 筆電螢幕背面會擋住笑嘴的下緣，程式跑起來時用 ^_^（開心眼＋一字嘴）
  if (running) eyes = "happy";
  else if (busy) eyes = b.blink > 0 ? "closed" : "open";
  else if (b.state === "sleep") eyes = "closed";
  else if (b.state === "ink" || (b.state === "wake" && b.t < 0.35)) mouth = "frown";
  else if (b.state === "cheer") {
    // 剛回覆好那一下開心，之後睜眼等你看；出錯的就嘴往上拱
    if (b.activity === "error") mouth = "frown";
    else { mouth = "smile"; if (b.t < 1.5 || b.hovering) eyes = "happy"; }
  }
  else if (b.state === "pet" || b.hovering || b.squash > 0) { eyes = "happy"; mouth = "smile"; }
  else if (b.blink > 0) eyes = "closed";
  let legs = (air && b.bigHop) || (b.state === "ink" && b.t < 0.3) ? "stretch" : "curl";
  if (legs === "curl" && b.state === "walk") legs = b.stepPhase ? "walkA" : "walkB";
  let jy = Math.round(b.jy);
  if (legs === "stretch") jy = Math.min(jy, -STRETCH_LIFT);
  return {
    legs,
    eyes,
    // 看書時嘴巴被書擋住；寫程式時盯著鍵盤，嘴巴抿成一字
    mouth: busy && !running && b.prop === "book" ? "none" : mouth,
    eyeDy: busy && !running ? 1 : 0,
    look: eyes === "open" ? b.look : 0,
    headDy: air ? 0 : b.headDy,
    tassel: b.tassel,
    jy,
    // 走到哪（格數，往左為負）：由外層搬動整個元素，點擊與 hover 範圍跟著走
    x: b.x,
    shake: b.shake > 0 ? (Math.floor(b.shake * 40) % 2 ? 1 : -1) : 0,
    bang: b.state === "ink" && b.t < 1.1,
    // 泡泡每 0.65 秒上下浮一格；減少動態時不動
    bubble: b.state === "cheer",
    bubbleDy: b.state === "cheer" && !b.reduced ? Math.floor(b.t / 0.65) % 2 : 0,
    prop: busy
      ? {
          kind: b.prop,
          lift: Math.round(b.lift),
          flip: b.prop === "book" && b.flipT >= 0 ? Math.min(FLIP.length - 1, Math.floor(b.flipT / 0.084)) : -1,
          hand: running ? -1 : Math.floor(b.typeT / 0.14) % 2,
        }
      : null,
  };
}
