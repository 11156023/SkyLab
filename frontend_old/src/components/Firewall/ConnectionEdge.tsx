import {
  type Edge,
  EdgeLabelRenderer,
  type EdgeProps,
  getBezierPath,
} from "@xyflow/react"
import { X } from "lucide-react"
import { useState } from "react"

export type ConnectionEdgeData = {
  ports: Array<{
    port: number
    protocol: string
    external_port?: number | null
    domain?: string | null
  }>
  direction: string
  onDelete?: (sourceVmid: number | null, targetVmid: number | null) => void
  sourceVmid: number | null
  targetVmid: number | null
  isGateway?: boolean
  isInbound?: boolean // internet→VM 入站
  isVMtoVM?: boolean // VM→VM 連線
  showLabels?: boolean // 全域標籤開關
  isHighlighted?: boolean // 節點聚焦時強制顯示
  hidden?: boolean // 聚焦模式下非關聯邊隱藏
}

type ConnectionEdgeType = Edge<ConnectionEdgeData, "connection">

// 每個 edge 共用同一份 keyframes（SVG 層級，定義一次即可）
const FLOW_STYLE = `
  @keyframes flow-fwd {
    from { stroke-dashoffset: 12; }
    to   { stroke-dashoffset: 0;  }
  }
  @keyframes flow-bwd {
    from { stroke-dashoffset: 0;  }
    to   { stroke-dashoffset: 12; }
  }
`

export function ConnectionEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps<ConnectionEdgeType>) {
  const [hovered, setHovered] = useState(false)

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  const portLabel =
    data?.ports && data.ports.length > 0
      ? data.ports
          .map((p) =>
            p.domain
              ? `${p.domain}→:${p.port}`
              : p.external_port != null
                ? `外:${p.external_port}→${p.port}/${p.protocol}`
                : `${p.port}/${p.protocol}`,
          )
          .join(", ")
      : "All"

  const isGateway = data?.isGateway
  const isInbound = data?.isInbound
  const isVMtoVM = data?.isVMtoVM
  const isBidirectional = data?.direction === "bidirectional"
  const showLabel = hovered || data?.showLabels || data?.isHighlighted
  const lineOpacity = data?.hidden ? 0 : 1

  const color = isInbound
    ? hovered
      ? "#93c5fd"
      : "#60a5fa" // 藍色：internet→VM
    : isVMtoVM
      ? hovered
        ? "#fcd34d"
        : "#f59e0b" // 橘色：VM→VM
      : hovered
        ? "#6ee7b7"
        : "#4ade80" // 綠色：VM→internet
  const dashArray = "8 4"
  const animDuration = "1.2s"

  return (
    <>
      {/* 全域 keyframes（重複定義無害） */}
      <defs>
        <style>{FLOW_STYLE}</style>
      </defs>

      {/* 透明寬邊路徑（hover 偵測區域，永遠存在） */}
      {/* biome-ignore lint/a11y/noStaticElementInteractions: SVG path hover detection in ReactFlow edge component */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="cursor-pointer"
      />

      {/* 主流向：source → target */}
      <path
        id={id}
        d={edgePath}
        fill="none"
        stroke={color}
        strokeWidth={isBidirectional ? 1.2 : 1.5}
        strokeDasharray={dashArray}
        style={{
          animation: `flow-fwd ${animDuration} linear infinite`,
          opacity: lineOpacity * 0.9,
          transition: "stroke 0.2s, opacity 0.15s",
        }}
      />

      {/* 反向流：target → source（僅雙向連線） */}
      {isBidirectional && (
        <path
          d={edgePath}
          fill="none"
          stroke={
            isInbound
              ? hovered
                ? "#bfdbfe"
                : "#93c5fd"
              : hovered
                ? "#a7f3d0"
                : "#86efac"
          }
          strokeWidth={1.2}
          strokeDasharray={dashArray}
          strokeDashoffset={6}
          style={{
            animation: `flow-bwd ${animDuration} linear infinite`,
            opacity: lineOpacity * 0.6,
            transition: "stroke 0.2s, opacity 0.15s",
          }}
        />
      )}

      {/* 連線標籤（hover 時才顯示） */}
      <EdgeLabelRenderer>
        {/* biome-ignore lint/a11y/noStaticElementInteractions: label tooltip follows pointer without keyboard focus */}
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: showLabel ? "all" : "none",
            opacity: showLabel ? 1 : 0,
            transition: "opacity 0.15s",
          }}
          className="nodrag nopan"
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          <div
            className={`flex items-center gap-1.5 rounded-md px-2 py-0.5 ${
              isInbound
                ? "bg-blue-500/10 border border-blue-500/20"
                : isVMtoVM
                  ? "bg-amber-500/10 border border-amber-500/20"
                  : "bg-emerald-500/10 border border-emerald-500/20"
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                isInbound
                  ? "bg-blue-400"
                  : isVMtoVM
                    ? "bg-amber-400"
                    : "bg-emerald-400"
              }`}
            />
            <span className="text-xs text-foreground/80 whitespace-nowrap">
              {isInbound ? `入站 ${portLabel}` : isGateway ? "上網" : portLabel}
            </span>
            {isBidirectional && (
              <span className="text-xs text-muted-foreground">↔</span>
            )}

            {/* 刪除按鈕（hover 時顯示） */}
            {hovered && (
              <button
                type="button"
                onClick={() =>
                  data?.onDelete?.(data.sourceVmid, data.targetVmid)
                }
                className="ml-0.5 p-0.5 rounded hover:bg-red-900/50 text-muted-foreground hover:text-red-400 transition-colors"
                title="刪除連線"
              >
                <X className="w-2.5 h-2.5" />
              </button>
            )}
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
