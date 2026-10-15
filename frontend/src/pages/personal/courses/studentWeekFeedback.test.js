import { describe, expect, test } from "vitest";
import { feedbackFor } from "./studentWeekFeedback";

const checkpoint = (latestCheck) => ({ id: "service", latest_check: latestCheck });

describe("feedbackFor", () => {
  test("有逐項判定時用那一項的結果", () => {
    const result = feedbackFor(checkpoint({
      status: "completed",
      items: [{ item_id: "service", status: "fail", comment: "服務沒有啟動" }],
    }));
    expect(result.status).toBe("failed");
    expect(result.text).toBe("服務沒有啟動");
  });

  test("執行完成但這一項沒有判定時是等待檢查，不是通過", () => {
    expect(feedbackFor(checkpoint({ status: "completed", items: [] })).status).toBe("pending");
  });

  test("機器連不上或執行失敗時標成環境異常", () => {
    expect(feedbackFor(checkpoint({ status: "failed", items: [] })).status).toBe("env_error");
    expect(feedbackFor(checkpoint({ status: "completed", items: [], error: "檢查未完成" })).status).toBe("env_error");
  });

  test("還在檢查時照實顯示", () => {
    expect(feedbackFor(checkpoint({ status: "running", items: [] })).status).toBe("running");
  });

  test("從來沒檢查過", () => {
    expect(feedbackFor(checkpoint(null)).status).toBe("none");
  });
});
