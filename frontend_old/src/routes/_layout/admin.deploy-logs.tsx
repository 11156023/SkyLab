import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/_layout/admin/deploy-logs")({
  beforeLoad: () => {
    throw redirect({ to: "/jobs" })
  },
})
