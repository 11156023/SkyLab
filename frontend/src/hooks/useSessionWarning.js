/**
 * useSessionWarning.js
 * 輪詢使用者自己「執行中」的 VM 的練習階段狀態，回傳第一個
 * 後端回報 should_warn=true 的 SessionStatus，供 layout 顯示共用警告對話框。
 *
 * - 每 30 秒輪詢一次 /resources/my/session-status（一次取回全部，不再逐台打）
 * - dismiss（稍後再說）只記在記憶體，重新整理會再提醒；
 *   should_warn 變回 false 時自動清除，讓下一次警告能再出現
 * - dismissPermanent（不再顯示）以 auto_stop_at / expiry_at 為 key 存 localStorage，
 *   條件變更（例如延長後 auto_stop_at 更新）時該筆記錄自動失效
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ResourcesService } from "../services/resources";

const POLL_INTERVAL_MS = 30_000;
const LS_KEY = "session_warning_dismissed";

function loadDismissed() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function saveDismissed(store) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(store));
  } catch {
    // localStorage 不可用時僅本次瀏覽生效
  }
}

function warningKey(status) {
  return status.auto_stop_at ?? status.expiry_at ?? "";
}

export default function useSessionWarning() {
  const [statuses, setStatuses] = useState([]);
  // 記憶體內的「稍後再說」（重新整理即清除）
  const [dismissed, setDismissed] = useState(() => new Set());
  // localStorage 的「不再顯示」：vmid → warning key
  const [permanent, setPermanent] = useState(loadDismissed);
  const signatureRef = useRef("");

  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      try {
        // 後端一次回本人所有執行中機器（只含自己擁有的，不含班級學生機與分享機）
        const results = (await ResourcesService.mySessionStatuses()) ?? [];
        if (cancelled) return;
        // 內容沒變就不更新：這個 hook 掛在 layout，換新陣列會讓整個版面重繪
        const signature = JSON.stringify(results);
        if (signature === signatureRef.current) return;
        signatureRef.current = signature;
        setStatuses(results);
      } catch {
        // 靜默失敗，下一輪再試
      }
    };

    tick();
    const timer = setInterval(() => {
      if (!document.hidden) tick();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // should_warn 變回 false 時清掉記憶體 dismiss，讓下一次警告能再出現
  useEffect(() => {
    setDismissed((prev) => {
      if (prev.size === 0) return prev;
      const next = new Set(prev);
      for (const vmid of prev) {
        const s = statuses.find((x) => x.vmid === vmid);
        if (s && !s.should_warn) next.delete(vmid);
      }
      return next.size === prev.size ? prev : next;
    });
  }, [statuses]);

  // warning key 變更（例如延長後 auto_stop_at 更新）時清除過期的永久 dismiss
  useEffect(() => {
    setPermanent((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const s of statuses) {
        if (s.vmid in next && next[s.vmid] !== warningKey(s)) {
          delete next[s.vmid];
          changed = true;
        }
      }
      if (changed) saveDismissed(next);
      return changed ? next : prev;
    });
  }, [statuses]);

  const active =
    statuses.find((s) => {
      if (!s.should_warn) return false;
      if (dismissed.has(s.vmid)) return false;
      if (permanent[s.vmid] === warningKey(s)) return false;
      return true;
    }) ?? null;

  const dismiss = useCallback(() => {
    if (active) setDismissed((prev) => new Set(prev).add(active.vmid));
  }, [active]);

  const dismissPermanent = useCallback(() => {
    if (!active) return;
    const key = warningKey(active);
    setPermanent((prev) => {
      const next = { ...prev, [active.vmid]: key };
      saveDismissed(next);
      return next;
    });
    setDismissed((prev) => new Set(prev).add(active.vmid));
  }, [active]);

  return { active, dismiss, dismissPermanent };
}
