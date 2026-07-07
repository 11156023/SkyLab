import { Link } from "@tanstack/react-router"
import { FileSearch } from "lucide-react"
import { useTranslation } from "react-i18next"

import type { VMRequestPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

interface ReviewActionsProps {
  request: VMRequestPublic
}

export const ReviewActions = ({ request }: ReviewActionsProps) => {
  const { t } = useTranslation(["approvals"])

  if (request.status !== "pending") {
    const statusMap: Record<
      string,
      { label: string; variant: "default" | "destructive" | "outline" }
    > = {
      approved: { label: t("approvals:filters.approved"), variant: "default" },
      cancelled: {
        label: t("approvals:filters.cancelled"),
        variant: "outline",
      },
      rejected: {
        label: t("approvals:filters.rejected"),
        variant: "destructive",
      },
    }
    const s = statusMap[request.status]
    return (
      <div className="flex items-center gap-2">
        <Badge variant={s?.variant || "outline"}>
          {s?.label || request.status}
        </Badge>
        {request.vmid && (
          <span className="text-xs text-muted-foreground">
            VMID: {request.vmid}
          </span>
        )}
        {request.assigned_node && (
          <span className="text-xs text-muted-foreground">
            Node: {request.assigned_node}
          </span>
        )}
      </div>
    )
  }

  return (
    <Button size="sm" asChild>
      <Link to="/approvals/$requestId" params={{ requestId: request.id }}>
        <FileSearch className="mr-1 h-4 w-4" />
        Review
      </Link>
    </Button>
  )
}
