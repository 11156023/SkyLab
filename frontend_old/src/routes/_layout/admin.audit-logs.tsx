import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useCallback, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  type AuditActionMeta,
  type AuditLogQuery,
  AuditLogsAPI,
  type AuditUserOption,
  downloadBlob,
} from "@/services/auditLogs"

export const Route = createFileRoute("/_layout/admin/audit-logs")({
  component: AdminAuditLogsPage,
})

const LIMIT = 50

const CATEGORY_BADGE: Record<string, string> = {
  auth: "bg-yellow-500",
  resource: "bg-blue-500",
  request: "bg-orange-500",
  user: "bg-indigo-500",
  firewall: "bg-rose-500",
  gateway: "bg-teal-500",
  system: "bg-slate-500",
  ai: "bg-purple-500",
  other: "bg-gray-500",
}

const DANGER_ACTIONS = new Set([
  "resource_delete",
  "resource_reset",
  "snapshot_delete",
  "snapshot_rollback",
  "user_delete",
  "group_delete",
  "firewall_rule_delete",
  "firewall_connection_delete",
  "nat_rule_delete",
  "reverse_proxy_rule_delete",
  "proxmox_config_update",
  "gateway_keypair_generate",
  "cloudflare_config_update",
  "cloudflare_dns_record_delete",
  "login_failed",
  "login_google_failed",
])

function getActionBadgeColor(action: string, category?: string) {
  if (DANGER_ACTIONS.has(action)) return "bg-red-500"
  if (category) return CATEGORY_BADGE[category] ?? "bg-gray-500"
  if (action.includes("create")) return "bg-green-500"
  if (action.includes("delete")) return "bg-red-500"
  if (action.includes("update") || action.includes("change"))
    return "bg-blue-500"
  if (action.includes("login")) return "bg-yellow-500"
  return "bg-gray-500"
}

