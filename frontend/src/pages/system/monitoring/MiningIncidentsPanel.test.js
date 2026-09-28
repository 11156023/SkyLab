import { describe, expect, test } from "vitest";
import { dismissOutcome } from "./MiningIncidentsPanel";

const suspended = { id: "i1", status: "suspended" };
const detected = { id: "i2", status: "detected" };

describe("MiningIncidentsPanel 誤判解除結果提示", () => {
  test("後端回 warnings 時改報警告，不能報成功", () => {
    const out = dismissOutcome(suspended, { status: "dismissed", review_note: "x", warnings: ["恢復失敗，請手動開機：timeout"] }, "x");
    expect(out.level).toBe("warning");
    expect(out.key).toBe("MiningIncidentsPanel.toastDismissedWithWarnings");
    expect(out.message).toContain("恢復失敗");
  });

  test("warnings 為空陣列時依原狀態選成功文案", () => {
    expect(dismissOutcome(suspended, { status: "dismissed", warnings: [] }, null)).toEqual({
      level: "success",
      key: "MiningIncidentsPanel.toastDismissedAndRecovered",
    });
    expect(dismissOutcome(detected, { status: "dismissed", warnings: [] }, null).key).toBe(
      "MiningIncidentsPanel.toastDismissed",
    );
  });

  test("舊版後端沒有 warnings：review_note 被附加失敗原因時仍報警告", () => {
    const out = dismissOutcome(suspended, { status: "dismissed", review_note: "恢復失敗，請手動開機：timeout" }, null);
    expect(out.level).toBe("warning");
    expect(out.message).toBe("恢復失敗，請手動開機：timeout");
    const withNote = dismissOutcome(suspended, { status: "dismissed", review_note: "誤判 | 恢復失敗，請手動開機：timeout" }, "誤判");
    expect(withNote.level).toBe("warning");
  });

  test("舊版後端沒有 warnings：review_note 等於送出的備註就是成功", () => {
    expect(dismissOutcome(suspended, { status: "dismissed", review_note: "誤判" }, "誤判").level).toBe("success");
    expect(dismissOutcome(detected, { status: "dismissed", review_note: null }, null).level).toBe("success");
  });
});
