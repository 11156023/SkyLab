import { useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import type { RowSelectionState } from "@tanstack/react-table"
import { Monitor, RefreshCw } from "lucide-react"
import { Suspense, useCallback, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { ResourcesService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import { JobDetailDialog } from "@/components/Jobs/JobDetailDialog"
import PendingItems from "@/components/Pending/PendingItems"
import { BatchActionBar } from "@/components/Resources/BatchActionBar"
import CreateContainer from "@/components/Resources/CreateResources"
import { createColumns } from "@/components/Resources/columns"
import { TerminalConsoleDialog } from "@/components/Terminal"
import { Button } from "@/components/ui/button"
import { VNCConsoleDialog } from "@/components/VNC"
import { requireAdminUser } from "@/features/auth/guards"
import useAuth from "@/hooks/useAuth"
import { queryKeys } from "@/lib/queryKeys"
import {
  deletionToMeta,
  useDeletingResources,
  useDeletingResourcesLiveSync,
} from "@/services/deletingResources"
import {
  pendingToFakeRow,
  type ResourceRow,
  usePendingResources,
  usePendingResourcesLiveSync,
} from "@/services/pendingResources"

function getVMsQueryOptions() {
  return {
    queryFn: () => ResourcesService.listResources({}),
    queryKey: queryKeys.resources.all,
  }
}

export const Route = createFileRoute("/_layout/resources")({
  component: VirtualMachines,
  beforeLoad: () => requireAdminUser(),
  head: () => ({
    meta: [
      {
        title: "Virtual Machines - SkyLab",
      },
    ],
  }),
})

function VMsTableContent({
  onOpenConsole,
  rowSelection,
  onRowSelectionChange,
  onOpenCreatingDetail,
  onOpenDeletingDetail,
}: {
  onOpenConsole: (vmid: number, name: string, type: string) => void
  rowSelection: RowSelectionState
  onRowSelectionChange: (selection: RowSelectionState) => void
  onOpenCreatingDetail: (requestId: string) => void
  onOpenDeletingDetail: (requestId: string) => void
}) {
  const { t } = useTranslation(["resources"])
  const navigate = useNavigate()
  const { user } = useAuth()
  const isAdmin = Boolean(user?.is_superuser)
  const { data: resources } = useSuspenseQuery(getVMsQueryOptions())
  const { data: pending = [] } = usePendingResources({ isAdmin })
  const { data: deletingMap } = useDeletingResources({ isAdmin })

  // Merge：creating placeholders 排在最前面，已完成的 VM 在後（並標記刪除中的 row）
  const merged = useMemo<ResourceRow[]>(() => {
    const realVmids = new Set(resources.map((r) => r.vmid))
    // 過濾掉 vmid 已存在的 pending（已完成 provision，真實 row 會在 resources 中）
    const stillCreating = pending.filter(
      (p) => p.vmid == null || !realVmids.has(p.vmid),
    )
    const placeholders = stillCreating.map((req, idx) =>
      pendingToFakeRow(req, idx),
    )
    const annotated = (resources as ResourceRow[]).map((r) => {
      const del = r.vmid == null ? undefined : deletingMap?.get(r.vmid)
      return del ? { ...r, _deleting: deletionToMeta(del) } : r
    })
    return [...placeholders, ...annotated]
  }, [resources, pending, deletingMap])

  const handleRowClick = useCallback(
    (row: ResourceRow) => {
      if (row._creating) {
        onOpenCreatingDetail(row._creating.request_id)
        return
      }
      if (row._deleting) {
        onOpenDeletingDetail(row._deleting.request_id)
        return
      }
      if (row.vmid == null) return
      navigate({
        to: "/resources/$vmid",
        params: { vmid: row.vmid.toString() },
      })
    },
    [navigate, onOpenCreatingDetail, onOpenDeletingDetail],
  )

  const columns = useMemo(
    () =>
      createColumns(t, onOpenConsole, {
        enableSelection: true,
        enableMiningExempt: true,
      }),
    [t, onOpenConsole],
  )

  if (merged.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <Monitor className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">{t("resources:page.noVMs")}</h3>
        <p className="text-muted-foreground">
          {t("resources:page.noVMsDescription")}
        </p>
      </div>
    )
  }

  return (
    <DataTable
      columns={columns}
      data={merged}
      onRowClick={(row) => handleRowClick(row)}
      enableRowSelection
      rowSelection={rowSelection}
      onRowSelectionChange={onRowSelectionChange}
      getRowId={(row) =>
        row._creating
          ? `creating:${row._creating.request_id}`
          : row._deleting
            ? `deleting:${row._deleting.request_id}`
            : String(row.vmid)
      }
    />
  )
}

function VMsTable({
  onOpenConsole,
  rowSelection,
  onRowSelectionChange,
  onOpenCreatingDetail,
  onOpenDeletingDetail,
}: {
  onOpenConsole: (vmid: number, name: string, type: string) => void
  rowSelection: RowSelectionState
  onRowSelectionChange: (selection: RowSelectionState) => void
  onOpenCreatingDetail: (requestId: string) => void
  onOpenDeletingDetail: (requestId: string) => void
}) {
  return (
    <Suspense fallback={<PendingItems />}>
      <VMsTableContent
        onOpenConsole={onOpenConsole}
        rowSelection={rowSelection}
        onRowSelectionChange={onRowSelectionChange}
        onOpenCreatingDetail={onOpenCreatingDetail}
        onOpenDeletingDetail={onOpenDeletingDetail}
      />
    </Suspense>
  )
}

function RefreshButton() {
  const { t } = useTranslation(["resources"])
  const queryClient = useQueryClient()

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
  }

  return (
    <Button variant="outline" onClick={handleRefresh}>
      <RefreshCw className="mr-2 h-4 w-4" />
      {t("resources:page.refresh")}
    </Button>
  )
}

function VirtualMachines() {
  const { t } = useTranslation(["resources"])
  usePendingResourcesLiveSync()
  useDeletingResourcesLiveSync()
  const [vncConsoleOpen, setVncConsoleOpen] = useState(false)
  const [terminalConsoleOpen, setTerminalConsoleOpen] = useState(false)
  const [selectedVM, setSelectedVM] = useState<{
    vmid: number
    name: string
    type: string
  } | null>(null)
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [focusJobId, setFocusJobId] = useState<string | null>(null)

  // 排除 placeholder（getRowId 為 "creating:..." / "deleting:..." 開頭）
  const selectedVmids = useMemo(
    () =>
      Object.keys(rowSelection)
        .filter(
          (k) =>
            rowSelection[k] &&
            !k.startsWith("creating:") &&
            !k.startsWith("deleting:"),
        )
        .map(Number)
        .filter((n) => Number.isFinite(n) && n > 0),
    [rowSelection],
  )

  const handleOpenConsole = (vmid: number, name: string, type: string) => {
    setSelectedVM({ vmid, name, type })
    if (type === "lxc") {
      setTerminalConsoleOpen(true)
    } else {
      setVncConsoleOpen(true)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {t("resources:page.title")}
          </h1>
          <p className="text-muted-foreground">
            {t("resources:page.description")}
          </p>
        </div>
        <div className="flex gap-2">
          <CreateContainer />
          <RefreshButton />
        </div>
      </div>
      <BatchActionBar
        selectedVmids={selectedVmids}
        onClearSelection={() => setRowSelection({})}
      />
      <VMsTable
        onOpenConsole={handleOpenConsole}
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        onOpenCreatingDetail={(requestId) =>
          setFocusJobId(`vm_request:${requestId}`)
        }
        onOpenDeletingDetail={(requestId) =>
          setFocusJobId(`deletion:${requestId}`)
        }
      />
      <VNCConsoleDialog
        vmid={selectedVM?.type === "qemu" ? selectedVM.vmid : null}
        vmName={selectedVM?.name}
        open={vncConsoleOpen}
        onOpenChange={setVncConsoleOpen}
      />
      <TerminalConsoleDialog
        vmid={selectedVM?.type === "lxc" ? selectedVM.vmid : null}
        vmName={selectedVM?.name}
        open={terminalConsoleOpen}
        onOpenChange={setTerminalConsoleOpen}
      />
      <JobDetailDialog jobId={focusJobId} onClose={() => setFocusJobId(null)} />
    </div>
  )
}
