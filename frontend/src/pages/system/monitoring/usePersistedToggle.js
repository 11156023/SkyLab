import { useCallback, useState } from "react";

/** 讀本機記住的開合狀態；沒記過或 localStorage 不可用都當收起 */
export function loadPersistedOpen(storageKey) {
  try {
    return window.localStorage.getItem(storageKey) === "1";
  } catch {
    return false;
  }
}

export function savePersistedOpen(storageKey, open) {
  try {
    window.localStorage.setItem(storageKey, open ? "1" : "0");
  } catch {
    // localStorage 不可用時偏好僅本次瀏覽生效
  }
}

/**
 * 卡片收合偏好記在本機（"1"／"0"），重整後維持使用者的選擇。
 * 監控頁的節點用量卡與系統健康卡共用；回傳 [open, toggle]。
 */
export default function usePersistedToggle(storageKey) {
  const [open, setOpen] = useState(() => loadPersistedOpen(storageKey));
  const toggle = useCallback(() => {
    setOpen((value) => {
      savePersistedOpen(storageKey, !value);
      return !value;
    });
  }, [storageKey]);
  return [open, toggle];
}
