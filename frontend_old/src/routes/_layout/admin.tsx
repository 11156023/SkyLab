import { createFileRoute, Outlet } from "@tanstack/react-router"
import { requireAdminUser } from "@/features/auth/guards"

export const Route = createFileRoute("/_layout/admin")({
  component: AdminLayout,
  beforeLoad: () => requireAdminUser(),
})

function AdminLayout() {
  return <Outlet />
}
