import { useSuspenseQuery } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"

import { AuditLogsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

interface AuditLogsTabProps {
  vmid: number
}

export default function AuditLogsTab({ vmid }: AuditLogsTabProps) {
  const { t } = useTranslation("resourceDetail")

  const { data: auditLogs } = useSuspenseQuery({
    queryKey: ["auditLogs", vmid],
    queryFn: () =>
      AuditLogsService.getResourceAuditLogs({ vmid, skip: 0, limit: 100 }),
  })

  const getActionBadgeColor = (action: string) => {
    if (action.includes("create")) return "bg-green-500"
    if (action.includes("delete")) return "bg-red-500"
    if (action.includes("update") || action.includes("spec_change"))
      return "bg-blue-500"
    if (action.includes("snapshot")) return "bg-purple-500"
    return "bg-gray-500"
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("auditLogs.title")}</CardTitle>
          <CardDescription>
            {t("auditLogs.description")} ({auditLogs.count}{" "}
            {t("auditLogs.records")})
          </CardDescription>
        </CardHeader>
        <CardContent>
          {auditLogs.data.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              {t("auditLogs.noRecords")}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("auditLogs.time")}</TableHead>
                  <TableHead>{t("auditLogs.operator")}</TableHead>
                  <TableHead>{t("auditLogs.action")}</TableHead>
                  <TableHead>{t("auditLogs.details")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {auditLogs.data.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="whitespace-nowrap">
                      {new Date(log.created_at).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      <div>
                        <div className="font-medium">
                          {log.user_full_name || "Unknown"}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {log.user_email}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge className={getActionBadgeColor(log.action)}>
                        {t(
                          `resourceDetail.auditLogs.actions.${log.action}`,
                          log.action,
                        )}
                      </Badge>
                    </TableCell>
                    <TableCell className="max-w-md truncate">
                      {log.details}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
