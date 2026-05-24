import { useQueryClient, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import type { RowSelectionState } from "@tanstack/react-table"
import { Download, Monitor, RefreshCw } from "lucide-react"
import { Suspense, useCallback, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { OpenAPI, ResourcesService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import PendingItems from "@/components/Pending/PendingItems"
import { BatchActionBar } from "@/components/Resources/BatchActionBar"
import { createColumns } from "@/components/Resources/columns"
import { TerminalConsoleDialog } from "@/components/Terminal"
import { Button } from "@/components/ui/button"
import { VNCConsoleDialog } from "@/components/VNC"
import { queryKeys } from "@/lib/queryKeys"
import {
  deletionToMeta,
  useDeletingResources,
  useDeletingResourcesLiveSync,
} from "@/services/deletingResources"
import type { ResourceRow } from "@/services/pendingResources"

function getMyResourcesQueryOptions() {
  return {
    queryFn: () => ResourcesService.listMyResources(),
    queryKey: queryKeys.resources.my,
  }
}

export const Route = createFileRoute("/_layout/my-resources")({
  component: MyResources,
  head: () => ({
    meta: [
      {
        title: "My Resources - SkyLab",
      },
    ],
  }),
})

function MyResourcesTableContent({
  onOpenConsole,
  rowSelection,
  onRowSelectionChange,
}: {
  onOpenConsole: (vmid: number, name: string, type: string) => void
  rowSelection: RowSelectionState
  onRowSelectionChange: (selection: RowSelectionState) => void
}) {
  const { t } = useTranslation(["resources"])
  const navigate = useNavigate()
  const { data: resources } = useSuspenseQuery(getMyResourcesQueryOptions())
  const { data: deletingMap } = useDeletingResources({ isAdmin: false })

  const merged = useMemo<ResourceRow[]>(() => {
    return (resources as ResourceRow[]).map((r) => {
      const del = deletingMap?.get(r.vmid)
      return del ? { ...r, _deleting: deletionToMeta(del) } : r
    })
  }, [resources, deletingMap])

  const handleRowClick = useCallback(
    (row: ResourceRow) => {
      if (row._deleting) return
      navigate({
        to: "/my-resources/$vmid",
        params: { vmid: row.vmid.toString() },
      })
    },
    [navigate],
  )

  const columns = useMemo(
    () => createColumns(t, onOpenConsole, { enableSelection: true }),
    [t, onOpenConsole],
  )

  if (merged.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <Monitor className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">
          {t("resources:page.noResources")}
        </h3>
        <p className="text-muted-foreground">
          {t("resources:page.noResourcesDescription")}
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
        row._deleting
          ? `deleting:${row._deleting.request_id}`
          : String(row.vmid)
      }
    />
  )
}

function MyResourcesTable({
  onOpenConsole,
  rowSelection,
  onRowSelectionChange,
}: {
  onOpenConsole: (vmid: number, name: string, type: string) => void
  rowSelection: RowSelectionState
  onRowSelectionChange: (selection: RowSelectionState) => void
}) {
  return (
    <Suspense fallback={<PendingItems />}>
      <MyResourcesTableContent
        onOpenConsole={onOpenConsole}
        rowSelection={rowSelection}
        onRowSelectionChange={onRowSelectionChange}
      />
    </Suspense>
  )
}

function RefreshButton() {
  const { t } = useTranslation(["resources"])
  const queryClient = useQueryClient()

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.resources.my })
  }

  return (
    <Button variant="outline" onClick={handleRefresh}>
      <RefreshCw className="mr-2 h-4 w-4" />
      {t("resources:page.refresh")}
    </Button>
  )
}

function DownloadDesktopClientButton() {
  const [isDownloading, setIsDownloading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const getDownloadFilename = (resp: Response) => {
    const disposition = resp.headers.get("content-disposition")
    const utf8Filename = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
    if (utf8Filename) return decodeURIComponent(utf8Filename)

    const plainFilename = disposition?.match(/filename="?([^";]+)"?/i)?.[1]
    return plainFilename || "campus-cloud-connect.exe"
  }

  const handleDownload = async () => {
    setIsDownloading(true)
    setErrorMessage(null)

    try {
      const resolvedToken =
        typeof OpenAPI.TOKEN === "function"
          ? await (
              OpenAPI.TOKEN as (options: {
                method: string
                url: string
              }) => Promise<string>
            )({
              method: "GET",
              url: "/api/v1/desktop-client/download",
            })
          : (OpenAPI.TOKEN as string | undefined)
      const token = resolvedToken || localStorage.getItem("access_token")
      if (!token) {
        throw new Error("尚未登入，請重新登入後再試。")
      }
      const resp = await fetch(
        `${OpenAPI.BASE}/api/v1/desktop-client/download`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!resp.ok) {
        throw new Error(`下載失敗（${resp.status} ${resp.statusText}）`)
      }
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = getDownloadFilename(resp)
      a.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "下載失敗，請稍後再試。",
      )
    } finally {
      setIsDownloading(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="outline"
        onClick={handleDownload}
        disabled={isDownloading}
      >
        <Download className="mr-2 h-4 w-4" />
        {isDownloading ? "下載中..." : "下載連線工具"}
      </Button>
      {errorMessage ? (
        <p className="text-sm text-destructive">{errorMessage}</p>
      ) : null}
    </div>
  )
}

function MyResources() {
  const { t } = useTranslation(["resources"])
  useDeletingResourcesLiveSync()
  const [vncConsoleOpen, setVncConsoleOpen] = useState(false)
  const [terminalConsoleOpen, setTerminalConsoleOpen] = useState(false)
  const [selectedVM, setSelectedVM] = useState<{
    vmid: number
    name: string
    type: string
  } | null>(null)
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})

  const selectedVmids = useMemo(
    () =>
      Object.keys(rowSelection)
        .filter((k) => rowSelection[k])
        .map(Number),
    [rowSelection],
  )

  const handleOpenConsole = (vmid: number, name: string, type: string) => {
    setSelectedVM({ vmid, name, type })
    const isLxc = type === "lxc"
    setTerminalConsoleOpen(isLxc)
    setVncConsoleOpen(!isLxc)
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {t("resources:page.myResourcesTitle")}
          </h1>
          <p className="text-muted-foreground">
            {t("resources:page.myResourcesDescription")}
          </p>
        </div>
        <div className="flex gap-2">
          <DownloadDesktopClientButton />
          <RefreshButton />
        </div>
      </div>
      <BatchActionBar
        selectedVmids={selectedVmids}
        onClearSelection={() => setRowSelection({})}
      />
      <MyResourcesTable
        onOpenConsole={handleOpenConsole}
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
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
    </div>
  )
}
