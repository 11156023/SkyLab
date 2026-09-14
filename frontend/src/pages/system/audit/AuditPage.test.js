import { describe, expect, test } from "vitest";
import { applyDateField, isDateInFuture, isDateRangeValid, todayDateStr } from "./AuditPage";

const base = { search: "", action: "", userId: "", startDate: "", endDate: "" };
const TODAY = "2026-09-09";
/** 範圍測試用：把「今天」放在所有測試日期之後，隔離未來日期校正 */
const RANGE_TODAY = "2026-12-31";

describe("AuditPage 日期篩選不可查今天以後", () => {
  test("今天以本地時區計算並補零", () => {
    expect(todayDateStr(new Date(2026, 8, 9, 23, 30))).toBe("2026-09-09");
    expect(todayDateStr(new Date(2026, 0, 1, 0, 5))).toBe("2026-01-01");
  });

  test("今天以後才算未來，今天與空值都合法", () => {
    expect(isDateInFuture("2026-09-10", TODAY)).toBe(true);
    expect(isDateInFuture("2026-09-09", TODAY)).toBe(false);
    expect(isDateInFuture("2026-09-01", TODAY)).toBe(false);
    expect(isDateInFuture("", TODAY)).toBe(false);
  });

  test("選到未來日期時校正為今天", () => {
    const next = applyDateField({ ...base, startDate: "2026-09-01", endDate: "" }, "endDate", "2026-12-31", TODAY);
    expect(next.endDate).toBe(TODAY);
    expect(next.startDate).toBe("2026-09-01");
  });

  test("起始選到未來時，校正為今天並把結束一起帶到今天", () => {
    const next = applyDateField({ ...base, startDate: "2026-09-01", endDate: "2026-09-05" }, "startDate", "2026-10-01", TODAY);
    expect(next.startDate).toBe(TODAY);
    expect(next.endDate).toBe(TODAY);
  });
});

describe("AuditPage 日期篩選範圍", () => {
  test("任一端為空即視為合法", () => {
    expect(isDateRangeValid("", "")).toBe(true);
    expect(isDateRangeValid("2026-09-01", "")).toBe(true);
    expect(isDateRangeValid("", "2026-09-01")).toBe(true);
  });

  test("起始不可晚於結束，同一天合法", () => {
    expect(isDateRangeValid("2026-09-01", "2026-09-09")).toBe(true);
    expect(isDateRangeValid("2026-09-09", "2026-09-09")).toBe(true);
    expect(isDateRangeValid("2026-09-10", "2026-09-09")).toBe(false);
    expect(isDateRangeValid("2026-10-01", "2026-09-30")).toBe(false);
  });

  test("起始改到結束之後，結束跟著移到同一天", () => {
    const next = applyDateField({ ...base, startDate: "2026-09-01", endDate: "2026-09-05" }, "startDate", "2026-09-20", RANGE_TODAY);
    expect(next.startDate).toBe("2026-09-20");
    expect(next.endDate).toBe("2026-09-20");
  });

  test("結束改到起始之前，起始跟著移到同一天", () => {
    const next = applyDateField({ ...base, startDate: "2026-09-10", endDate: "2026-09-15" }, "endDate", "2026-09-03", RANGE_TODAY);
    expect(next.startDate).toBe("2026-09-03");
    expect(next.endDate).toBe("2026-09-03");
  });

  test("範圍仍合法時不動另一端，其他篩選欄位保持不變", () => {
    const filters = { ...base, search: "login", startDate: "2026-09-01", endDate: "2026-09-15" };
    const next = applyDateField(filters, "startDate", "2026-09-05", RANGE_TODAY);
    expect(next).toEqual({ ...filters, startDate: "2026-09-05" });

    const cleared = applyDateField(filters, "endDate", "", RANGE_TODAY);
    expect(cleared).toEqual({ ...filters, endDate: "" });
  });
});
