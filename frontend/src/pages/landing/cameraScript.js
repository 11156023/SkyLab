/**
 * landing/cameraScript.js
 * 八段滾動的分段腳本：每段的滾動長度、相機焦點與倍率、HUD 對齊側。
 * 之後換正式美術只需要調這裡的 focus / zoom，不動 LandingPage 的運鏡邏輯。
 */
import { worldPixel } from "./iso";

/* 運鏡發生在段落交界:在下一段 sticky pin 住前 CAMERA_LEAD 段開始移動,
   花 CAMERA_DURATION 段到位——pin 住讀 HUD 的期間相機是靜止的。
   （sticky 只能 pin「段長 − 100vh」的距離,運鏡必須壓在交界處才對得上。） */
export const CAMERA_LEAD = 0.7;
export const CAMERA_DURATION = 0.8;

/**
 * length 單位＝100vh；zoom 是相對「全景基準倍率」的倍數（1＝剛好裝下整個校園）；
 * align / valign 決定該段 HUD 卡落在九宮格的哪一格(左中右 × 上中下),
 * 相機用反向偏置讓焦點建築讓開卡片:卡在右→建築偏左、卡在上→建築下沉。
 * workflow 拉遠框住「宿舍 → 行政樓 → 機房」整條申請管線;ai 框住 AI 核心與機房屋頂節點
 * (道具座標見 sceneLayout.js)。
 */

export const SECTIONS = [
  { id: "hero",      length: 1.0, focus: worldPixel(700, 700, 0),   zoom: 1.5,  align: "center", valign: "center" },
  { id: "overview",  length: 1.0, focus: worldPixel(700, 700, 0),   zoom: 1.0,  align: "center", valign: "center" },
  { id: "lifecycle", length: 1.5, focus: worldPixel(300, 960, 40),  zoom: 2.8,  align: "left",   valign: "center" },
  { id: "workflow",  length: 1.5, focus: worldPixel(141, 419, 0),   zoom: 1.8,  align: "right",  valign: "center" },
  { id: "classroom", length: 1.5, focus: worldPixel(1030, 950, 60), zoom: 3.1,  align: "left",   valign: "top" },
  { id: "network",   length: 1.5, focus: worldPixel(720, 1390, 20), zoom: 2.6,  align: "right",  valign: "bottom" },
  { id: "ai",        length: 1.0, focus: worldPixel(237, 843, 0),   zoom: 2.4,  align: "left",   valign: "bottom" },
  { id: "terminal",  length: 1.0, focus: worldPixel(960, 200, 160), zoom: 3.4,  align: "center", valign: "center" },
];

export const TOTAL_LENGTH = SECTIONS.reduce((sum, s) => sum + s.length, 0);

/* 全景基準以「實際內容範圍」計算(菱形＋建築高度,約 2480×1500 世界像素),
   不吃 viewBox 四周的空白邊——整個場景因此放大約 12–15% */
const FIT = { w: 2480, h: 1500 };

/**
 * 依視窗尺寸把一段的 focus/zoom 換算成場景 div 的 transform 目標值。
 * transform-origin 固定 0 0：translate = 目標螢幕位置 − 焦點像素 × scale。
 * align=left 時焦點推到畫面偏右（63%），讓左側 HUD 與建築並排。
 */
export function cameraState({ focus: [fx, fy], zoom, align, valign }, vw, vh) {
  const base = Math.min((vw * 0.95) / FIT.w, (vh * 0.92) / FIT.h);
  const scale = base * zoom;
  const cx = align === "left" ? vw * 0.63 : align === "right" ? vw * 0.37 : vw / 2;
  const cy = vh * (valign === "top" ? 0.6 : valign === "bottom" ? 0.44 : 0.5);
  return { scale, x: cx - fx * scale, y: cy - fy * scale };
}
