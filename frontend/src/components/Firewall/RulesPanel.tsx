import { useQuery } from "@tanstack/react-query"
import { ChevronRight, Shield } from "lucide-react"
import { useState } from "react"
import { Badge } from "@/components/ui/badge"
import { FirewallService } from "@/services/firewall"

type Props = {
  vmid: number | null
  vmName: string
  onClose: () => void
}

export function RulesPanel({ vmid, vmName, onClose }: Props) {
  const [show, setShow] = useState(true)

  const { data: rules, isLoading } = useQuery({
    queryKey: ["firewall-rules", vmid],
    queryFn: () => FirewallService.listFirewallRules({ vmid: vmid! }),
    enabled: vmid !== null,
    staleTime: 10_000,
  })

  const { data: options } = useQuery({
    queryKey: ["firewall-options", vmid],
    queryFn: () => FirewallService.getFirewallOptions({ vmid: vmid! }),
    enabled: vmid !== null,
    staleTime: 10_000,
  })

  if (!vmid) return null

  return (
    <div
      className={`
        absolute right-0 top-0 h-full bg-card border-l border-border
        transition-all duration-300 flex flex-col z-10
        ${show ? "w-80" : "w-0 overflow-hidden"}
      `}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-emerald-400" />
          <span className="text-sm font-medium text-foreground truncate">
            {vmName}
          </span>
        </div>
        <button
          type="button"
          onClick={() => {
            setShow(false)
            onClose()
          }}
          className="p-1 text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* 防火牆選項 */}
      {options && (
        <div className="px-4 py-3 border-b border-border bg-muted">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">狀態</span>
            <Badge
              className={
                options.enable
                  ? "bg-emerald-900/50 text-emerald-400 border-emerald-700"
                  : "bg-red-900/50 text-red-400 border-red-700"
              }
            >
              {options.enable ? "已啟用" : "未啟用"}
            </Badge>
          </div>
          <div className="flex items-center justify-between text-xs mt-1.5">
            <span className="text-muted-foreground">入站預設</span>
            <span className="text-foreground/80">{options.policy_in}</span>
          </div>
          <div className="flex items-center justify-between text-xs mt-1">
            <span className="text-muted-foreground">出站預設</span>
            <span className="text-foreground/80">{options.policy_out}</span>
          </div>
        </div>
      )}

      {/* 規則列表 */}
      <div className="flex-1 overflow-y-auto">
        <div className="px-4 py-2 text-xs text-muted-foreground font-medium uppercase tracking-wider">
          防火牆規則
        </div>

        {isLoading && (
          <div className="px-4 py-8 text-center text-xs text-muted-foreground/50">
            載入中...
          </div>
        )}

        {!isLoading && (!rules || rules.length === 0) && (
          <div className="px-4 py-8 text-center text-xs text-muted-foreground/50">
            無規則
          </div>
        )}

        {rules?.map((rule) => (
          <div
            key={rule.pos}
            className={`
              px-4 py-2.5 border-b border-border/50 hover:bg-accent
              ${rule.is_managed ? "opacity-70" : ""}
            `}
          >
            <div className="flex items-center gap-2">
              <span
                className={`
                  text-xs font-mono px-1.5 py-0.5 rounded
                  ${
                    rule.type === "in"
                      ? "bg-blue-900/40 text-blue-400"
                      : "bg-orange-900/40 text-orange-400"
                  }
                `}
              >
                {rule.type.toUpperCase()}
              </span>
              <span
                className={`
                  text-xs font-mono px-1.5 py-0.5 rounded
                  ${
                    rule.action === "ACCEPT"
                      ? "bg-emerald-900/40 text-emerald-400"
                      : "bg-red-900/40 text-red-400"
                  }
                `}
              >
                {rule.action}
              </span>
              {rule.enable === 0 && (
                <span className="text-xs text-muted-foreground/50">(停用)</span>
              )}
            </div>

            {(rule.source || rule.dest) && (
              <div className="mt-1 text-xs text-muted-foreground font-mono">
                {rule.source && <span>{rule.source} → </span>}
                {rule.dest && <span>{rule.dest}</span>}
              </div>
            )}

            {(rule.proto || rule.dport) && (
              <div className="mt-0.5 text-xs text-muted-foreground">
                {rule.proto && <span className="uppercase">{rule.proto}</span>}
                {rule.dport && <span>:{rule.dport}</span>}
              </div>
            )}

            {rule.comment && (
              <div className="mt-0.5 text-xs text-muted-foreground/50 truncate">
                {rule.is_managed ? "🔒 " : ""}
                {rule.comment.replace("SkyLab:", "")}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
