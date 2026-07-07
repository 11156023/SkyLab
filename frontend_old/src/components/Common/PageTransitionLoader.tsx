import { useRouterState } from "@tanstack/react-router"

export function PageTransitionLoader() {
  const isLoading = useRouterState({ select: (s) => s.status === "pending" })

  if (!isLoading) return null

  return (
    <div className="fixed top-0 left-0 right-0 z-[9999] h-1 overflow-hidden">
      <div className="h-full bg-primary animate-page-loading" />
    </div>
  )
}
