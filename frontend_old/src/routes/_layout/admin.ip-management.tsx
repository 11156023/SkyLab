import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  AlertTriangle,
  Globe,
  HardDrive,
  Loader2,
  Network,
  Server,
  Trash2,
  Wifi,
} from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { requireAdminUser } from "@/features/auth/guards"
import {
  type IpAllocationPublic,
  IpManagementApiService,
  type SubnetConfigCreate,
  type SubnetConfigPublic,
} from "@/services/ipManagement"

export const Route = createFileRoute("/_layout/admin/ip-management")({
  beforeLoad: () => requireAdminUser(),
  component: IpManagementPage,
})

function getApiErrorMessage(error: unknown): string {
  if (error && typeof error === "object") {
    const maybeApiError = error as {
      body?: { detail?: string }
      message?: string
    }
    return maybeApiError.body?.detail ?? maybeApiError.message ?? "未知錯誤"
  }
  return "未知錯誤"
}

const PURPOSE_LABELS: Record<string, string> = {
  vm: "VM",
  lxc: "LXC",
  gateway_vm: "Gateway VM",
  subnet_gateway: "閘道",
  reserved: "保留",
}

function IpManagementPage() {
  const queryClient = useQueryClient()
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)

  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ["ip-management", "subnet"],
    queryFn: () => IpManagementApiService.getSubnetConfig(),
  })

  const { data: allocations, isLoading: allocLoading } = useQuery({
    queryKey: ["ip-management", "allocations"],
    queryFn: () => IpManagementApiService.getAllocations(),
    enabled: !!config,
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">IP 管理</h1>
        <p className="text-muted-foreground">管理內部子網配置與 IP 位址分配</p>
      </div>

      {configLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          {/* Stats cards - only show when configured */}
          {config && <StatsCards config={config} />}

          {/* Subnet config form — key forces remount when config changes */}
          <SubnetConfigForm
            key={config?.updated_at ?? "new"}
            config={config ?? undefined}
            onSaved={() => {
              queryClient.invalidateQueries({
                queryKey: ["ip-management"],
              })
            }}
          />

          {/* IP allocation table */}
          {config && (
            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <div>
                  <CardTitle>IP 分配記錄</CardTitle>
                  <CardDescription>
                    所有已分配的 IP 位址及其用途
                  </CardDescription>
                </div>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setShowDeleteDialog(true)}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  刪除子網配置
                </Button>
              </CardHeader>
              <CardContent>
                {allocLoading ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : (
                  <AllocationTable
                    allocations={allocations?.allocations ?? []}
                  />
                )}
              </CardContent>
            </Card>
          )}

          {/* Delete confirmation dialog */}
          <DeleteSubnetDialog
            open={showDeleteDialog}
            onOpenChange={setShowDeleteDialog}
            onConfirm={() => {
              queryClient.invalidateQueries({
                queryKey: ["ip-management"],
              })
              setShowDeleteDialog(false)
            }}
          />
        </>
      )}
    </div>
  )
}

// ─── Stats Cards ──────────────────────────────────────────────────────────────

function StatsCards({ config }: { config: SubnetConfigPublic }) {
  return (
    <div className="grid gap-4 md:grid-cols-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">子網 CIDR</CardTitle>
          <Network className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-xl font-bold">{config.cidr}</div>
          <p className="text-xs text-muted-foreground">
            Bridge: {config.bridge_name}
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">總可用 IP</CardTitle>
          <Globe className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-xl font-bold">{config.total_ips}</div>
          <p className="text-xs text-muted-foreground">
            主機位址數（排除網路/廣播）
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">已分配</CardTitle>
          <Server className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-xl font-bold">{config.used_ips}</div>
          <p className="text-xs text-muted-foreground">已使用 IP 數量</p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">可用</CardTitle>
          <Wifi className="h-4 w-4 text-muted-foreground" />
        </CardHeader>
        <CardContent>
          <div className="text-xl font-bold text-green-600 dark:text-green-400">
            {config.available_ips}
          </div>
          <p className="text-xs text-muted-foreground">剩餘可分配 IP</p>
        </CardContent>
      </Card>
    </div>
  )
}

// ─── Subnet Config Form ───────────────────────────────────────────────────────

