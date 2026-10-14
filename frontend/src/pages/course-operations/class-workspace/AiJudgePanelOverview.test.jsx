import { describe, expect, test } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ExecutionTiming, buildBatchReviewTarget, getStudentOverviewStatus, sortStudentOverviewRows } from "./AiJudgePanel";

describe("ExecutionTiming", () => {
  test("整批就緒時間包含後續節點，批次投影保留計時", () => {
    const timing = { queue_seconds: 2, prepare_seconds: 3, script_seconds: 7,
      download_seconds: 1, finalize_wait_seconds: 4, batch_wait_seconds: 18,
      total_seconds: 14 };
    const target = buildBatchReviewTarget({ timing });
    const html = renderToStaticMarkup(<ExecutionTiming timing={target.timing} />);
    expect(html).toContain("10.000");
    expect(html).toContain("23.000");
    expect(html).toContain("14.000");
  });

  test("舊紀錄與失敗不會顯示未量測階段為零秒", () => {
    expect(renderToStaticMarkup(<ExecutionTiming />)).toBe("");
    const html = renderToStaticMarkup(<ExecutionTiming timing={{ queue_seconds: 0, total_seconds: 1.25 }} />);
    expect(html).toContain("0.000");
    expect(html).toContain("1.250");
    expect(html.match(/<dt>/g)).toHaveLength(2);
  });

  test("未完成批次及無效等待時間不顯示結果已就緒", () => {
    for (const batch_wait_seconds of [null, "18", -1, Infinity]) {
      const html = renderToStaticMarkup(<ExecutionTiming timing={{
        download_seconds: 1, finalize_wait_seconds: 4, batch_wait_seconds,
      }} />);
      expect(html).toContain("1.000");
      expect(html.match(/<dt>/g)).toHaveLength(1);
    }
  });
});

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
