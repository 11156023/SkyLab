import { useState } from "react";
import { EdgeLabelRenderer, getBezierPath } from "@xyflow/react";
import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";

/* 標籤與所屬節點的距離。固定距離而非固定比例：長線的標籤才不會
   飄到沿途其他節點上被蓋住，短線的也不會貼在節點身上。 */
const LABEL_GAP = 52;
/* 同一對節點之間的多條線，彼此錯開的量。控制點偏移，端點不動 */
const LANE_GAP = 56;
/* 標籤在端點附近時線才剛分岔，額外再錯開一點才不會疊住 */
const LANE_LABEL_GAP = 26;

/** 解析 getBezierPath 產生的 "M sx,sy C c1x,c1y c2x,c2y tx,ty" */
function parsePath(d) {
  const nums = d.match(/-?\d+(?:\.\d+)?/g);
  return nums && nums.length >= 8 ? nums.slice(0, 8).map(Number) : null;
}

/**
 * 單位法線。方向統一朝右半平面，否則入站與出站因為起訖點互換，
 * 算出來的法線相反，各自往「自己的左邊」偏移後又會疊回同一條線上。
 */
function unitNormal([x0, y0, , , , , x3, y3]) {
  const dx = x3 - x0;
  const dy = y3 - y0;
  const len = Math.hypot(dx, dy) || 1;
  let nx = -dy / len;
  let ny = dx / len;
  if (nx < -1e-6 || (Math.abs(nx) <= 1e-6 && ny < 0)) {
    nx = -nx;
    ny = -ny;
  }
  return { nx, ny };
}

/** 只推開控制點，端點仍固定在 handle 上，線才不會脫離節點 */
function spreadPath([x0, y0, x1, y1, x2, y2, x3, y3], { nx, ny }, offset) {
  const ox = nx * offset;
  const oy = ny * offset;
  return `M${x0},${y0} C${x1 + ox},${y1 + oy} ${x2 + ox},${y2 + oy} ${x3},${y3}`;
}

/**
 * 算標籤該放在 getBezierPath 產生的 "M sx,sy C c1x,c1y c2x,c2y tx,ty" 上的哪一點。
 *
 * 不放中點有兩個理由：多條線匯聚到同一個節點時，中點會擠成一排，
 * 看不出哪個標籤屬於哪條線；中段又常被沿線的其他節點蓋住。
 * 改成緊貼「這條規則的主體」那一端，標籤就散到各自的機器旁邊。
 */
function labelPointOnPath(d, nearTarget) {
  const nums = d.match(/-?\d+(?:\.\d+)?/g);
  if (!nums || nums.length < 8) return null;
  const [x0, y0, x1, y1, x2, y2, x3, y3] = nums.map(Number);

  const at = (t) => {
    const u = 1 - t;
    const a = u * u * u;
    const b = 3 * u * u * t;
    const c = 3 * u * t * t;
    const e = t * t * t;
    return {
      x: a * x0 + b * x1 + c * x2 + e * x3,
      y: a * y0 + b * y1 + c * y2 + e * y3,
    };
  };

  /* 標籤往遠離自己節點的方向展開，否則長標籤會往回蓋住節點 */
  const anchorX = nearTarget ? x3 : x0;
  const anchorY = nearTarget ? y3 : y0;
  const withAlign = (point) => {
    const awayX = point.x - anchorX;
    const awayY = point.y - anchorY;
    const horizontal = Math.abs(awayX) > Math.abs(awayY);
    return {
      ...point,
      align: horizontal
        ? `${awayX >= 0 ? "0" : "-100%"}, -50%`
        : `-50%, ${awayY >= 0 ? "0" : "-100%"}`,
    };
  };

  /* 沿路徑取樣累積真實弧長：貝茲的 t 與弧長不成正比，垂直連線尤其明顯，
     直接用 t 估距離會讓標籤飄進沿途的節點裡。 */
  const SAMPLES = 32;
  let prev = at(nearTarget ? 1 : 0);
  let travelled = 0;
  for (let i = 1; i <= SAMPLES; i++) {
    const ratio = i / SAMPLES;
    const point = at(nearTarget ? 1 - ratio : ratio);
    travelled += Math.hypot(point.x - prev.x, point.y - prev.y);
    if (travelled >= LABEL_GAP) return withAlign(point);
    prev = point;
  }
  /* 整條線比一個間距還短，放中點就好 */
  return withAlign(at(0.5));
}

/* ─── 邊動畫 keyframes（注入 head，避免 React 19 的 <style> 提升行為破壞 SVG 結構） ── */
if (!document.getElementById("flow-fwd-kf")) {
  const s = document.createElement("style");
  s.id = "flow-fwd-kf";
  s.textContent = `@keyframes flow-fwd{from{stroke-dashoffset:12}to{stroke-dashoffset:0}}`;
  document.head.appendChild(s);
}

