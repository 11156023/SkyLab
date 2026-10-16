/* 學生週次頁：把一個檢查點的最新檢查結果換成畫面上的狀態 */

const SCRIPT_CHECK_STATUS = {
  pass: "passed",
  fail: "failed",
  warning: "partial",
  unknown: "needs_review",
  skipped: "needs_review",
};

/* 這個檢查點沒有逐項判定時的狀態：只反映「檢查有沒有跑完」，
   不能把整次執行的「完成」當成這一項通過 */
function statusWithoutVerdict(check) {
  if (check.status === "running" || check.status === "pending") return check.status;
  if (check.error || check.status === "failed" || check.status === "cancelled") return "env_error";
  return "pending";
}

export function feedbackFor(checkpoint) {
  const check = checkpoint.latest_check;
  if (!check) return { score: null, maxScore: null, text: "", status: "none" };
  const item = (check.items ?? []).find((entry) => entry.item_id === checkpoint.id);
  const itemStatus = SCRIPT_CHECK_STATUS[item?.status]
    ?? (["passed", "failed", "partial", "needs_review"].includes(item?.status)
      ? item.status
      : null);
  return {
    score: item?.score ?? check.score,
    maxScore: item?.max_score ?? check.max_score,
    text: item?.comment || check.summary || check.error || "",
    status: itemStatus || statusWithoutVerdict(check),
  };
}
