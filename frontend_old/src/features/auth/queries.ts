import { queryOptions } from "@tanstack/react-query"

import { UsersService } from "@/client"
import { queryKeys } from "@/lib/queryKeys"

export function currentUserQueryOptions() {
  return queryOptions({
    queryKey: queryKeys.auth.currentUser,
    queryFn: UsersService.readUserMe,
    staleTime: 60_000,
  })
}
