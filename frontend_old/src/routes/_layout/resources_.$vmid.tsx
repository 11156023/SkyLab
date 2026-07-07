import { createFileRoute } from "@tanstack/react-router"
import { Suspense } from "react"

import ResourceDetailPage from "@/components/ResourceDetail/ResourceDetailPage"

export const Route = createFileRoute("/_layout/resources_/$vmid")({
  component: ResourceDetailPageRoute,
})

function ResourceDetailPageRoute() {
  const { vmid } = Route.useParams()

  return (
    <Suspense fallback={<div>Loading...</div>}>
      <ResourceDetailPage vmid={Number.parseInt(vmid, 10)} />
    </Suspense>
  )
}
