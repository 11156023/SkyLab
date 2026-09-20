/**
 * landing/iso.js
 * 導入頁灰盒場景的等距（isometric）投影工具。
 * 世界座標系：x 往右後、y 往左後、z 往上，單位為任意世界單位；
 * 校園地面規劃在 (0,0)–(1400,1400) 的正方形上。
 */

export const ISO_X = Math.cos(Math.PI / 6); // 0.866（2:1 等距的水平係數）
export const ISO_Y = 0.5;

/** CampusScene 的 SVG viewBox；相機運鏡以此換算世界 div 內的像素位置 */
export const VIEWBOX = { x: -1350, y: -350, w: 2700, h: 2000 };

/** 世界 (x, y, z) → 投影平面座標（SVG 使用者座標） */
export function project(x, y, z = 0) {
  return [(x - y) * ISO_X, (x + y) * ISO_Y - z];
}

/** 世界 (x, y, z) → 場景 div 內的像素座標（供相機置中計算） */
export function worldPixel(x, y, z = 0) {
  const [sx, sy] = project(x, y, z);
  return [sx - VIEWBOX.x, sy - VIEWBOX.y];
}