function SubnetConfigForm({
  config,
  onSaved,
}: {
  config?: SubnetConfigPublic
  onSaved: () => void
}) {
  type FormValues = SubnetConfigCreate & { extra_blocked_subnets_text?: string }
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({
    defaultValues: config
      ? {
          cidr: config.cidr,
          gateway: config.gateway,
          bridge_name: config.bridge_name,
          gateway_vm_ip: config.gateway_vm_ip,
          dns_servers: config.dns_servers ?? "",
          extra_blocked_subnets_text: (config.extra_blocked_subnets ?? []).join(
            "\n",
          ),
        }
      : {
          cidr: "",
          gateway: "",
          bridge_name: "vmbr1",
          gateway_vm_ip: "",
          dns_servers: "",
          extra_blocked_subnets_text: "",
        },
  })

  const saveMutation = useMutation({
    mutationFn: (data: FormValues) => {
      const extra = (data.extra_blocked_subnets_text ?? "")
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean)
      const { extra_blocked_subnets_text: _omit, ...rest } = data
      return IpManagementApiService.upsertSubnetConfig({
        ...rest,
        dns_servers: data.dns_servers || null,
        extra_blocked_subnets: extra,
      })
    },
    onSuccess: () => {
      toast.success("子網配置已儲存")
      onSaved()
    },
    onError: (error: unknown) =>
      toast.error(`儲存失敗：${getApiErrorMessage(error)}`),
  })

  const ipPattern = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/

  return (
    <Card>
      <CardHeader>
        <CardTitle>{config ? "編輯子網配置" : "建立子網配置"}</CardTitle>
        <CardDescription>
          {config
            ? "修改內部子網配置。注意：已有 VM/LXC 分配時無法變更 CIDR。"
            : "設定內部子網後，系統才能為 VM/LXC 分配 IP 位址。"}
        </CardDescription>
        {!config && (
          <div className="flex items-center gap-3 rounded-lg border border-yellow-600/40 bg-yellow-50 px-4 py-3 dark:border-yellow-700/50 dark:bg-yellow-900/20">
            <AlertTriangle className="h-4 w-4 shrink-0 text-yellow-600 dark:text-yellow-400" />
            <span className="text-sm text-yellow-800 dark:text-yellow-300">
              尚未配置子網，所有 VM/LXC 建立功能將被停用。
            </span>
          </div>
        )}
      </CardHeader>
      <CardContent>
        <form
          onSubmit={handleSubmit((data) => saveMutation.mutate(data))}
          className="space-y-4"
        >
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="cidr">子網 CIDR *</Label>
              <Input
                id="cidr"
                placeholder="例如：10.10.0.0/24"
                {...register("cidr", {
                  required: "請輸入子網 CIDR",
                })}
              />
              {errors.cidr && (
                <p className="text-sm text-destructive">
                  {errors.cidr.message}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="gateway">閘道 IP *</Label>
              <Input
                id="gateway"
                placeholder="例如：10.10.0.1"
                {...register("gateway", {
                  required: "請輸入閘道 IP",
                  pattern: { value: ipPattern, message: "IP 格式不正確" },
                })}
              />
              {errors.gateway && (
                <p className="text-sm text-destructive">
                  {errors.gateway.message}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="bridge_name">Bridge 名稱 *</Label>
              <Input
                id="bridge_name"
                placeholder="例如：vmbr1"
                {...register("bridge_name", {
                  required: "請輸入 Bridge 名稱",
                })}
              />
              {errors.bridge_name && (
                <p className="text-sm text-destructive">
                  {errors.bridge_name.message}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="gateway_vm_ip">Gateway VM IP *</Label>
              <Input
                id="gateway_vm_ip"
                placeholder="例如：10.10.0.3"
                {...register("gateway_vm_ip", {
                  required: "請輸入 Gateway VM IP",
                  pattern: { value: ipPattern, message: "IP 格式不正確" },
                })}
              />
              {errors.gateway_vm_ip && (
                <p className="text-sm text-destructive">
                  {errors.gateway_vm_ip.message}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="dns_servers">DNS Servers</Label>
              <Input
                id="dns_servers"
                placeholder="例如：8.8.8.8,1.1.1.1"
                {...register("dns_servers")}
              />
              <p className="text-xs text-muted-foreground">
                多個 DNS 以逗號分隔（選填）
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="extra_blocked_subnets_text">
              預設封鎖網段 / IP（套用於所有 VM/LXC）
            </Label>
            <Textarea
              id="extra_blocked_subnets_text"
              placeholder="例如：&#10;192.168.100.0/24&#10;10.0.0.5"
              rows={4}
              {...register("extra_blocked_subnets_text")}
            />
            <p className="text-xs text-muted-foreground">
              每行一個 CIDR 或 IP（也可用逗號分隔）。儲存時會在所有 VM/LXC
              上建立/更新出站 DROP 規則並清除已移除的舊規則。
            </p>
          </div>

          <div className="flex justify-end">
            <Button type="submit" disabled={saveMutation.isPending}>
              {saveMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {config ? "更新配置" : "建立配置"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

// ─── Allocation Table ─────────────────────────────────────────────────────────

function AllocationTable({
  allocations,
}: {
  allocations: IpAllocationPublic[]
}) {
  if (allocations.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-8 text-muted-foreground">
        <HardDrive className="h-10 w-10" />
        <p>尚無 IP 分配記錄</p>
      </div>
    )
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>IP 位址</TableHead>
          <TableHead>用途</TableHead>
          <TableHead>VMID</TableHead>
          <TableHead>說明</TableHead>
          <TableHead>分配時間</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {allocations.map((alloc) => (
          <TableRow key={alloc.ip_address}>
            <TableCell className="font-mono">{alloc.ip_address}</TableCell>
            <TableCell>
              <PurposeBadge purpose={alloc.purpose} />
            </TableCell>
            <TableCell>{alloc.vmid ?? "—"}</TableCell>
            <TableCell className="text-muted-foreground">
              {alloc.description ?? "—"}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {new Date(alloc.allocated_at).toLocaleString()}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function PurposeBadge({ purpose }: { purpose: string }) {
  const variant =
    purpose === "vm" || purpose === "lxc"
      ? "default"
      : purpose === "gateway_vm"
        ? "secondary"
        : "outline"
  return <Badge variant={variant}>{PURPOSE_LABELS[purpose] ?? purpose}</Badge>
}

// ─── Delete Dialog ────────────────────────────────────────────────────────────

function DeleteSubnetDialog({
  open,
  onOpenChange,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onConfirm: () => void
}) {
  const deleteMutation = useMutation({
    mutationFn: () => IpManagementApiService.deleteSubnetConfig(),
    onSuccess: () => {
      toast.success("子網配置已刪除")
      onConfirm()
    },
    onError: (error: unknown) =>
      toast.error(`刪除失敗：${getApiErrorMessage(error)}`),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>刪除子網配置</DialogTitle>
          <DialogDescription>
            確定要刪除子網配置嗎？如果仍有 VM/LXC 的 IP 分配記錄，需先移除相關
            VM/LXC 才能刪除。刪除後所有 VM/LXC 建立功能將被停用。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            variant="destructive"
            disabled={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate()}
          >
            {deleteMutation.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            確定刪除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
