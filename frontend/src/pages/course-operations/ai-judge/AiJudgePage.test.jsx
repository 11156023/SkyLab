import { describe, expect, test } from "vitest";
import { normalizeAiJudgeClass, toAiJudgeMembers } from "./AiJudgePage";

describe("AiJudgePage data boundary", () => {
  test("保留班級標頭與 AI 檢查需要的週次資料", () => {
    const result = normalizeAiJudgeClass({
      id: 42,
      start_time: "09:30:00",
      end_time: "11:00:00",
      students: [{ id: 7, email: "student@example.edu" }],
      weeks: [{ id: 3, week_number: 2, title: null }],
    });

    expect(result.id).toBe("42");
    expect(result.startTime).toBe("09:30");
    expect(result.endTime).toBe("11:00");
    expect(result.students[0].id).toBe("7");
    expect(result.weeks[0]).toMatchObject({ id: "3", week: 2, title: "" });
  });

  test("將班級學生機器轉為執行結果需要的 member contract", () => {
    expect(toAiJudgeMembers([
      { id: "enrollment-1", user_id: "u-1", email: "student@example.edu", full_name: "學生", vms: [{ vmid: 101, status: "running", vm_type: "qemu" }] },
    ])).toEqual([{
      student_id: "enrollment-1",
      user_id: "u-1",
      email: "student@example.edu",
      full_name: "學生",
      vmid: 101,
      vm_status: "running",
      vm_type: "qemu",
    }]);
  });
});
