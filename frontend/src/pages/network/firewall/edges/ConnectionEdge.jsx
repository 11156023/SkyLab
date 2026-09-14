import { useState } from "react";
import { EdgeLabelRenderer, getBezierPath } from "@xyflow/react";
import { useTranslation } from "react-i18next";
import styles from "../FirewallPage.module.scss";
import MIcon from "../../../../components/MIcon";

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
  // 入站藍 / 出站綠 / 內部灰，走主題語意色；hover 用 color-mix 提亮，深淺色模式都跟著換
  const baseColor = isInbound
    ? "var(--color-info)"
    : isOutbound
    ? "var(--color-success)"
    : "var(--color-status-neutral)";
  const color = hovered ? `color-mix(in srgb, ${baseColor} 70%, white)` : baseColor;

  const showLabel = hovered || data?.showLabel;

  return (
    <g>
      {/* 透明寬路徑：hover 偵測 */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        style={{ cursor: data?.onSelect ? "pointer" : "default" }}
        onClick={() => data?.onSelect?.(id)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      />

      {/* 主流向路徑（動畫虛線） */}
      <path
        id={id}
        d={edgePath}
        className="react-flow__edge-path"
        style={{
          fill: "none",
          stroke: color,
          strokeWidth: hovered ? 2.5 : 1.8,
          strokeDasharray: "8 4",
          animation: "flow-fwd 1.2s linear infinite",
          opacity: 0.9,
          transition: "stroke 0.2s",
          cursor: data?.onSelect ? "pointer" : "default",
        }}
        onClick={() => data?.onSelect?.(id)}
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
          {data?.label && (
            <span className={styles.edgeLabel} style={{ color }}>
              {data.label}
            </span>
          )}
          {data?.onDelete && (
            <button
              type="button"
              className={styles.edgeDeleteBtn}
              onClick={() => data.onDelete(data.edge)}
              title={t("ConnectionEdge.deleteConnection")}
            >
              <MIcon name="close" size={12} />
            </button>
          )}
        </div>
      </EdgeLabelRenderer>
    </g>
  );
}
