import { Handle, type Node, type NodeProps, Position } from "@xyflow/react"
import { Globe } from "lucide-react"

export type GatewayNodeData = {
  name: string
}

type GatewayNodeType = Node<GatewayNodeData, "gateway">

export function GatewayNode({ selected }: NodeProps<GatewayNodeType>) {
  return (
    <div
      className={`
        relative flex items-center gap-3 px-4 py-3
        bg-white border border-dashed rounded-xl cursor-default
        transition-all duration-200 min-w-[160px]
        ${
          selected
            ? "border-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.3)]"
            : "border-border"
        }
      `}
    >
      {/* Globe icon */}
      <div className="flex-shrink-0 w-8 h-8 bg-emerald-500/10 rounded-lg flex items-center justify-center">
        <Globe className="w-4 h-4 text-emerald-400" />
      </div>

      {/* 文字 */}
      <div className="flex-1">
        <div className="text-sm font-medium text-foreground">Internet</div>
        <div className="text-xs text-muted-foreground mt-0.5">網關 / 上網</div>
      </div>

      {/* Internet→VM 出站 handle（右側，藍色）*/}
      <Handle
        id="out"
        type="source"
        position={Position.Right}
        className="!w-2.5 !h-2.5 !bg-blue-400 !border-2 !border-card"
      />
      {/* VM→Internet 入站 handle（左側，綠色）*/}
      <Handle
        id="in"
        type="target"
        position={Position.Left}
        className="!w-2.5 !h-2.5 !bg-emerald-400 !border-2 !border-card"
      />
    </div>
  )
}
