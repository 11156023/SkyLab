import { apiGet, apiPost } from "./api";

/**
 * 規格調整申請流程：
 *   學生送出 → 管理員審核（核准不套用） → 學生按「套用」（202 背景任務：
 *   執行中的 VM 會關機 → 改規格 → 開機；容器線上生效） → applied_at 寫入。
 *
 * 回應中的 apply_status（僅 status=approved 時有值）：
 *   ready | applying | applied | failed | interrupted
 */
export const SpecChangeRequestsService = {
  /** 送出規格變更申請（body: { vmid, change_type, reason, requested_cpu?, requested_memory?, requested_disk?, requested_expiry_date? }） */
  create(body) {
    return apiPost("/api/v1/spec-change-requests/", body);
  },

  /** 自己的申請（含已核准待套用、已套用、已取消） */
  listMy(params = {}) {
    const query = new URLSearchParams();
    query.set("limit", String(params.limit ?? 100));
    if (params.skip) query.set("skip", String(params.skip));
    return apiGet(`/api/v1/spec-change-requests/my?${query.toString()}`);
  },

  listAll(params = {}) {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.vmid) query.set("vmid", String(params.vmid));
    query.set("limit", String(params.limit ?? 100));
    if (params.skip) query.set("skip", String(params.skip));
    const qs = query.toString();
    return apiGet(`/api/v1/spec-change-requests/${qs ? `?${qs}` : ""}`);
  },

  review(requestId, body) {
    return apiPost(`/api/v1/spec-change-requests/${requestId}/review`, body);
  },

  /** 套用已核准的規格（202；回 { message, task_id, request }） */
  apply(requestId) {
    return apiPost(`/api/v1/spec-change-requests/${requestId}/apply`, {});
  },

  /** 撤銷待審核、或已核准但尚未套用的申請 */
  cancel(requestId) {
    return apiPost(`/api/v1/spec-change-requests/${requestId}/cancel`, {});
  },
};

/* ── 顯示用 helper（RequestsPage / SpecificationsTab 共用；文案 key 在 personal 命名空間） ── */

/** 申請是否還在流程中（送出後到套用完成或結案前） */
export function isOpenSpecRequest(req) {
  if (!req) return false;
  if (req.status === "pending") return true;
  return req.status === "approved" && req.apply_status !== "applied";
}

/** 可以按「套用」：已核准且沒在跑（失敗／中斷可重試） */
export function canApplySpecRequest(req) {
  return (
    req?.status === "approved" &&
    ["ready", "failed", "interrupted"].includes(req.apply_status)
  );
}

/** 可以撤銷：待審核，或已核准但還沒開始套用／套用失敗 */
export function canCancelSpecRequest(req) {
  if (!req) return false;
  if (req.status === "pending") return true;
  return canApplySpecRequest(req);
}

/**
 * 狀態徽章：key + color（success / danger / info / warning / muted）+ labelKey。
 * 呼叫端用 personal 命名空間的 t(labelKey) 取得文字。
 */
export function specRequestDisplayStatus(req) {
  switch (req?.status) {
    case "pending":
      return { key: "pending", color: "info", labelKey: "SpecRequest.statusPending" };
    case "rejected":
      return { key: "rejected", color: "danger", labelKey: "SpecRequest.statusRejected" };
    case "cancelled":
      return { key: "cancelled", color: "muted", labelKey: "SpecRequest.statusCancelled" };
    case "approved":
      switch (req.apply_status) {
        case "applied":
          return { key: "applied", color: "success", labelKey: "SpecRequest.statusApplied" };
        case "applying":
          return { key: "applying", color: "info", labelKey: "SpecRequest.statusApplying" };
        case "failed":
          return { key: "apply_failed", color: "danger", labelKey: "SpecRequest.statusApplyFailed" };
        case "interrupted":
          return { key: "apply_interrupted", color: "danger", labelKey: "SpecRequest.statusApplyInterrupted" };
        default:
          return { key: "ready", color: "warning", labelKey: "SpecRequest.statusReady" };
      }
    default:
      return { key: req?.status ?? "unknown", color: "muted", labelKey: null };
  }
}

function memLabel(mb, t) {
  if (mb == null) return "—";
  const value = mb % 1024 === 0 ? mb / 1024 : (mb / 1024).toFixed(1);
  return t("SpecRequest.memUnit", { value });
}

/** 「CPU 2 → 4 核 / 記憶體 2 GB → 4 GB」；t 為 personal 命名空間的翻譯函式 */
export function specRequestChangeLabel(req, t) {
  const parts = [];
  if (req?.requested_cpu != null) {
    parts.push(t("SpecRequest.changeCpu", { from: req.current_cpu ?? "—", to: req.requested_cpu }));
  }
  if (req?.requested_memory != null) {
    parts.push(
      t("SpecRequest.changeMemory", {
        from: memLabel(req.current_memory, t),
        to: memLabel(req.requested_memory, t),
      }),
    );
  }
  if (req?.requested_disk != null) {
    parts.push(t("SpecRequest.changeDisk", { from: req.current_disk ?? "—", to: req.requested_disk }));
  }
  if (req?.requested_expiry_date != null) {
    parts.push(
      t("SpecRequest.changeExpiry", {
        from: req.current_expiry_date ?? t("SpecRequest.expiryUnlimited"),
        to: req.requested_expiry_date,
      }),
    );
  }
  return parts.join(" / ") || "—";
}

/** 延長到期日的申請（核准即生效，不需要再按「套用」） */
export function isExpirySpecRequest(req) {
  return req?.change_type === "expiry";
}