function AdminAuditLogsPage() {
  const { t } = useTranslation("settings")
  const tR = useTranslation("resourceDetail").t

  // Filters
  const [page, setPage] = useState(0)
  const [vmidFilter, setVmidFilter] = useState("")
  const [userFilter, setUserFilter] = useState("")
  const [actionFilter, setActionFilter] = useState("")
  const [ipFilter, setIpFilter] = useState("")
  const [searchFilter, setSearchFilter] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")

  const query: AuditLogQuery = useMemo(
    () => ({
      skip: page * LIMIT,
      limit: LIMIT,
      vmid: vmidFilter ? Number.parseInt(vmidFilter, 10) : null,
      user_id: userFilter || null,
      action: actionFilter || null,
      ip_address: ipFilter || null,
      search: searchFilter || null,
      start_time: startDate ? new Date(startDate).toISOString() : null,
      end_time: endDate ? new Date(`${endDate}T23:59:59`).toISOString() : null,
    }),
    [
      page,
      vmidFilter,
      userFilter,
      actionFilter,
      ipFilter,
      searchFilter,
      startDate,
      endDate,
    ],
  )

  // Data queries
  const { data: auditLogs, refetch } = useQuery({
    queryKey: ["admin-audit-logs", query],
    queryFn: () => AuditLogsAPI.listAll(query),
  })

  const { data: stats } = useQuery({
    queryKey: ["admin-audit-stats", startDate, endDate],
    queryFn: () =>
      AuditLogsAPI.getStats({
        start_time: startDate ? new Date(startDate).toISOString() : null,
        end_time: endDate
          ? new Date(`${endDate}T23:59:59`).toISOString()
          : null,
      }),
  })

  const { data: actionMetas } = useQuery({
    queryKey: ["admin-audit-actions"],
    queryFn: () => AuditLogsAPI.listActions(),
    staleTime: 1000 * 60 * 30,
  })

  const { data: userOptions } = useQuery({
    queryKey: ["admin-audit-users"],
    queryFn: () => AuditLogsAPI.listUsers(),
    staleTime: 1000 * 60 * 5,
  })

  // Build a map: action value -> category
  const actionCategoryMap = useMemo(() => {
    const map: Record<string, string> = {}
    if (actionMetas) {
      for (const m of actionMetas) {
        map[m.value] = m.category
      }
    }
    return map
  }, [actionMetas])

  // Group actions by category for the select
  const actionsByCategory = useMemo(() => {
    const groups: Record<string, AuditActionMeta[]> = {}
    if (actionMetas) {
      for (const m of actionMetas) {
        const cat = m.category
        if (!groups[cat]) groups[cat] = []
        groups[cat].push(m)
      }
    }
    return groups
  }, [actionMetas])

  const totalPages = auditLogs ? Math.ceil(auditLogs.count / LIMIT) : 0

  const resetFilters = useCallback(() => {
    setPage(0)
    setVmidFilter("")
    setUserFilter("")
    setActionFilter("")
    setIpFilter("")
    setSearchFilter("")
    setStartDate("")
    setEndDate("")
  }, [])

  const handleExport = useCallback(async () => {
    const blob = await AuditLogsAPI.exportCsv(query)
    const now = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")
    downloadBlob(blob, `audit-logs-${now}.csv`)
  }, [query])

  const formatUserLabel = (u: AuditUserOption) =>
    u.full_name ? `${u.full_name} (${u.email})` : u.email

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {t("admin.auditLogs.title")}
          </h1>
          <p className="text-muted-foreground">
            {t("admin.auditLogs.description")}
          </p>
        </div>
        <Button variant="outline" onClick={handleExport}>
          {t("admin.auditLogs.export")}
        </Button>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>
                {t("admin.auditLogs.statsTotal")}
              </CardDescription>
              <CardTitle className="text-3xl">{stats.total}</CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>
                {t("admin.auditLogs.statsDanger")}
              </CardDescription>
              <CardTitle className="text-3xl text-red-500">
                {stats.danger}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>
                {t("admin.auditLogs.statsLoginFailed")}
              </CardDescription>
              <CardTitle className="text-3xl text-yellow-500">
                {stats.login_failed}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>
                {t("admin.auditLogs.statsActiveUsers")}
              </CardDescription>
              <CardTitle className="text-3xl text-green-500">
                {stats.active_users}
              </CardTitle>
            </CardHeader>
          </Card>
        </div>
      )}

      {/* Filters */}
      <Card>
        <CardContent className="pt-6">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {/* Search */}
            <Input
              placeholder={t("admin.auditLogs.searchPlaceholder")}
              value={searchFilter}
              onChange={(e) => {
                setSearchFilter(e.target.value)
                setPage(0)
              }}
            />
            {/* VMID */}
            <Input
              placeholder={t("admin.auditLogs.filterByVMID")}
              value={vmidFilter}
              onChange={(e) => {
                setVmidFilter(e.target.value)
                setPage(0)
              }}
              type="number"
            />
            {/* IP */}
            <Input
              placeholder={t("admin.auditLogs.filterByIP")}
              value={ipFilter}
              onChange={(e) => {
                setIpFilter(e.target.value)
                setPage(0)
              }}
            />
            {/* User select */}
            <Select
              value={userFilter}
              onValueChange={(v) => {
                setUserFilter(v === "__all__" ? "" : v)
                setPage(0)
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder={t("admin.auditLogs.allUsers")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">
                  {t("admin.auditLogs.allUsers")}
                </SelectItem>
                {userOptions?.map((u) => (
                  <SelectItem key={u.id} value={u.id}>
                    {formatUserLabel(u)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {/* Action select grouped by category */}
            <Select
              value={actionFilter}
              onValueChange={(v) => {
                setActionFilter(v === "__all__" ? "" : v)
                setPage(0)
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder={t("admin.auditLogs.allActions")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">
                  {t("admin.auditLogs.allActions")}
                </SelectItem>
                {Object.entries(actionsByCategory).map(([cat, actions]) => (
                  <SelectGroup key={cat}>
                    <SelectLabel>
                      {t(`admin.auditLogs.categories.${cat}`, cat)}
                    </SelectLabel>
                    {actions.map((a) => (
                      <SelectItem key={a.value} value={a.value}>
                        {tR(`auditLogs.actions.${a.value}`, a.value)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                ))}
              </SelectContent>
            </Select>
            {/* Date range */}
            <Input
              type="date"
              value={startDate}
              onChange={(e) => {
                setStartDate(e.target.value)
                setPage(0)
              }}
              placeholder={t("admin.auditLogs.startDate")}
            />
            <Input
              type="date"
              value={endDate}
              onChange={(e) => {
                setEndDate(e.target.value)
                setPage(0)
              }}
              placeholder={t("admin.auditLogs.endDate")}
            />
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={resetFilters}
                className="flex-1"
              >
                {t("common.buttons.reset", "Reset")}
              </Button>
              <Button
                variant="outline"
                onClick={() => refetch()}
                className="flex-1"
              >
                {t("common.buttons.refresh", "Refresh")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader>
          <CardTitle>{t("admin.auditLogs.allRecords")}</CardTitle>
          <CardDescription>
            {t("admin.auditLogs.showing")} {auditLogs?.count ?? 0}{" "}
            {t("admin.auditLogs.records")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!auditLogs?.data?.length ? (
            <div className="py-8 text-center text-muted-foreground">
              {t("admin.auditLogs.noRecords")}
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[160px]">
                        {tR("auditLogs.time")}
                      </TableHead>
                      <TableHead className="w-[70px]">
                        {t("admin.auditLogs.vmid")}
                      </TableHead>
                      <TableHead>{tR("auditLogs.operator")}</TableHead>
                      <TableHead>{tR("auditLogs.action")}</TableHead>
                      <TableHead className="max-w-[300px]">
                        {tR("auditLogs.details")}
                      </TableHead>
                      <TableHead>{t("admin.auditLogs.ipAddress")}</TableHead>
                      <TableHead className="w-[60px]">
                        {t("admin.auditLogs.userAgent")}
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {auditLogs.data.map((log) => {
                      const cat = actionCategoryMap[log.action]
                      return (
                        <TableRow key={log.id}>
                          <TableCell className="whitespace-nowrap text-xs">
                            {new Date(log.created_at).toLocaleString()}
                          </TableCell>
                          <TableCell className="font-mono text-xs">
                            {log.vmid ?? "-"}
                          </TableCell>
                          <TableCell>
                            <div>
                              <div className="text-sm font-medium">
                                {log.user_full_name || "-"}
                              </div>
                              {log.user_email && (
                                <div className="text-xs text-muted-foreground">
                                  {log.user_email}
                                </div>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge
                              className={getActionBadgeColor(log.action, cat)}
                            >
                              {tR(
                                `auditLogs.actions.${log.action}`,
                                log.action,
                              )}
                            </Badge>
                          </TableCell>
                          <TableCell className="max-w-[300px] truncate text-xs">
                            {log.details}
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {log.ip_address || "-"}
                          </TableCell>
                          <TableCell>
                            {log.user_agent ? (
                              <TooltipProvider>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <span className="cursor-help text-xs text-muted-foreground">
                                      ...
                                    </span>
                                  </TooltipTrigger>
                                  <TooltipContent className="max-w-sm">
                                    <p className="break-all text-xs">
                                      {log.user_agent}
                                    </p>
                                  </TooltipContent>
                                </Tooltip>
                              </TooltipProvider>
                            ) : (
                              <span className="text-xs text-muted-foreground">
                                -
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="mt-4 flex items-center justify-between">
                  <div className="text-sm text-muted-foreground">
                    {t("admin.auditLogs.page")} {page + 1} / {totalPages}
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(Math.max(0, page - 1))}
                      disabled={page === 0}
                    >
                      {t("common.buttons.previous", "Previous")}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setPage(Math.min(totalPages - 1, page + 1))
                      }
                      disabled={page >= totalPages - 1}
                    >
                      {t("common.buttons.next", "Next")}
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
