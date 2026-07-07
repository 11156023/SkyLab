import { useQuery } from "@tanstack/react-query"
import type { ColumnDef } from "@tanstack/react-table"
import type { TFunction } from "i18next"

import type { VMRequestPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  type VmRequestAvailabilityResponse,
  VmRequestAvailabilityService,
} from "@/services/vmRequestAvailability"
import { ReviewActions } from "./ReviewActions"

function formatScheduledRange(startAt?: string | null, endAt?: string | null) {
  if (!startAt || !endAt) return "未設定"

  const formatter = new Intl.DateTimeFormat("zh-TW", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Taipei",
  })

  return `${formatter.format(new Date(startAt))} - ${formatter.format(new Date(endAt))}`
}

function formatTemplateLabel(request: VMRequestPublic) {
  if (request.resource_type === "lxc") {
    if (!request.ostemplate) return "未設定"
    return (
      request.ostemplate
        .split("/")
        .pop()
        ?.replace(/\.tar\..+$/i, "")
        .replace(/\.img$/i, "") || request.ostemplate
    )
  }

  if (request.template_id) return `Template #${request.template_id}`
  return "未設定"
}

function formatSpecLabel(request: VMRequestPublic) {
  const base = `${request.cores} Core / ${(request.memory / 1024).toFixed(1)} GB RAM`

  if (request.resource_type === "vm") {
    return `${base} / ${(request.disk_size ?? 0).toFixed(0)} GB Disk`
  }

  return `${base} / ${(request.rootfs_size ?? 0).toFixed(0)} GB Rootfs`
}

function getSelectedSlots(
  data: VmRequestAvailabilityResponse | undefined,
  startAt?: string | null,
  endAt?: string | null,
) {
  if (!data || !startAt || !endAt) return []

  const startMs = new Date(startAt).getTime()
  const endMs = new Date(endAt).getTime()

  return data.days
    .flatMap((day) => day.slots)
    .filter((slot) => {
      const slotStart = new Date(slot.start_at).getTime()
      return slotStart >= startMs && slotStart < endMs
    })
}

function PlacementStatusCell({ request }: { request: VMRequestPublic }) {
  const hasSchedule = !!(request.start_at && request.end_at)
  const isTerminal = ["approved", "rejected", "cancelled"].includes(
    request.status,
  )

  const query = useQuery({
    queryKey: [
      "vm-request-availability-row",
      request.id,
      request.start_at,
      request.end_at,
    ],
    queryFn: () =>
      VmRequestAvailabilityService.getByRequestId({
        requestId: request.id,
        days: 7,
        timezone: "Asia/Taipei",
      }),
    staleTime: 30_000,
    enabled: hasSchedule && !isTerminal,
  })

  if (request.status === "approved") {
    return (
      <div className="flex flex-col gap-1">
        <Badge variant="default" className="w-fit">
          已通過
        </Badge>
        <span className="text-xs text-muted-foreground">
          {request.assigned_node
            ? `節點 ${request.assigned_node}`
            : "待同步節點"}
        </span>
      </div>
    )
  }

  if (request.status === "rejected") {
    return (
      <Badge variant="outline" className="w-fit">
        已拒絕
      </Badge>
    )
  }

  if (request.status === "cancelled") {
    return (
      <Badge variant="secondary" className="w-fit">
        已撤銷
      </Badge>
    )
  }

  if (!request.start_at || !request.end_at) {
    return (
      <div className="flex flex-col gap-1">
        <Badge variant="outline" className="w-fit">
          未排程
        </Badge>
        <span className="text-xs text-muted-foreground">尚未選擇時段</span>
      </div>
    )
  }

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-1">
        <Badge variant="outline" className="w-fit">
          模擬中
        </Badge>
        <span className="text-xs text-muted-foreground">
          正在檢查容量與節點
        </span>
      </div>
    )
  }

  if (query.isError) {
    return (
      <div className="flex flex-col gap-1">
        <Badge variant="destructive" className="w-fit">
          模擬失敗
        </Badge>
        <span className="text-xs text-muted-foreground">暫時無法取得狀態</span>
      </div>
    )
  }

  const selectedSlots = getSelectedSlots(
    query.data,
    request.start_at,
    request.end_at,
  )
  const feasible =
    selectedSlots.length > 0 &&
    selectedSlots.every(
      (slot) => slot.status === "available" || slot.status === "limited",
    )
  const nodes = Array.from(
    selectedSlots.reduce((acc, slot) => {
      for (const node of slot.recommended_nodes) {
        acc.set(node, (acc.get(node) ?? 0) + 1)
      }
      return acc
    }, new Map<string, number>()),
  )
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([node]) => node)

  return (
    <div className="flex flex-col gap-1">
      <Badge variant={feasible ? "default" : "destructive"} className="w-fit">
        {feasible ? "可放入" : "放不下"}
      </Badge>
      <span className="text-xs text-muted-foreground">
        {nodes.length > 0 ? `節點 ${nodes.join(", ")}` : "尚無可用節點"}
      </span>
    </div>
  )
}

