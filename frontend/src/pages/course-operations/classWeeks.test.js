import { describe, expect, it } from "vitest";
import { isWeekVisible, parseStudentEmails, visibleWeekCount, weekFilesPayload } from "./classWeeks";

describe("parseStudentEmails", () => {
  it("accepts common separators, normalizes case, and removes duplicates", () => {
    expect(parseStudentEmails("A@EXAMPLE.COM, b@example.com\nA@example.com; c@example.com")).toEqual([
      "a@example.com",
      "b@example.com",
      "c@example.com",
    ]);
  });

  it("也把空白當分隔，空字串回傳空陣列", () => {
    expect(parseStudentEmails("a@example.com b@example.com\t c@example.com")).toEqual([
      "a@example.com",
      "b@example.com",
      "c@example.com",
    ]);
    expect(parseStudentEmails("  \n ,; ")).toEqual([]);
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

  it("isWeekVisible 與後端 VISIBLE_WEEK_STATUSES 一致", () => {
    expect(isWeekVisible({ status: "published" })).toBe(true);
    expect(isWeekVisible({ status: "completed" })).toBe(true);
    expect(isWeekVisible({ status: "draft" })).toBe(false);
    expect(isWeekVisible(undefined)).toBe(false);
  });
});

describe("weekFilesPayload", () => {
  it("只送已上傳檔案的 id，不帶 storage_key", () => {
    expect(weekFilesPayload([
      { id: 7, filename: "lab.pdf", storage_key: "hack.task", target_path: "/root/lab.pdf" },
      { id: "file-2", filename: "notes.md" },
      { filename: "還沒上傳.pdf" },
    ])).toEqual([
      { id: "7", target_path: "/root/lab.pdf" },
      { id: "file-2", target_path: null },
    ]);
    expect(weekFilesPayload(undefined)).toEqual([]);
  });
});
