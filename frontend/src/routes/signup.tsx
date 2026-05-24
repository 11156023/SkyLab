import { createFileRoute, redirect } from "@tanstack/react-router"
import { SignUpPage } from "@/components/Auth/SignUpPage"
import { isLoggedIn } from "@/hooks/useAuth"

export const Route = createFileRoute("/signup")({
  component: SignUpPage,
  beforeLoad: async () => {
    if (isLoggedIn()) {
      throw redirect({
        to: "/",
      })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Sign Up - SkyLab",
      },
    ],
  }),
})
