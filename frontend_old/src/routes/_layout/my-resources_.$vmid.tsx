import { createFileRoute } from "@tanstack/react-router"
import { Suspense } from "react"

import ResourceDetailPage from "@/components/ResourceDetail/ResourceDetailPage"

export const Route = createFileRoute("/_layout/my-resources_/$vmid")({
  component: MyResourceDetailPageRoute,
})

function MyResourceDetailPageRoute() {
  const { vmid } = Route.useParams()

  return (
    <Suspense fallback={<div>Loading...</div>}>
      <ResourceDetailPage
        vmid={Number.parseInt(vmid, 10)}
        backTo="/my-resources"
      />
    </Suspense>
  )
}
