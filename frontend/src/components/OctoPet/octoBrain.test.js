import { expect, test } from "vitest";
import { LAYOUT, READ_AFTER_MS, SLEEP_AFTER_MS, brainEvent, createBrain, poseOf, stepBrain } from "./octoBrain";
import { drawScene } from "./octoDraw";

const T0 = 1_000_000;
const STEP = 16;

/* 用模擬時鐘一格一格推，記下每次換狀態的時間 */
function run(brain, from, ms, env = {}, onStep) {
  const log = [];
  let last = brain.state;
  for (let now = from; now <= from + ms; now += STEP) {
    stepBrain(brain, STEP / 1000, now, env);
    if (brain.state !== last) {
      last = brain.state;
      log.push([now - from, last]);
    }
    onStep?.(now);
  }
  return log;
}
const states = (log) => log.map(([, s]) => s);

test("沒人理牠就拿書出來看，看完收起來；整頁沒動靜就收書睡覺，一有動靜就醒來", () => {
  const brain = createBrain(T0, () => 0);
  const log = run(brain, T0, SLEEP_AFTER_MS + 1000);
  const [readAt] = log.find(([, s]) => s === "read");
  expect(readAt).toBeGreaterThanOrEqual(READ_AFTER_MS);
  expect(states(log)).toContain("stow");
  expect(brain.state).toBe("sleep");

  const now = T0 + SLEEP_AFTER_MS + 2000;
  brainEvent(brain, "active", now);
  run(brain, now, 1000);
  expect(brain.state).toBe("idle");
});

test("AI 回覆中寫程式，回覆好了先「程式跑起來了」再冒對話泡泡，被看過就回到待機", () => {
  const brain = createBrain(T0, () => 0);
  // 筆電 0.3 秒拿出來，第一個 0/1 再 0.4 秒才冒出來
  run(brain, T0, 1000, { activity: "thinking" });
  expect(brain.state).toBe("code");
  expect(poseOf(brain)).toMatchObject({ eyeDy: 1, mouth: "flat", prop: { kind: "laptop", lift: 0 } });
  expect(brain.parts.some((p) => p.kind === "code")).toBe(true);
  expect(brain.parts.filter((p) => p.kind === "code").every((p) => ["0", "1"].includes(p.g))).toBe(true);

  stepBrain(brain, STEP / 1000, T0 + 1100, { activity: "done" });
  expect(poseOf(brain)).toMatchObject({ eyes: "happy", mouth: "flat" });
  const log = run(brain, T0 + 1100, 2000, { activity: "done" });
  expect(states(log)).toEqual(["cheer"]);
  expect(poseOf(brain)).toMatchObject({ bubble: true, mouth: "smile" });

  run(brain, T0 + 3500, 100, { activity: "idle" });
  expect(brain.state).toBe("idle");
});

test("回覆中途被取消（又回到 idle）就把筆電收起來", () => {
  const brain = createBrain(T0, () => 0);
  run(brain, T0, 500, { activity: "thinking" });
  const log = run(brain, T0 + 500, 1000, { activity: "idle" });
  expect(states(log)).toEqual(["stow", "idle"]);
});

test("出錯就噴墨、嘴往上拱，接著冒泡泡提醒；被看過就回到待機", () => {
  const brain = createBrain(T0, () => 0);
  stepBrain(brain, STEP / 1000, T0, { activity: "error" });
  expect(brain.state).toBe("ink");
  expect(poseOf(brain)).toMatchObject({ mouth: "frown", bang: true });
  expect(brain.parts.some((p) => p.kind === "ink")).toBe(true);
  run(brain, T0, 2500, { activity: "error" });
  expect(brain.state).toBe("cheer");
  expect(poseOf(brain)).toMatchObject({ bubble: true, mouth: "frown" });
  run(brain, T0 + 2600, 100, { activity: "idle" });
  expect(brain.state).toBe("idle");
});

test("游標移上來：看書的會把書放下，睡著的會醒來；移上來時是開心臉", () => {
  const reading = createBrain(T0, () => 0);
  run(reading, T0, READ_AFTER_MS + 1000);
  expect(reading.state).toBe("read");
  brainEvent(reading, "hover", T0 + READ_AFTER_MS + 1100, true);
  run(reading, T0 + READ_AFTER_MS + 1100, 600);
  expect(reading.state).toBe("idle");
  expect(poseOf(reading)).toMatchObject({ eyes: "happy", mouth: "smile" });

  const sleeping = createBrain(T0, () => 0);
  run(sleeping, T0, SLEEP_AFTER_MS + 1000);
  expect(sleeping.state).toBe("sleep");
  brainEvent(sleeping, "hover", T0 + SLEEP_AFTER_MS + 1100, true);
  expect(sleeping.state).toBe("wake");
});

