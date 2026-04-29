import { useQuery, useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { ClipboardCheck } from "lucide-react"
import { Suspense, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { type VMRequestStatus, VmRequestsService } from "@/client"
import { createAdminRequestColumns } from "@/components/Applications/adminColumns"
import {
  BatchProvisionReviewList,
  useBatchProvisionPendingCount,
} from "@/components/Approvals/BatchProvisionReviewList"
import { DataTable } from "@/components/Common/DataTable"
import PendingItems from "@/components/Pending/PendingItems"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { adminVmRequestsQueryOptions } from "@/features/applications/queryOptions"
import { requireAdminUser } from "@/features/auth/guards"
import { queryKeys } from "@/lib/queryKeys"

export const Route = createFileRoute("/_layout/approvals")({
  component: Approvals,
  beforeLoad: () => requireAdminUser({ redirectTo: "/applications" }),
  head: () => ({
    meta: [
      {
        title: "Approvals - Campus Cloud",
      },
    ],
  }),
})

function AdminRequestsTableContent({
  status,
}: {
  status: VMRequestStatus | null
}) {
  const { t } = useTranslation(["approvals"])
  const { data } = useSuspenseQuery(adminVmRequestsQueryOptions(status))

  const columns = useMemo(() => createAdminRequestColumns(t), [t])

  if (data.data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <ClipboardCheck className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">
          {t("approvals:page.noApplications")}
        </h3>
        <p className="text-muted-foreground">
          {status === "pending"
            ? t("approvals:page.noApplicationsDescription")
            : t("approvals:page.noMatch")}
        </p>
      </div>
    )
  }

  return <DataTable columns={columns} data={data.data} />
}

function AdminRequestsTable({ status }: { status: VMRequestStatus | null }) {
  return (
    <Suspense fallback={<PendingItems />}>
      <AdminRequestsTableContent status={status} />
    </Suspense>
  )
}

function PendingCountBadge() {
  const { data } = useQuery({
    queryFn: () =>
      VmRequestsService.listAllVmRequests({
        status: "pending" as VMRequestStatus,
      }),
    queryKey: queryKeys.vmRequests.pendingCount,
  })

  const count = data?.count ?? 0
  if (count === 0) return null

  return (
    <Badge variant="outline" className="ml-1.5 text-xs">
      {count}
    </Badge>
  )
}

type TabValue = VMRequestStatus | "all" | "batch"

function BatchPendingCountBadge() {
  const count = useBatchProvisionPendingCount()
  if (count === 0) return null
  return (
    <Badge variant="outline" className="ml-1.5 text-xs">
      {count}
    </Badge>
  )
}

function Approvals() {
  const { t } = useTranslation(["approvals"])
  const [tab, setTab] = useState<TabValue>("pending")

  const statusFilter: VMRequestStatus | null =
    tab === "batch" ? null : tab === "all" ? null : (tab as VMRequestStatus)

  return (
    <div className="flex w-full min-w-0 max-w-full flex-col gap-6 overflow-hidden">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {t("approvals:page.title")}
          </h1>
          <p className="text-muted-foreground">
            {t("approvals:page.description")}
          </p>
        </div>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as TabValue)}>
        <div className="w-full overflow-x-auto">
          <TabsList className="min-w-max">
            <TabsTrigger value="pending">
              {t("approvals:filters.pending")}
              <PendingCountBadge />
            </TabsTrigger>
            <TabsTrigger value="approved">
              {t("approvals:filters.approved")}
            </TabsTrigger>
            <TabsTrigger value="rejected">
              {t("approvals:filters.rejected")}
            </TabsTrigger>
            <TabsTrigger value="all">{t("approvals:filters.all")}</TabsTrigger>
            <TabsTrigger value="batch">
              批量建立
              <BatchPendingCountBadge />
            </TabsTrigger>
          </TabsList>
        </div>
      </Tabs>

      {tab === "batch" ? (
        <BatchProvisionReviewList />
      ) : (
        <AdminRequestsTable status={statusFilter} />
      )}
    </div>
  )
}
