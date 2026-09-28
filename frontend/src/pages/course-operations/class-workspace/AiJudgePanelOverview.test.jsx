import { describe, expect, test } from "vitest";
import { getStudentOverviewStatus, sortStudentOverviewRows } from "./AiJudgePanel";

describe("sortStudentOverviewRows 學號排序", () => {
  test("沒有 email／帳號的學生改用 studentId 排序", () => {
    const rows = [
      { studentId: "s-10", user: {}, status: { kind: "automatic", pending: 0 } },
      { studentId: "s-2", user: {}, status: { kind: "automatic", pending: 0 } },
      { studentId: "s-1", user: { email: "" }, status: { kind: "automatic", pending: 0 } },
    ];
    expect(sortStudentOverviewRows(rows, "student-number").map((row) => row.studentId))
      .toEqual(["s-1", "s-2", "s-10"]);
  });

  test("有 email 時仍以 email 帳號排序", () => {
    const rows = [
      { studentId: "b", user: { email: "s10@example.edu" }, status: { kind: "automatic" } },
      { studentId: "a", user: { email: "s2@example.edu" }, status: { kind: "automatic" } },
    ];
    expect(sortStudentOverviewRows(rows, "student-number").map((row) => row.studentId)).toEqual(["a", "b"]);
  });
});

describe("getStudentOverviewStatus", () => {
  const reviewed = { target: { parsed_result: { checks: [{ id: "c", status: "unknown" }] }, teacher_review: { decisions: { c: "pass" } } } };
  const automatic = { target: { parsed_result: { checks: [{ id: "c", status: "pass" }] } } };

  test("有已核查的機器就算核查完成（不論是否混有 AI 已判定）", () => {
    expect(getStudentOverviewStatus([reviewed]).kind).toBe("reviewed");
    expect(getStudentOverviewStatus([reviewed, automatic]).kind).toBe("reviewed");
    expect(getStudentOverviewStatus([reviewed, { target: null }]).kind).toBe("reviewed");
  });
});