export const createAdminRequestColumns = (
  t: TFunction<string, string>,
): ColumnDef<VMRequestPublic>[] => [
  {
    accessorKey: "user_full_name",
    header: t("approvals:review.applicant"),
    cell: ({ row }) => (
      <div className="flex flex-col">
        <span className="font-medium">
          {row.original.user_full_name || "N/A"}
        </span>
        <span className="text-xs text-muted-foreground">
          {row.original.user_email}
        </span>
      </div>
    ),
  },
  {
    accessorKey: "hostname",
    header: t("approvals:review.hostname"),
    cell: ({ row }) => (
      <span className="font-medium">{row.original.hostname}</span>
    ),
  },
  {
    accessorKey: "resource_type",
    header: t("approvals:review.type"),
    cell: ({ row }) => (
      <Badge variant="secondary">
        {row.original.resource_type === "lxc"
          ? t("applications:types.lxc")
          : t("applications:types.qemu")}
      </Badge>
    ),
  },
  {
    id: "template",
    header: "作業系統 / 模板",
    cell: ({ row }) => (
      <div className="min-w-[180px] max-w-[220px] overflow-hidden">
        <div className="font-medium truncate">
          {formatTemplateLabel(row.original)}
        </div>
        <div className="mt-1 text-xs text-muted-foreground truncate">
          {row.original.os_info?.trim() || "未填寫作業系統資訊"}
        </div>
      </div>
    ),
  },
  {
    id: "formDetails",
    header: "表單資訊",
    cell: ({ row }) => (
      <div className="min-w-[190px] max-w-[220px] overflow-hidden text-sm">
        <div className="truncate">
          {row.original.environment_type || "未設定環境類型"}
        </div>
        {row.original.resource_type === "vm" ? (
          <div className="mt-1 text-xs text-muted-foreground truncate">
            使用者名稱：{row.original.username || "未填寫"}
          </div>
        ) : null}
        <div className="mt-1 text-xs text-muted-foreground truncate">
          儲存：{row.original.storage || "未設定"}
        </div>
      </div>
    ),
  },
  {
    accessorKey: "reason",
    header: t("approvals:review.reason"),
    cell: ({ row }) => (
      <span className="block min-w-[220px] max-w-[260px] truncate text-muted-foreground">
        {row.original.reason}
      </span>
    ),
  },
  {
    id: "specs",
    header: t("approvals:review.specs"),
    cell: ({ row }) => (
      <span className="block min-w-[210px] max-w-[240px] truncate text-sm text-muted-foreground">
        {formatSpecLabel(row.original)}
      </span>
    ),
  },
  {
    id: "schedule",
    header: "申請時段",
    cell: ({ row }) => (
      <span className="block min-w-[210px] max-w-[240px] truncate text-sm text-muted-foreground">
        {formatScheduledRange(row.original.start_at, row.original.end_at)}
      </span>
    ),
  },
  {
    id: "placementStatus",
    header: "放置狀態",
    cell: ({ row }) => <PlacementStatusCell request={row.original} />,
  },
  {
    accessorKey: "created_at",
    header: t("applications:table.applicationTime"),
    cell: ({ row }) => (
      <span className="text-sm text-muted-foreground">
        {new Date(row.original.created_at).toLocaleString("zh-TW")}
      </span>
    ),
  },
  {
    id: "actions",
    header: "審核",
    cell: ({ row }) => <ReviewActions request={row.original} />,
  },
]

const noopT = ((key: string) => key) as TFunction

export const adminRequestColumns: ColumnDef<VMRequestPublic>[] =
  createAdminRequestColumns(noopT)
