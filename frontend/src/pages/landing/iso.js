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

function pts(points) {
  return points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
}

/** 世界座標點列 → polygon points 字串 */
export function P(worldPoints) {
  return pts(worldPoints.map(([x, y, z]) => project(x, y, z ?? 0)));
}

/** 世界座標點列 → path d 字串（折線） */
export function linePath(worldPoints) {
  return worldPoints
    .map(([x, y, z], i) => {
      const [sx, sy] = project(x, y, z ?? 0);
      return `${i === 0 ? "M" : "L"} ${sx.toFixed(1)} ${sy.toFixed(1)}`;
    })
    .join(" ");
}

/** 世界折線投影後的螢幕長度（SMIL keyPoints 換算「走到第 k 點」的比例用） */
export function projectedLength(worldPoints) {
  let total = 0;
  for (let i = 1; i < worldPoints.length; i += 1) {
    const [ax, ay] = project(...worldPoints[i - 1]);
    const [bx, by] = project(...worldPoints[i]);
    total += Math.hypot(bx - ax, by - ay);
  }
  return total;
}

/** 世界水平圓 → 螢幕橢圓參數 */
export function isoEllipse(cx, cy, r, z = 0) {
  const [sx, sy] = project(cx, cy, z);
  return { cx: sx, cy: sy, rx: r * 1.2247, ry: r * 0.7071 };
}
