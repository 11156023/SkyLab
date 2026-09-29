import { describe, expect, it } from "vitest";
import { furthestReachableStep, templateBuilderPath, weekPayload } from "./ClassSetupPage";

describe("furthestReachableStep", () => {
  it("還沒建班只能停在第一步", () => {
    expect(furthestReachableStep({ saved: false, hasStudents: false, hasEnvironment: false })).toBe(1);
  });

  it("必要步驟依序解鎖：沒有學生停在第二步、沒有環境停在第三步", () => {
    expect(furthestReachableStep({ saved: true, hasStudents: false, hasEnvironment: false })).toBe(2);
    expect(furthestReachableStep({ saved: true, hasStudents: true, hasEnvironment: false })).toBe(3);
  });

  it("每週任務是選填：必要步驟都完成就能直接到確認建立", () => {
    expect(furthestReachableStep({ saved: true, hasStudents: true, hasEnvironment: true })).toBe(5);
  });
});

describe("weekPayload", () => {
  it("keeps the generated class dates and trims checkpoint titles", () => {
    expect(weekPayload([{ week_number: 1, session_date: "2026-09-07", title: "  完成 SSH 連線  " }])).toEqual([
      {
        week_number: 1,
        session_date: "2026-09-07",
        title: "完成 SSH 連線",
        target_node_key: null,
        status: "draft",
        files: [],
      },
    ]);
  });
});

describe("weekPayload files", () => {
  it("只送已上傳檔案的 id，不帶 storage_key", () => {
    const rows = weekPayload([
      {
        week_number: 1,
        session_date: "2026-09-07",
        title: "Linux 權限",
        files: [
          { id: "file-1", filename: "lab.pdf", storage_key: "hack.task", target_path: "/root/lab.pdf" },
          { filename: "還沒上傳.pdf" },
        ],
      },
    ]);

    expect(rows[0].files).toEqual([{ id: "file-1", target_path: "/root/lab.pdf" }]);
  });
});

describe("weekPayload publishing", () => {
  it("勾選發布時，有主題的週次才會變成學生看得到的狀態", () => {
    const rows = weekPayload(
      [
        { week_number: 1, session_date: "2026-09-09", title: "Linux 權限" },
        { week_number: 2, session_date: "2026-09-16", title: "   " },
      ],
      { publish: true },
    );

    expect(rows.map((row) => row.status)).toEqual(["published", "draft"]);
  });

  it("不勾選時維持草稿，學生看不到", () => {
    const rows = weekPayload(
      [{ week_number: 1, session_date: "2026-09-09", title: "Linux 權限" }],
      { publish: false },
    );

    expect(rows[0].status).toBe("draft");
  });

  it("不把已完成的週次降級成 published", () => {
    const rows = weekPayload(
      [{ week_number: 1, session_date: "2026-09-09", title: "Linux 權限", status: "completed" }],
      { publish: true },
    );

    expect(rows[0].status).toBe("completed");
  });
});

describe("templateBuilderPath", () => {
  it("建立模板後會返回原班級的環境步驟", () => {
    const destination = templateBuilderPath("class-1");
    const params = new URLSearchParams(destination.split("?")[1]);

    expect(destination).toContain("/course-template-management/new?");
    expect(params.get("returnTo")).toBe("/class-setup?classId=class-1&step=3");
  });
});
