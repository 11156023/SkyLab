import { useQuery, useQueryClient } from "@tanstack/react-query";

import { UserGroupsService } from "../services/userGroups";

export const userGroupKeys = Object.freeze({
  all: ["userGroups"],
  list: ["userGroups", "list"],
  members: (groupId) => ["userGroups", "members", groupId],
});

/**
 * 管理員維護的使用者群組（例如一個班），含每組的成員數與學生數。
 * 使用者管理頁跟著使用者清單一起背景刷新；老師挑群組匯入班級時只在開對話框時抓一次。
 */
export function useUserGroupsQuery({ enabled = true, refetchInterval = false } = {}) {
  return useQuery({
    queryKey: userGroupKeys.list,
    queryFn: async () => (await UserGroupsService.list())?.data ?? [],
    enabled,
    refetchInterval,
  });
}

/** 單一群組的成員（使用者管理頁的群組篩選用）；沒選群組時不抓 */
export function useUserGroupMembersQuery(groupId) {
  return useQuery({
    queryKey: userGroupKeys.members(groupId),
    queryFn: async () => (await UserGroupsService.members(groupId))?.data ?? [],
    enabled: Boolean(groupId),
  });
}

/** 建立、改名、刪除群組或增減成員後重抓：清單上的人數與篩選中的成員都要跟著變 */
export function useUserGroupCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: userGroupKeys.all });
    },
  };
}
