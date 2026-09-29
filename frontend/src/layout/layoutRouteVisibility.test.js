import { describe, expect, test } from "vitest";
import { isAiJudgePath } from "./layoutRouteVisibility";

describe("isAiJudgePath", () => {
  test("只將 AI 檢查頁與其舊連結視為無側邊欄路由", () => {
    expect(isAiJudgePath("/class-management/class-1/ai")).toBe(true);
    expect(isAiJudgePath("/class-management/class-1/ai/checks/session-1/edit")).toBe(true);
    expect(isAiJudgePath("/class-management/class-1")).toBe(false);
    expect(isAiJudgePath("/class-management/class-1/weekly")).toBe(false);
    expect(isAiJudgePath("/class-management/class-1/ai-tools")).toBe(false);
  });
});
