import { describe, expect, it } from "vitest";
import { parseStudentEmails, templateBuilderPath, visibleWeekCount, weekPayload } from "./ClassSetupPage";

describe("parseStudentEmails", () => {
  it("accepts common separators, normalizes case, and removes duplicates", () => {
    expect(parseStudentEmails("A@EXAMPLE.COM, b@example.com\nA@example.com; c@example.com")).toEqual([
      "a@example.com",
      "b@example.com",
      "c@example.com",
    ]);
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

describe("visibleWeekCount", () => {
  it("只算學生真的看得到的週次", () => {
    expect(visibleWeekCount([
      { status: "draft" },
      { status: "published" },
      { status: "completed" },
    ])).toBe(2);
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
