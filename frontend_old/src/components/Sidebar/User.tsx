import { Link as RouterLink } from "@tanstack/react-router"
import { ChevronsUpDown, LogOut, Settings } from "lucide-react"

import type { UserPublic } from "@/client"
import UserAvatar from "@/components/Common/UserAvatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"

interface UserInfoProps {
  avatarUrl?: string | null
  fullName?: string | null
  email?: string | null
}

function UserInfo({ avatarUrl, fullName, email }: UserInfoProps) {
  const displayName = fullName || email || "User"

  return (
    <div className="flex items-center gap-2.5 w-full min-w-0">
      <UserAvatar
        avatarUrl={avatarUrl}
        className="size-8"
        email={email}
        fullName={fullName}
      />
      <div className="flex flex-col items-start min-w-0">
        <p className="text-sm font-medium truncate w-full sidebar-user-text">
          {displayName}
        </p>
        <p className="text-xs truncate w-full sidebar-user-text">{email}</p>
      </div>
    </div>
  )
}

export function User({ user }: { user: UserPublic | null | undefined }) {
  const { logout } = useAuth()
  const { isMobile, setOpenMobile } = useSidebar()

  if (!user) return null

  const handleMenuClick = () => {
    if (isMobile) {
      setOpenMobile(false)
    }
  }
  const handleLogout = async () => {
    logout()
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground [&:hover_.sidebar-user-text]:text-white!"
              data-testid="user-menu"
            >
              <UserInfo
                avatarUrl={user?.avatar_url}
                fullName={user?.full_name}
                email={user?.email}
              />
              <ChevronsUpDown className="ml-auto size-4 sidebar-user-text" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg"
            side="top"
            align="end"
            sideOffset={4}
          >
            <DropdownMenuLabel className="p-0 font-normal">
              <UserInfo
                avatarUrl={user?.avatar_url}
                fullName={user?.full_name}
                email={user?.email}
              />
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <RouterLink to="/settings" onClick={handleMenuClick}>
              <DropdownMenuItem>
                <Settings />
                User Settings
              </DropdownMenuItem>
            </RouterLink>
            <DropdownMenuItem onClick={handleLogout}>
              <LogOut />
              Log Out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
