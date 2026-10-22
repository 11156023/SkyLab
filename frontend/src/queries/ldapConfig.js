import { useQuery, useQueryClient } from "@tanstack/react-query";

import { LdapConfigService } from "../services/ldapConfig";

export const ldapConfigKeys = Object.freeze({
  all: ["ldapConfig"],
  config: ["ldapConfig", "config"],
});

/** LDAP 連線設定（表單型：不輪詢、不因切回分頁重抓） */
export function useLdapConfigQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: ldapConfigKeys.config,
    queryFn: () => LdapConfigService.get(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

export function useLdapConfigCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    setConfig(config) {
      queryClient.setQueryData(ldapConfigKeys.config, config);
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: ldapConfigKeys.all });
    },
  };
}