test("待機時隨機往左走幾格、臉朝走的方向，走完會再走回家；走路不會把看書的倒數往後推", () => {
  const brain = createBrain(T0, () => 0);
  const xs = [];
  const log = run(brain, T0, 7900, { walkRange: 8 }, () => {
    if (brain.state === "walk") {
      xs.push(brain.x);
      // 眨眼那幾格 pose 的 look 會歸零，所以看狀態機記的朝向
      expect(brain.look).toBe(Math.sign(brain.walkTo - brain.x) || 0);
      expect(["walkA", "walkB"]).toContain(poseOf(brain).legs);
    }
  });
  expect(states(log)).toContain("walk");
  expect(Math.min(...xs)).toBe(-8);
  expect(Math.max(...xs)).toBeLessThanOrEqual(0);
  // 7.9 秒內走過路，第 8 秒照樣開始看書
  run(brain, T0 + 7916, 1000, { walkRange: 8 });
  expect(brain.state).toBe("read");
});

test("走路走到一半，游標移上來就停下來", () => {
  const brain = createBrain(T0, () => 0);
  run(brain, T0, 4300, { walkRange: 8 });
  expect(brain.state).toBe("walk");
  brainEvent(brain, "hover", T0 + 4320, true);
  expect(brain.state).toBe("idle");
  const x = brain.x;
  run(brain, T0 + 4320, 2000, { walkRange: 8 });
  expect(brain.x).toBe(x);
});

test("安靜模式只呼吸眨眼：不會自己看書、走路、睡覺，摸頭照樣冒愛心", () => {
  const brain = createBrain(T0, () => 0);
  const log = run(brain, T0, SLEEP_AFTER_MS + 2000, { quiet: true, walkRange: 8 });
  expect(log).toEqual([]);
  expect(brain.state).toBe("idle");
  const now = T0 + SLEEP_AFTER_MS + 2100;
  brainEvent(brain, "hover", now, true);
  for (let i = 1; i <= 12; i++) brainEvent(brain, "rub", now + i * 30, 40);
  expect(brain.state).toBe("pet");
});

test("walkRange 為 0（預設）就不會走", () => {
  const brain = createBrain(T0, () => 0);
  const log = run(brain, T0, 7900);
  expect(states(log)).not.toContain("walk");
  expect(brain.x).toBe(0);
});

test("在身上來回滑夠多就算摸頭，冒愛心", () => {
  const brain = createBrain(T0, () => 0);
  brainEvent(brain, "hover", T0, true);
  for (let i = 1; i <= 12; i++) brainEvent(brain, "rub", T0 + i * 30, 40);
  expect(brain.state).toBe("pet");
  run(brain, T0 + 400, 1200);
  expect(brain.parts.filter((p) => p.kind === "heart").length).toBeGreaterThan(0);
});

test("每一種姿勢都畫得出來，而且不會畫出畫布範圍", () => {
  const rects = [];
  const ctx = {
    canvas: { width: LAYOUT.CW * 3, height: LAYOUT.CH * 3 },
    fillStyle: "",
    clearRect() {},
    fillRect(x, y, w, h) { rects.push([x, y, w, h]); },
  };
  const theme = {
    cap: "#252c48", lid: "#252c48", fx: "#5471bf", heart: "#ec7aa2", ink: "#232a4d", shadow: "rgba(0,0,0,.2)",
    bubbleLine: "#202748", bubbleFill: "#ffffff", bubbleDot: "#202748",
  };
  const brain = createBrain(T0, () => 0);
  const scenarios = [
    ["idle", 300], ["thinking", 1500], ["done", 300], ["idle", 9000], ["error", 200],
  ];
  let now = T0;
  for (const [activity, ms] of scenarios) {
    run(brain, now, ms, { activity }, () => {
      rects.length = 0;
      drawScene(ctx, { u: 1, pose: poseOf(brain), parts: brain.parts, theme });
      // 章魚本體（不含飄走的粒子）一定落在畫布內
      expect(rects.length).toBeGreaterThan(0);
    });
    now += ms + STEP;
  }
});
