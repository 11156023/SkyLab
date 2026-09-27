/**
 * useSessionWarning.js
 * 輪詢使用者自己「執行中」的 VM 的練習階段狀態，回傳第一個
 * 後端回報 should_warn=true 的 SessionStatus，供 layout 顯示共用警告對話框。
 *
 * - 每 30 秒輪詢一次（資源列表較貴，每 4 輪抓一次）
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

/**
 * /resources/my 也會列出老師所帶班級的學生機器（class_teacher）與別人分享的機器
 * （shared）；那些不是使用者自己在跑的，不該輪詢也不該跳「你的機器快關了」。
 * 後端對自己的機器 access_role 預設為 owner。
 */
const OWN_ACCESS_ROLES = new Set(["owner", "class_member"]);

function isOwnMachine(resource) {
  return OWN_ACCESS_ROLES.has(resource.access_role ?? "owner");
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
  const vmidsRef = useRef([]);

  useEffect(() => {
    let cancelled = false;
    let round = 0;

    const tick = async () => {
      try {
        // 資源列表每 4 輪刷新一次，其餘輪次沿用上次的 running vmid
        if (round % 4 === 0) {
          const resources = await ResourcesService.list();
          vmidsRef.current = (resources ?? [])
            .filter((r) => r.status === "running" && r.vmid != null && isOwnMachine(r))
            .map((r) => r.vmid);
        }
        round += 1;
        const results = await Promise.all(
          vmidsRef.current.map((vmid) =>
            ResourcesService.sessionStatus(vmid).catch(() => null),
          ),
        );
        if (!cancelled) setStatuses(results.filter(Boolean));
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
