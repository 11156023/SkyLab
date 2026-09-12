import { useState } from "react";
import { EdgeLabelRenderer, getBezierPath } from "@xyflow/react";
import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";

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
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });

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
            transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)`,
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
          >
            {label}
          </button>
        </div>
      </EdgeLabelRenderer>
    </g>
  );
}
