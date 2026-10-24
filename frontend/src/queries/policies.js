import { useQuery } from "@tanstack/react-query";

import { PoliciesService } from "../services/policies";

export const policyKeys = Object.freeze({
  all: ["policies"],
  usernameRules: ["policies", "usernameRules"],
});

/**
 * Linux 登入帳號命名規則（regex、長度、保留字…）。規則來自後端 yaml，部署後幾乎不變，
 * 所以抓到就留著、不輪詢也不因切回分頁重抓；`enabled` 讓表單只在需要檢查帳號時才抓。
 */
export function useUsernameRulesQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: policyKeys.usernameRules,
    queryFn: ({ signal }) => PoliciesService.getUsernameRules({ signal }),
    enabled,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}
