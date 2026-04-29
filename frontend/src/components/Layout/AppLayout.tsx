import { useQuery } from "@tanstack/react-query"
import { Link, Outlet, useRouterState } from "@tanstack/react-router"
import { AlertTriangle } from "lucide-react"

import { Footer } from "@/components/Common/Footer"
import { ErrorBoundary } from "@/components/ErrorBoundary"
import { JobsBanner } from "@/components/Jobs/JobsBanner"
import { SessionWarningDialog } from "@/components/Resources/SessionWarningDialog"
import AppSidebar from "@/components/Sidebar/AppSidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { useSessionWarning } from "@/hooks/useSessionWarning"
import { cn } from "@/lib/utils"
import { IpManagementApiService } from "@/services/ipManagement"

// 這些路由會填滿整個內容區域，不套用 padding / max-width / footer
const FULLSCREEN_ROUTES = ["/firewall"]

export function AppLayout() {
  const { location } = useRouterState()
  const { user } = useAuth()
  const isFullscreen = FULLSCREEN_ROUTES.includes(location.pathname)
  const hasFixedFooter =
    location.pathname === "/applications-create" ||
    location.pathname === "/resources-create"
  const isAdmin = user?.role === "admin" || user?.is_superuser
  const { active: warning, dismiss } = useSessionWarning()

  return (
    <div className="app-layout min-h-svh w-full">
      <SessionWarningDialog
        status={warning}
        open={Boolean(warning)}
        onClose={dismiss}
      />
      <SidebarProvider defaultOpen={false}>
        <AppSidebar />
        <SidebarInset className="min-w-0 overflow-x-hidden">
          <JobsBanner />
          <SubnetBanner isAdmin={isAdmin} />
          {isFullscreen ? (
            <main className="flex-1 min-w-0 overflow-hidden">
              <ErrorBoundary>
                <Outlet />
              </ErrorBoundary>
            </main>
          ) : (
            <>
              <main
                className={cn(
                  "flex-1 min-w-0 overflow-x-hidden py-7.5 px-20",
                  hasFixedFooter && "pb-28 md:pb-32",
                )}
              >
                <div className="w-full min-w-0 max-w-full">
                  <ErrorBoundary>
                    <Outlet />
                  </ErrorBoundary>
                </div>
              </main>
              <Footer
                data-app-footer={hasFixedFooter ? "fixed" : undefined}
                className={
                  hasFixedFooter
                    ? "sticky bottom-0 z-20 mt-auto bg-background/95 backdrop-blur supports-backdrop-filter:bg-background/80"
                    : undefined
                }
              />
            </>
          )}
        </SidebarInset>
      </SidebarProvider>
    </div>
  )
}

// ─── Subnet Not Configured Banner ─────────────────────────────────────────────

function SubnetBanner({ isAdmin }: { isAdmin?: boolean }) {
  const { user } = useAuth()
  const { data: status } = useQuery({
    queryKey: ["ip-management", "status"],
    queryFn: () => IpManagementApiService.getSubnetStatus(),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    enabled: !!user,
  })

  if (!status || status.configured) return null

  return (
    <div className="flex items-center gap-3 border-b border-yellow-600/40 bg-yellow-50 px-6 py-2.5 dark:border-yellow-700/50 dark:bg-yellow-900/20">
      <AlertTriangle className="h-4 w-4 shrink-0 text-yellow-600 dark:text-yellow-400" />
      <span className="text-sm text-yellow-800 dark:text-yellow-300">
        子網尚未配置，VM/LXC 建立功能已停用。
        {isAdmin && (
          <Link
            to="/admin/ip-management"
            className="ml-1 underline hover:text-yellow-700 dark:hover:text-yellow-200"
          >
            前往設定
          </Link>
        )}
      </span>
    </div>
  )
}
