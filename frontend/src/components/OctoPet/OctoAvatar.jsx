import { COLORS, EYE_AT, EYES, HEAD, LEGS, MOUTHS } from "./octoSprites";
import styles from "./OctoPet.module.scss";

/* 頭像畫整隻章魚，視窗對準臉（16×16 格），超出的部分交給外層的圓框裁掉，
   像照片頭像一樣自然地被圓邊切到，不會在脖子切成一條直線。
   靜態 SVG，訊息列表有幾十則也不會多跑動畫迴圈；帽子色走 CSS 變數，深色模式自動調亮 */
const VIEW = { x: 2, y: -1.5, size: 16 };
const CELLS = (() => {
  const grid = new Map();
  [...HEAD, ...LEGS.curl].forEach((row, y) => {
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
 * 章魚學士的靜態頭像（訊息旁的圓形頭像用，外層負責圓框與裁切）。
 * @param {number} cell 每一格幾 px，用整數像素才不會糊；預設 2（32×32）
 */
export default function OctoAvatar({ cell = 2, className = "" }) {
  return (
    <svg
      className={`${styles.root} ${className}`}
      width={VIEW.size * cell}
      height={VIEW.size * cell}
      viewBox={`${VIEW.x} ${VIEW.y} ${VIEW.size} ${VIEW.size}`}
      shapeRendering="crispEdges"
      aria-hidden="true"
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