export default function ConnectionEdge(props) {
  const {
    id, sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition, data,
  } = props;

  const { t } = useTranslation("network");
  const [hovered, setHovered] = useState(false);
  const [basePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });

  /* 同一對節點之間的線各走一側：入站與出站的端點完全相同，
     不錯開就會疊成一條，看不出有兩條規則也點不到下面那條 */
  const laneCount = data?.laneCount ?? 1;
  const lane = laneCount > 1 ? (data?.laneIndex ?? 0) - (laneCount - 1) / 2 : 0;
  const parsed = lane ? parsePath(basePath) : null;
  const normal = parsed ? unitNormal(parsed) : null;
  const edgePath = parsed && normal
    ? spreadPath(parsed, normal, lane * LANE_GAP)
    : basePath;

  const edge       = data?.edge ?? {};
  const isInbound  = edge.source_vmid === null;
  const isOutbound = edge.target_vmid === null;
  const isBidirectional = edge.direction === "bidirectional";
  const isSelected = Boolean(data?.selected);
  // 入站藍 / 出站綠 / 內部灰，走主題語意色；hover 用 color-mix 提亮，深淺色模式都跟著換
  const baseColor = isInbound
    ? "var(--color-info)"
    : isOutbound
    ? "var(--color-success)"
    : "var(--color-status-neutral)";
  const color = hovered || isSelected
    ? `color-mix(in srgb, ${baseColor} 70%, white)`
    : baseColor;

  const showLabel = hovered || isSelected || data?.showLabel;
  /* 箭頭 marker 必須是全域唯一 id；edge id 已含來源與目標，直接沿用 */
  const markerId = `arrow-${id}`;
  const markerRef = `url(#${markerId})`;
  const strokeWidth = isSelected ? 3 : hovered ? 2.5 : 1.8;
  /* 唯讀檢視（資源詳情的迷你拓撲）沒有細節面板可開，就不要裝成可點 */
  const clickable = Boolean(data?.onSelect);
  const cursor = clickable ? "pointer" : "default";
  const select = () => data?.onSelect?.(edge, id);
  /* 不限 port 的連線（出站上網等）沒有可列的埠，仍要講清楚它開了什麼 */
  const label = data?.label || t("ConnectionEdge.allPorts");
  /* 入站的主體是目標 VM，出站與內部互通的主體是來源 VM */
  const basePoint = labelPointOnPath(edgePath, isInbound)
    ?? { x: labelX, y: labelY, align: "-50%, -50%" };
  /* 標籤所在的端點附近，兩條線才剛分岔，再往外錯開一段才分得開 */
  const labelPoint = normal
    ? {
        ...basePoint,
        x: basePoint.x + normal.nx * lane * LANE_LABEL_GAP,
        y: basePoint.y + normal.ny * lane * LANE_LABEL_GAP,
      }
    : basePoint;

  return (
    <g>
      {/* 箭頭：單向只有終點，雙向兩端都有（orient 讓起點的箭頭自動反向） */}
      <defs>
        <marker
          id={markerId}
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          markerUnits="userSpaceOnUse"
          orient="auto-start-reverse"
        >
          <path d="M0,1 L9,5 L0,9 z" fill={color} />
        </marker>
      </defs>

      {/* 透明寬路徑：hover 偵測與點擊區 */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        style={{ cursor }}
        onClick={select}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      />

      {/* 主流向路徑（動畫虛線） */}
      <path
        id={id}
        d={edgePath}
        className="react-flow__edge-path"
        markerEnd={markerRef}
        markerStart={isBidirectional ? markerRef : undefined}
        style={{
          fill: "none",
          stroke: color,
          strokeWidth,
          strokeDasharray: "8 4",
          animation: "flow-fwd 1.2s linear infinite",
          opacity: 0.9,
          transition: "stroke 0.2s",
          cursor,
        }}
        onClick={select}
      />

      <EdgeLabelRenderer>
        <div
          className={`${styles.edgeLabelWrap} nodrag nopan`}
          style={{
            position: "absolute",
            transform: `translate(${labelPoint.align}) translate(${labelPoint.x}px,${labelPoint.y}px)`,
            pointerEvents: showLabel ? "all" : "none",
            opacity: showLabel ? 1 : 0,
            transition: "opacity 0.15s",
          }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          <button
            type="button"
            className={`${styles.edgeLabel} ${isSelected ? styles.edgeLabelActive : ""}`}
            style={{ color, cursor }}
            onClick={select}
            title={label}
          >
            {label}
          </button>
        </div>
      </EdgeLabelRenderer>
    </g>
  );
}
