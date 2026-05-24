import { createFileRoute } from "@tanstack/react-router"
import { Suspense } from "react"

import VMRequestReviewPage from "@/components/Applications/VMRequestReviewPage"
import PendingItems from "@/components/Pending/PendingItems"
import { requireAdminUser } from "@/features/auth/guards"

export const Route = createFileRoute("/_layout/approvals_/$requestId")({
  component: ApprovalReviewRoute,
  beforeLoad: () => requireAdminUser({ redirectTo: "/applications" }),
  head: () => ({
    meta: [
      {
        title: "Request Review - SkyLab",
      },
    ],
  }),
})

function ApprovalReviewRoute() {
  const { requestId } = Route.useParams()

  return (
    <Suspense fallback={<PendingItems />}>
      <VMRequestReviewPage requestId={requestId} />
    </Suspense>
  )
}
