import type { ColumnDef } from "@tanstack/react-table"
import type { TFunction } from "i18next"

import type { UserPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { UserActionsMenu } from "./UserActionsMenu"

export type UserTableData = UserPublic & {
  isCurrentUser: boolean
}

export const createColumns = (
  t: TFunction<string, string>,
): ColumnDef<UserTableData>[] => [
  {
    accessorKey: "full_name",
    header: t("settings:table.fullName"),
    cell: ({ row }) => {
      const fullName = row.original.full_name
      return (
        <div className="flex items-center gap-2">
          <span
            className={cn("font-medium", !fullName && "text-muted-foreground")}
          >
            {fullName || "N/A"}
          </span>
          {row.original.isCurrentUser && (
            <Badge variant="outline" className="text-xs">
              {t("settings:table.you")}
            </Badge>
          )}
        </div>
      )
    },
  },
  {
    accessorKey: "email",
    header: t("settings:table.email"),
    cell: ({ row }) => (
      <span className="text-muted-foreground">{row.original.email}</span>
    ),
  },
  {
    accessorKey: "role",
    header: t("settings:table.role"),
    cell: ({ row }) => {
      const role = row.original.role
      const label =
        role === "admin"
          ? t("settings:table.superuser")
          : role === "teacher"
            ? "Teacher"
            : t("settings:table.user")
      return (
        <Badge variant={role === "admin" ? "default" : "secondary"}>
          {label}
        </Badge>
      )
    },
  },
  {
    accessorKey: "is_active",
    header: t("settings:table.status"),
    cell: ({ row }) => (
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-2 rounded-full",
            row.original.is_active ? "bg-green-500" : "bg-gray-400",
          )}
        />
        <span className={row.original.is_active ? "" : "text-muted-foreground"}>
          {row.original.is_active
            ? t("settings:table.active")
            : t("settings:table.inactive")}
        </span>
      </div>
    ),
  },
  {
    id: "actions",
    header: () => (
      <span className="sr-only">{t("settings:table.actions")}</span>
    ),
    cell: ({ row }) => (
      <div className="flex justify-end">
        <UserActionsMenu user={row.original} />
      </div>
    ),
  },
]

const noopT = ((key: string) => key) as TFunction

export const columns: ColumnDef<UserTableData>[] = createColumns(noopT)
