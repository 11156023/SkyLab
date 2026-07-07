import { createFileRoute } from "@tanstack/react-router"
import { z } from "zod"

import { ResourceCreatePage } from "@/components/Resources/ResourceCreatePage"
import { requireAdminUser } from "@/features/auth/guards"
import { QUICK_START_TEMPLATE_SLUGS } from "@/lib/templateQuickStart"

const resourcesCreateSearchSchema = z.object({
  quickStartTemplate: z.enum(QUICK_START_TEMPLATE_SLUGS).optional(),
})

export const Route = createFileRoute("/_layout/resources-create")({
  component: ResourcesCreateRoute,
  beforeLoad: () => requireAdminUser(),
  validateSearch: resourcesCreateSearchSchema,
  head: () => ({
    meta: [
      {
        title: "Create Resource - SkyLab",
      },
    ],
  }),
})

function ResourcesCreateRoute() {
  const search = Route.useSearch()

  return <ResourceCreatePage quickStartTemplate={search.quickStartTemplate} />
}
