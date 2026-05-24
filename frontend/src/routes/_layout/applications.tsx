import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { FileText } from "lucide-react"
import { Suspense, useMemo } from "react"
import { useTranslation } from "react-i18next"

import type { ApiError, VMRequestPublic } from "@/client"
import CreateVMRequest from "@/components/Applications/CreateVMRequest"
import { createMyRequestColumns } from "@/components/Applications/columns"
import { DataTable } from "@/components/Common/DataTable"
import PendingItems from "@/components/Pending/PendingItems"
import { myVmRequestsQueryOptions } from "@/features/applications/queryOptions"
import { requireApplicationUser } from "@/features/auth/guards"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import { VmRequestsApi } from "@/services/vmRequests"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/applications")({
  component: Applications,
  beforeLoad: () => requireApplicationUser(),
  head: () => ({
    meta: [
      {
        title: "Applications - SkyLab",
      },
    ],
  }),
})

function RequestsTableContent() {
  const { t } = useTranslation(["applications"])
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { data } = useSuspenseQuery(myVmRequestsQueryOptions())
  const cancelMutation = useMutation({
    mutationFn: (requestId: string) => VmRequestsApi.cancel({ requestId }),
    onSuccess: () => {
      showSuccessToast(t("applications:actions.cancelSuccess"))
      queryClient.invalidateQueries({ queryKey: queryKeys.vmRequests.all })
      queryClient.invalidateQueries({
        queryKey: queryKeys.vmRequests.pendingCount,
      })
      queryClient.invalidateQueries({ queryKey: queryKeys.vmRequests.admin })
    },
    onError: (err) => handleError.call(showErrorToast, err as ApiError),
  })

  const retryMutation = useMutation({
    mutationFn: (requestId: string) => VmRequestsApi.retry({ requestId }),
    onSuccess: () => {
      showSuccessToast(
        t("applications:actions.retrySuccess", {
          defaultValue: "已重新觸發背景部署",
        }),
      )
      queryClient.invalidateQueries({ queryKey: queryKeys.vmRequests.all })
      queryClient.invalidateQueries({ queryKey: ["pending-resources"] })
    },
    onError: (err) => handleError.call(showErrorToast, err as ApiError),
  })

  const columns = useMemo(
    () =>
      createMyRequestColumns(t, {
        cancellingRequestId: cancelMutation.variables ?? null,
        onCancelRequest: (request: VMRequestPublic) => {
          const shouldCancel = window.confirm(
            t("applications:actions.cancelConfirm", {
              hostname: request.hostname,
            }),
          )
          if (!shouldCancel) return
          cancelMutation.mutate(request.id)
        },
        retryingRequestId: retryMutation.variables ?? null,
        onRetryRequest: (request: VMRequestPublic) => {
          retryMutation.mutate(request.id)
        },
      }),
    [cancelMutation, retryMutation, t],
  )

  if (data.data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <FileText className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">
          {t("applications:page.noApplications")}
        </h3>
        <p className="text-muted-foreground">
          {t("applications:page.noApplicationsDescription")}
        </p>
      </div>
    )
  }

  return <DataTable columns={columns} data={data.data} />
}

function RequestsTable() {
  return (
    <Suspense fallback={<PendingItems />}>
      <RequestsTableContent />
    </Suspense>
  )
}

function Applications() {
  const { t } = useTranslation(["applications"])

  return (
    <div className="flex w-full min-w-0 max-w-full flex-col gap-6 overflow-hidden">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {t("applications:page.title")}
          </h1>
          <p className="text-muted-foreground">
            {t("applications:page.description")}
          </p>
        </div>
        <CreateVMRequest />
      </div>
      <RequestsTable />
    </div>
  )
}
