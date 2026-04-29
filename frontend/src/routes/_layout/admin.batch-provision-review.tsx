/**
 * Deep link to the batch-provision review queue. The same content lives
 * under the unified `/approvals` page (preferred entry point); this route
 * is kept for direct linking and for users who bookmark it.
 */
import { createFileRoute } from "@tanstack/react-router"

import { BatchProvisionReviewList } from "@/components/Approvals/BatchProvisionReviewList"

export const Route = createFileRoute("/_layout/admin/batch-provision-review")({
  component: BatchProvisionReviewPage,
  head: () => ({
    meta: [{ title: "批量建立審核 - Campus Cloud" }],
  }),
})

function BatchProvisionReviewPage() {
  return (
    <div className="container mx-auto py-6">
      <div className="mb-4">
        <h1 className="text-2xl font-semibold">批量建立審核</h1>
      </div>
      <BatchProvisionReviewList />
    </div>
  )
}
