import { createFileRoute } from "@tanstack/react-router"
import { z } from "zod"

import { ApplicationRequestPage } from "@/components/Applications/ApplicationRequestPage"
import { requireApplicationUser } from "@/features/auth/guards"

const quickStartSearchSchema = z.object({
  quickStartMode: z.enum(["template", "ai"]).optional(),
  quickStartPreset: z.enum(["postgres-vm", "python-vm"]).optional(),
})

export const Route = createFileRoute("/_layout/applications-create")({
  component: ApplicationsCreateRoute,
  validateSearch: quickStartSearchSchema,
  beforeLoad: () => requireApplicationUser({ redirectTo: "/applications" }),
  head: () => ({
    meta: [
      {
        title: "Request Resource - SkyLab",
      },
    ],
  }),
})

function ApplicationsCreateRoute() {
  const search = Route.useSearch()

  return (
    <ApplicationRequestPage
      quickStartMode={search.quickStartMode}
      quickStartPreset={search.quickStartPreset}
    />
  )
}
