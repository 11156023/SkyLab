import { createFileRoute, redirect } from "@tanstack/react-router"
import { AppLayout } from "@/components/Layout/AppLayout"
import { isLoggedIn } from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout")({
  component: AppLayout,
  beforeLoad: async () => {
    if (!isLoggedIn()) {
      throw redirect({
        to: "/login",
      })
    }
  },
})
