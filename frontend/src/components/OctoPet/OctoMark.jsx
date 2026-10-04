import { COLORS, EYE_AT, EYES, HEAD, LEGS, MOUTHS } from "./octoSprites";
import styles from "./OctoPet.module.scss";

/* 品牌標誌用的整隻靜態章魚：待機捲腳姿勢＋張眼＋一字嘴＋帽穗，
   去掉頂端的空列後是 20×14 格。靜態 SVG，側欄常駐也不跑動畫迴圈；帽子色走 CSS 變數跟深色模式 */
const ROWS = [...HEAD, ...LEGS.curl];
const TOP = 1;
const GRID_W = ROWS[0].length;
const GRID_H = ROWS.length - TOP;
const CELLS = (() => {
  const grid = new Map();
  ROWS.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) if (row[x] !== ".") grid.set(`${x},${y}`, row[x]);
  });
  for (let y = 2; y <= 5; y++) grid.set(`15,${y}`, "y");
  EYE_AT.forEach(([ax, ay], i) => {
    for (const [dx, dy] of EYES.open[i]) grid.set(`${ax + dx},${ay + dy}`, "k");
  });
  for (const [x, y] of MOUTHS.flat) grid.set(`${x},${y}`, "k");
  return [...grid.entries()].map(([key, ch]) => [...key.split(",").map(Number), ch]);
})();

/**
 * 章魚學士的靜態全身標誌（側欄品牌列用）。
 * @param {number} size   高度 px；寬度依 20:14 跟著算。28 → 每格剛好 2px，像素才不會糊
 * @param {string} [label] 無障礙名稱；不給就是純裝飾
 */
export default function OctoMark({ size = 28, label, className = "" }) {
  const cell = size / GRID_H;
  return (
    <svg
      className={`${styles.root} ${className}`}
      width={GRID_W * cell}
      height={size}
      viewBox={`0 ${TOP} ${GRID_W} ${GRID_H}`}
      shapeRendering="crispEdges"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : "true"}
      focusable="false"
    >
      {CELLS.map(([x, y, ch]) => (
        <rect
          key={`${x}-${y}`}
          x={x}
          y={y}
          width="1"
          height="1"
          {...(ch === "c" ? { style: { fill: "var(--octo-cap)" } } : { fill: COLORS[ch] })}
        />
      ))}
    </svg>
  );
}
