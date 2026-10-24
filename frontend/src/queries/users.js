import { useQuery, useQueryClient } from "@tanstack/react-query";

import { UsersService } from "../services/users";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const userKeys = Object.freeze({
  all: ["users"],
  list: ["users", "list"],
  pendingRegistrations: ["users", "pendingRegistrations"],
  registrationPolicy: ["users", "registrationPolicy"],
});

/** 管理員：全部使用者（service 自己分頁補齊） */
export function useAllUsersQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: userKeys.list,
    queryFn: () => UsersService.listAll(),
    enabled,
    refetchInterval,
  });
}

/** 管理員：待審核的註冊申請 */
export function usePendingRegistrationsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: userKeys.pendingRegistrations,
    queryFn: () => UsersService.listPendingRegistrations(),
    enabled,
    refetchInterval,
  });
}

/**
 * 管理員：註冊政策。預設不輪詢；使用者管理頁的開關是按下就存（沒有草稿），
 * 那裡傳 refetchInterval 跟著清單一起背景刷新。
 */
export function useRegistrationPolicyQuery({ enabled = true, refetchInterval = false } = {}) {
  return useQuery({
    queryKey: userKeys.registrationPolicy,
    queryFn: () => UsersService.getRegistrationPolicy(),
    enabled,
    refetchInterval,
  });
}

/**
 * 增刪改後就地更新快取（使用者清單、待審核註冊、註冊政策）。
 * 寫入前先取消還在路上的背景刷新：改動前送出的舊清單比改動後才回來時，不能把剛改好的列蓋回去
 * （cancelQueries 要等它完成再寫，取消會把 query 還原成 fetch 開始前的狀態）。
 * 首次載入還沒回來時沒有東西可改，改成重抓：舊請求作廢，由新的接手（新的才看得到這次改動）。
 * 沒有資料時 invalidate 會併進進行中的請求而不是重發，所以要先取消。
 */
export function useUserCacheUpdater() {
  const queryClient = useQueryClient();
  const patch = async (queryKey, mapper) => {
    await queryClient.cancelQueries({ queryKey });
    if (queryClient.getQueryData(queryKey) === undefined) {
      queryClient.invalidateQueries({ queryKey });
      return;
    }
    queryClient.setQueryData(queryKey, (prev) => (prev === undefined ? prev : mapper(prev)));
  };
  const patchList = (mapper) => patch(userKeys.list, (prev) => (Array.isArray(prev) ? mapper(prev) : prev));
  return {
    /** 單筆更新回來的資料就地寫進清單 */
    update(updated) {
      return patchList((list) => list.map((u) => (u.id === updated.id ? updated : u)));
    },
    /** 批量更新：只換掉有回來的那幾筆 */
    updateMany(updatedUsers) {
      const byId = new Map(updatedUsers.map((u) => [u.id, u]));
      return patchList((list) => list.map((u) => byId.get(u.id) ?? u));
    },
    /** 只改單筆的部分欄位（例如重設兩步驟驗證後 totp_enabled 變 false） */
    patchUser(userId, fields) {
      return patchList((list) => list.map((u) => (u.id === userId ? { ...u, ...fields } : u)));
    },
    /** 新建的帳號放最前面 */
    prepend(created) {
      return patchList((list) => [created, ...list]);
    },
    remove(userId) {
      return patchList((list) => list.filter((u) => u.id !== userId));
    },
    removeMany(userIds) {
      const ids = new Set(userIds);
      return patchList((list) => list.filter((u) => !ids.has(u.id)));
    },
    /** 審核完的註冊申請從待審清單拿掉（回應格式是 { data: [...] }） */
    removePendingRegistration(userId) {
      return patch(userKeys.pendingRegistrations, (prev) => (
        Array.isArray(prev?.data) ? { ...prev, data: prev.data.filter((u) => u.id !== userId) } : prev
      ));
    },
    /** 註冊政策存檔後直接換成回應 */
    async setRegistrationPolicy(policy) {
      await queryClient.cancelQueries({ queryKey: userKeys.registrationPolicy });
      queryClient.setQueryData(userKeys.registrationPolicy, policy);
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: userKeys.all });
    },
  };
}
