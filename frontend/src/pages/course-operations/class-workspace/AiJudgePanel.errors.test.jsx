// @vitest-environment happy-dom
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";
import {
  CommandLog, ExecutionFailure, aggregateStudentCheckTotals,
  buildBatchReviewTarget, checkpointBadge, checkOutcome, getCheckResultSummary,
} from "./AiJudgePanel";
import "../../../i18n";

describe("teacher execution diagnostics", () => {
  test.each([
    ["ssh_authentication_failed", "SSH 連線或認證失敗"],
    ["python_missing", "主機執行環境無法執行檢查"],
    ["invalid_json", "檢查腳本或結果格式有誤"],
    ["missing_checks", "檢查結果不完整"],
    ["check_id_mismatch", "檢查結果不完整"],
    ["duplicate_check_ids", "檢查結果不完整"],
    ["download_failed", "檢查結果傳輸失敗"],
    ["executor_error", "檢查執行失敗，原因待確認"],
  ])("%s 顯示失敗位置與證據，不判定為學生未通過", (reason, title) => {
    const target = buildBatchReviewTarget({
      execution_status: "failed", reason_code: reason, exit_code: 2,
      failure_stage: "executing", stderr_excerpt: "remote diagnostic",
      stdout_excerpt: "partial output", validation: { valid: false, error: "bad schema" },
      items: [],
    });
    const html = renderToStaticMarkup(<ExecutionFailure target={target} />);
    expect(html).toContain(title);
    expect(html).toContain("remote diagnostic");
    expect(html).toContain("partial output");
    expect(html).toContain("bad schema");
    expect(html).toContain("結束代碼 2");
    expect(html).toContain("無法據此判定學生作業是否通過");
  });

  test("缺少步驟優先於部分通過或待核查標籤", () => {
    const badge = checkpointBadge({ status: "unknown", missing_check_ids: ["b"], checks: [{ id: "a", status: "pass" }] });
    expect(badge.label).toBe("證據不完整");
    expect(badge.kind).not.toBe("pass");
  });

  test("失敗執行保留下來的 pass 不計入學生成績", () => {
    const target = { status: "failed", parsed_result: { checks: [{ id: "a", status: "pass" }] } };
    expect(aggregateStudentCheckTotals([{ target }]).pass).toBe(0);
    expect(checkOutcome([{ target }])).toEqual({ kind: "error", count: 1 });
  });

  test("檔案不存在與空檔案仍顯示原始證據，不顯示假的指令例外", () => {
    const raw = JSON.stringify({ exists: false });
    expect(getCheckResultSummary({ raw, evidence: "" })).toBe("檔案或目錄不存在");
    const html = renderToStaticMarkup(<CommandLog raw={raw} />);
    expect(html).toContain("false");
    expect(html).not.toContain("執行例外");
  });
});
