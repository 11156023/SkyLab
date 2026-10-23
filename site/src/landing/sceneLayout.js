/**
 * landing/sceneLayout.js
 * 段落道具（機房節點、申請管線、AI 核心、防火牆閘門）的世界座標與資料換算。
 * 純函式,CampusScene / StoryProps 共用；機房量體要跟 CampusScene 的 BUILDINGS 一致。
 */

export const DATACENTER = { x: 140, y: 860, w: 300, d: 200, h: 70 };
export const NODE = { w: 44, d: 52, h: 28 };
const MAX_NODES = 8;

/* AI 核心懸浮在機房東側的道路上空,光束掃向屋頂節點 */
export const AI_CORE = { x: 570, y: 910, z: 240 };

/* 申請管線:宿舍屋頂 → 行政樓屋頂審核環 → 機房節點;巡航高度高過沿線所有建築 */
export const PIPELINE = {
  submit: [355, 470, 80],
  stamp: [230, 200, 140],
  cruise: 190,
};

/* 防火牆閘門:圍牆缺口 x 610–790、牆心 y 1365 */
export const GATE = { x0: 610, x1: 790, y: 1365, h: 64 };

/* 私有雲:懸在校園中央上空,光纖接到各棟屋頂(世界座標＝屋頂中心與高度) */
export const CLOUD = { x: 700, y: 700, z: 505 };
export const CLOUD_LINKS = [
  [200, 185, 140], // 行政樓
  [175, 430, 80], // 宿舍
  [435, 275, 100], // 圖書館
  [290, 960, 70], // 機房
  [585, 485, 55], // 學生活動中心
  [960, 225, 210], // 教學大樓
  [1185, 300, 70], // 階梯教室
  [1030, 955, 55], // 電腦教室
  [1285, 950, 85], // 實驗室
];

/* lifecycle 屋頂劇場(機房屋頂南側,節點機櫃讓到北側):範本模具 → VM 位 → 快照位 */
export const LIFECYCLE = {
  template: { x: 162, y: 1010, s: 34 },
  vm: { x: 240, y: 1018, s: 20 },
  snapshot: { x: 296, y: 1018, s: 20 },
};

/* 電腦教室:屋頂＝挑空教室(女兒牆＋黑板＋桌機),座位上限 5 排 × 8 */
export const CLASSROOM = { x: 880, y: 860, w: 300, d: 190, h: 55, cols: 8, maxRows: 5 };

/**
 * 概況的節點數 → 機房屋頂節點機櫃的位置（≤4 台一排,更多排兩排,靠北側排,
 * 南側留給 lifecycle 屋頂劇場）。
 * 依 (x+y) 遞增輸出,直接照順序畫就符合 painter's algorithm。
 */
export function nodeSlots(count) {
  const n = Math.min(Math.max(Math.round(Number(count)) || 3, 1), MAX_NODES);
  const rows = n <= 4 ? 1 : 2;
  const cols = Math.ceil(n / rows);
  const gapX = 18;
  const gapY = 24;
  const totalW = cols * NODE.w + (cols - 1) * gapX;
  const x0 = DATACENTER.x + DATACENTER.w / 2 - totalW / 2;
  const y0 = DATACENTER.y + 16;
  return Array.from({ length: n }, (_, i) => {
    const x = x0 + (i % cols) * (NODE.w + gapX);
    const y = y0 + Math.floor(i / cols) * (NODE.d + gapY);
    return { x, y, cx: x + NODE.w / 2, cy: y + NODE.d / 2, top: DATACENTER.h + NODE.h };
  });
}

/** AI 放置建議分數最高的節點索引（只看場景裡畫得出來的前 n 台） */
export function pickWinner(candidates, n) {
  let best = 0;
  (candidates ?? []).slice(0, n).forEach((c, i) => {
    if ((c?.score ?? -1) > (candidates[best]?.score ?? -1)) best = i;
  });
  return best;
}

/** 申請管線的世界折線,終點落在目標節點頂面;stampIndex＝審核環所在點 */
export function pipelinePoints(target) {
  const { submit, stamp, cruise } = PIPELINE;
  const points = [
    submit,
    [submit[0], submit[1], cruise],
    [submit[0], stamp[1], cruise],
    [stamp[0], stamp[1], cruise],
    stamp,
    [stamp[0], stamp[1], cruise],
    [stamp[0], target.cy, cruise],
    [target.cx, target.cy, cruise],
    [target.cx, target.cy, target.top],
  ];
  return { points, stampIndex: 4 };
}
