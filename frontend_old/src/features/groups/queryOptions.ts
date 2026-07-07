import { queryOptions } from "@tanstack/react-query"

import { GroupsService } from "@/client"
import { GroupFeatureService } from "@/features/groups/api"
import { queryKeys } from "@/lib/queryKeys"

export function groupListQueryOptions() {
  return queryOptions({
    queryKey: queryKeys.groups.all,
    queryFn: () => GroupsService.listGroups(),
  })
}

export function groupDetailQueryOptions(groupId: string) {
  return queryOptions({
    queryKey: queryKeys.groups.detail(groupId),
    queryFn: () => GroupsService.getGroup({ groupId }),
    refetchInterval: 10000,
  })
}

export function batchProvisionStatusQueryOptions(jobId: string | null) {
  return queryOptions({
    queryKey: queryKeys.groups.batchJob(jobId ?? "pending"),
    queryFn: () =>
      GroupFeatureService.getBatchProvisionStatus({ jobId: jobId! }),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === "completed" || status === "failed") return false
      return 2000
    },
  })
}
