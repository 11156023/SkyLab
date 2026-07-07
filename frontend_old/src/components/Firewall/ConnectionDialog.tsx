import { useQuery } from "@tanstack/react-query"
import { AlertTriangle, Info, Plug, Plus, Shield, Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useAuth from "@/hooks/useAuth"
import { GatewayApiService } from "@/services/gateway"

// PVE 支援的所有協定
const PVE_PROTOCOLS = [
  { value: "tcp", label: "TCP", hasPort: true },
  { value: "udp", label: "UDP", hasPort: true },
  { value: "sctp", label: "SCTP", hasPort: true },
  { value: "dccp", label: "DCCP", hasPort: true },
  { value: "udplite", label: "UDPLite", hasPort: true },
  { value: "icmp", label: "ICMP", hasPort: false },
  { value: "icmpv6", label: "ICMPv6", hasPort: false },
  { value: "igmp", label: "IGMP", hasPort: false },
  { value: "esp", label: "ESP", hasPort: false },
  { value: "ah", label: "AH", hasPort: false },
  { value: "gre", label: "GRE", hasPort: false },
  { value: "ospf", label: "OSPF", hasPort: false },
  { value: "vrrp", label: "VRRP", hasPort: false },
] as const

const PROTOCOLS_WITH_PORT: Set<string> = new Set(
  PVE_PROTOCOLS.filter((p) => p.hasPort).map((p) => p.value),
)

export type PortSpec = {
  port: number
  protocol: string
  external_port?: number | null
  domain?: string | null
  enable_https?: boolean
}

type Props = {
  open: boolean
  sourceVmid: number | null // null = Internet
  sourceName: string
  targetVmid: number | null
  targetName: string
  onConfirm: (ports: PortSpec[], direction: "one_way" | "bidirectional") => void
  onClose: () => void
}

type PortPair = {
  id: string
  external: number
  internal: number
  protocol: string
}

type FirewallPortRow = {
  id: string
  port: number
  protocol: string
}

function createPortPair(
  external = 8080,
  internal = 80,
  protocol = "tcp",
): PortPair {
  return {
    id: crypto.randomUUID(),
    external,
    internal,
    protocol,
  }
}

function createFirewallPort(port = 80, protocol = "tcp"): FirewallPortRow {
  return {
    id: crypto.randomUUID(),
    port,
    protocol,
  }
}

// PVE 保留 port（前端提示用）
const RESERVED_PORTS = new Set([
  22, 80, 443, 3128, 4007, 4008, 5900, 5901, 5902, 5903, 5904, 5905, 6789, 6800,
  6801, 6802, 6803, 8006, 8007, 111,
])

// 入站存取模式
type InboundMode = "port" | "firewall"

export function ConnectionDialog({
  open,
  sourceVmid,
  sourceName,
  targetName,
  targetVmid,
  onConfirm,
  onClose,
}: Props) {
  const { user: currentUser } = useAuth()
  const isGateway = targetVmid === null
  const isInbound = sourceVmid === null
  const isAdmin = currentUser?.role === "admin" || currentUser?.is_superuser

  // ─── 入站模式狀態 ────────────────────────────────────────────────────────
  const [inboundMode, setInboundMode] = useState<InboundMode>("port")

  // 🔌 Port 轉發模式
  const [portPairs, setPortPairs] = useState<PortPair[]>([createPortPair()])

  // 🔓 僅開放模式 & 非入站通用
  const [ports, setPorts] = useState<FirewallPortRow[]>([createFirewallPort()])

  const [direction, setDirection] = useState<"one_way" | "bidirectional">(
    "one_way",
  )

  // Gateway VM 狀態查詢
  const { data: gatewayConfig } = useQuery({
    queryKey: ["gateway-config-conn"],
    queryFn: GatewayApiService.getConfig,
    enabled: isInbound && isAdmin,
    staleTime: 30_000,
  })
  const isGatewayConfigured = isAdmin
    ? (gatewayConfig?.is_configured ?? false)
    : false
  const gatewaySetupMessage = isAdmin
    ? "Gateway VM 尚未設定，請先至「Gateway VM 管理」設定後再使用此功能。"
    : "此功能需要管理員先完成 Gateway VM 設定。"
  const needsGateway = isInbound && inboundMode === "port"

  // ─── 確認送出 ────────────────────────────────────────────────────────────
  const handleConfirm = () => {
    let result: PortSpec[]

    if (isInbound) {
      switch (inboundMode) {
        case "port": {
          const valid = portPairs.filter(
            (p) =>
              p.external >= 1 &&
              p.external <= 65535 &&
              p.internal >= 1 &&
              p.internal <= 65535,
          )
          if (valid.length === 0) {
            toast.error("請輸入有效的 port")
            return
          }
          result = valid.map((p) => ({
            port: p.internal,
            protocol: p.protocol,
            external_port: p.external,
          }))
          break
        }
        default: {
          const valid = ports.filter((p) =>
            PROTOCOLS_WITH_PORT.has(p.protocol)
              ? p.port >= 1 && p.port <= 65535
              : true,
          )
          if (valid.length === 0) {
            toast.error("請輸入有效的設定")
            return
          }
          result = valid.map((p) => ({
            port: PROTOCOLS_WITH_PORT.has(p.protocol) ? p.port : 0,
            protocol: p.protocol,
          }))
          break
        }
      }
    } else {
      // 非入站（VM→Gateway / VM→VM）
      const valid = ports.filter((p) =>
        PROTOCOLS_WITH_PORT.has(p.protocol)
          ? p.port >= 1 && p.port <= 65535
          : true,
      )
      if (valid.length === 0) {
        toast.error("請輸入有效的設定")
        return
      }
      result = valid.map((p) => ({
        port: PROTOCOLS_WITH_PORT.has(p.protocol) ? p.port : 0,
        protocol: p.protocol,
      }))
    }

    onConfirm(result, direction)
    // 重置
    setPortPairs([createPortPair()])
    setPorts([createFirewallPort()])
    setDirection("one_way")
    setInboundMode("port")
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border-border text-foreground sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-foreground">設定連線規則</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* 連線方向說明 */}
          <div className="text-sm text-muted-foreground bg-card rounded-lg p-3 border border-border">
            {isInbound ? (
              <>
                <span className="text-blue-400 font-medium">Internet</span>
                <span className="text-muted-foreground"> → </span>
                <span className="text-blue-400 font-medium">{targetName}</span>
                <span className="text-muted-foreground">（入站開放）</span>
              </>
            ) : (
              <>
                <span className="text-emerald-400 font-medium">
                  {sourceName}
                </span>
                {isGateway ? (
                  <span className="text-muted-foreground">
                    {" "}
                    → 連到 Internet（上網）
                  </span>
                ) : (
                  <>
                    <span className="text-muted-foreground"> → </span>
                    <span className="text-emerald-400 font-medium">
                      {targetName}
                    </span>
                  </>
                )}
              </>
            )}
          </div>

          {/* ── 入站模式選擇器 ──────────────────────────────────────────── */}
          {isInbound && (
            <>
              <div className="space-y-2">
                <Label className="text-foreground/80 text-sm">
                  你想怎麼從外面連到這台 VM？
                </Label>
                <div className="grid grid-cols-2 gap-2">
                  {(
                    [
                      {
                        mode: "port" as InboundMode,
                        icon: Plug,
                        label: "用 Port 號",
                        desc: "外網 Port → VM",
                      },
                      {
                        mode: "firewall" as InboundMode,
                        icon: Shield,
                        label: "僅開放防火牆",
                        desc: "只開放 Port",
                      },
                    ] as const
                  ).map(({ mode, icon: Icon, label, desc }) => (
                    <button
                      type="button"
                      key={mode}
                      onClick={() => setInboundMode(mode)}
                      className={`flex flex-col items-center gap-1.5 px-3 py-3 rounded-lg text-xs transition-all ${
                        inboundMode === mode
                          ? "bg-emerald-950/60 border-2 border-emerald-600 text-emerald-300"
                          : "bg-card border border-border text-muted-foreground hover:border-ring/50"
                      }`}
                    >
                      <Icon className="w-4 h-4" />
                      <span className="font-medium">{label}</span>
                      <span className="text-[10px] text-muted-foreground">
                        {desc}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {/* ── 情境提示 ──────────────────────────────────────────── */}
              {needsGateway && !isGatewayConfigured && (
                <div className="flex gap-2 items-start bg-orange-950/40 border border-orange-700/60 rounded-lg px-3 py-2.5 text-xs text-orange-300">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>
                    <span className="font-medium">{gatewaySetupMessage}</span>
                  </span>
                </div>
              )}
              {needsGateway && isGatewayConfigured && (
                <div className="flex gap-2 items-start bg-emerald-950/40 border border-emerald-700/60 rounded-lg px-3 py-2.5 text-xs text-emerald-300">
                  <Plug className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>
                    建立後將同時更新{" "}
                    <span className="font-medium">PVE 防火牆 + Gateway VM</span>
                    （立即生效）
                  </span>
                </div>
              )}
              <div className="flex gap-2 items-start bg-sky-950/30 border border-sky-700/40 rounded-lg px-3 py-2.5 text-xs text-sky-300">
                <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span>
                  若要設定網域型反向代理，請改到新的「反向代理」頁面管理。
                </span>
              </div>
              {inboundMode === "firewall" && (
                <div className="flex gap-2 items-start bg-blue-950/30 border border-blue-700/40 rounded-lg px-3 py-2.5 text-xs text-blue-300">
                  <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>
                    僅在 PVE 防火牆開放此
                    Port，不設定外部存取路徑。適合同網段內的 VM 互連使用。
                  </span>
                </div>
              )}
            </>
          )}

          {/* ── 🔌 Port 轉發模式 ──────────────────────────────────── */}
          {isInbound && inboundMode === "port" && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs text-muted-foreground px-1">
                <span className="w-24">外網 Port</span>
                <span className="text-muted-foreground/50">→</span>
                <span className="w-24">內部 Port</span>
                <span className="w-24">協定</span>
              </div>
              {portPairs.map((pair, i) => {
                const extWarning = RESERVED_PORTS.has(pair.external)
                return (
                  <div key={pair.id} className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Input
                        type="number"
                        min={1}
                        max={65535}
                        value={pair.external}
                        onChange={(e) => {
                          const updated = [...portPairs]
                          updated[i] = {
                            ...updated[i],
                            external: parseInt(e.target.value, 10) || 0,
                          }
                          setPortPairs(updated)
                        }}
                        className={`bg-card border-border text-foreground w-24 h-8 text-sm ${extWarning ? "border-yellow-600" : ""}`}
                      />
                      <span className="text-muted-foreground text-sm">→</span>
                      <Input
                        type="number"
                        min={1}
                        max={65535}
                        value={pair.internal}
                        onChange={(e) => {
                          const updated = [...portPairs]
                          updated[i] = {
                            ...updated[i],
                            internal: parseInt(e.target.value, 10) || 0,
                          }
                          setPortPairs(updated)
                        }}
                        className="bg-card border-border text-foreground w-24 h-8 text-sm"
                      />
                      <Select
                        value={pair.protocol}
                        onValueChange={(v) => {
                          const updated = [...portPairs]
                          updated[i] = { ...updated[i], protocol: v }
                          setPortPairs(updated)
                        }}
                      >
                        <SelectTrigger className="bg-card border-border text-foreground w-24 h-8 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent className="bg-card border-border text-foreground">
                          <SelectItem value="tcp">TCP</SelectItem>
                          <SelectItem value="udp">UDP</SelectItem>
                        </SelectContent>
                      </Select>
                      {portPairs.length > 1 && (
                        <button
                          type="button"
                          onClick={() =>
                            setPortPairs(
                              portPairs.filter(
                                (currentPair) => currentPair.id !== pair.id,
                              ),
                            )
                          }
                          className="p-1 hover:text-red-400 text-muted-foreground transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                    {extWarning && (
                      <p className="text-xs text-yellow-500 pl-1">
                        Port {pair.external} 為系統保留 port，可能無法使用
                      </p>
                    )}
                  </div>
                )
              })}
              <button
                type="button"
                onClick={() =>
                  setPortPairs([...portPairs, createPortPair(0, 0, "tcp")])
                }
                className="flex items-center gap-1 text-xs text-emerald-400 hover:text-emerald-300 transition-colors"
              >
                <Plus className="w-3 h-3" />
                新增端口
              </button>
            </div>
          )}

          {/* ── 🔓 僅開放模式（入站）/ 通用端口設定（非入站） ──────── */}
          {(isInbound ? inboundMode === "firewall" : true) &&
            !(isInbound && inboundMode !== "firewall") && (
              <div className="space-y-2">
                <Label className="text-foreground/80 text-sm">允許端口</Label>
                {ports.map((port, index) => (
                  <div key={port.id} className="flex items-center gap-2">
                    {PROTOCOLS_WITH_PORT.has(port.protocol) ? (
                      <Input
                        type="number"
                        min={1}
                        max={65535}
                        value={port.port}
                        onChange={(e) => {
                          const updated = [...ports]
                          updated[index] = {
                            ...updated[index],
                            port: parseInt(e.target.value, 10) || 80,
                          }
                          setPorts(updated)
                        }}
                        className="bg-card border-border text-foreground w-24 h-8 text-sm"
                        placeholder="80"
                      />
                    ) : (
                      <div className="w-24 h-8 flex items-center px-2 text-xs text-muted-foreground bg-card border border-border rounded-md">
                        無端口
                      </div>
                    )}
                    <Select
                      value={port.protocol}
                      onValueChange={(v) => {
                        const updated = [...ports]
                        updated[index] = { ...updated[index], protocol: v }
                        setPorts(updated)
                      }}
                    >
                      <SelectTrigger className="bg-card border-border text-foreground w-24 h-8 text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="bg-card border-border text-foreground">
                        {PVE_PROTOCOLS.map((p) => (
                          <SelectItem key={p.value} value={p.value}>
                            {p.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {ports.length > 1 && (
                      <button
                        type="button"
                        onClick={() =>
                          setPorts(
                            ports.filter(
                              (currentPort) => currentPort.id !== port.id,
                            ),
                          )
                        }
                        className="p-1 hover:text-red-400 text-muted-foreground transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() =>
                    setPorts([...ports, createFirewallPort(443, "tcp")])
                  }
                  className="flex items-center gap-1 text-xs text-emerald-400 hover:text-emerald-300 transition-colors"
                >
                  <Plus className="w-3 h-3" />
                  新增端口
                </button>
              </div>
            )}

          {/* 方向設定（VM 間連線才顯示） */}
          {!isGateway && !isInbound && (
            <div className="space-y-2">
              <Label className="text-foreground/80 text-sm">連線方向</Label>
              <div className="flex gap-2">
                {(["one_way", "bidirectional"] as const).map((dir) => (
                  <button
                    type="button"
                    key={dir}
                    onClick={() => setDirection(dir)}
                    className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                      direction === dir
                        ? "bg-emerald-900/50 border border-emerald-600 text-emerald-400"
                        : "bg-card border border-border text-muted-foreground hover:border-ring/50"
                    }`}
                  >
                    {dir === "one_way" ? "→ 單向" : "↔ 雙向"}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button
            variant="ghost"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            取消
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={needsGateway && !isGatewayConfigured}
            className="bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40"
          >
            建立連線
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
