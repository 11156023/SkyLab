import i18n from "@/lang";
import { ElMessage } from "element-plus";

/** 登入狀態失效：由 store 統一提示一次並清掉登入與連線（見 handleSessionExpired） */
export const NOT_LOGGED_IN = "B1001";

/**
 * 主程序錯誤碼 → 給使用者看的在地化說明。
 * 主程序的英文訊息只進記錄檔（IPC 回覆的 message 只在 DevTools 印出），不直接顯示；
 * 沒對應文案的錯誤碼一律當成未預期錯誤。
 */
export const describeError = (bizCode?: string | null): string => {
  const key = `errors.${bizCode}`;
  return i18n.global.te(key)
    ? i18n.global.t(key)
    : i18n.global.t("errors.B1000");
};

/** 操作失敗的 toast；登入失效不在這裡跳，避免同時好幾個請求失敗時連跳好幾則 */
export const toastError = (bizCode: string) => {
  if (bizCode === NOT_LOGGED_IN) return;
  ElMessage.error(describeError(bizCode));
};
