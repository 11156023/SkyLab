import { describe, expect, test } from "vitest";
import { mergeUnsavedWeekEdits } from "./weeklyEdits";

const week = (date, overrides = {}) => ({
  id: `w-${date}`,
  date,
  week: 1,
  title: "",
  target: "",
  status: "draft",
  files: [],
  ...overrides,
});

describe("mergeUnsavedWeekEdits", () => {
  test("重抓回來的是同一批週次時，畫面上還沒存的主題、機器、發布狀態都保留", () => {
    const server = [week("2026-09-21"), week("2026-09-28")];
    const local = [
      week("2026-09-21"),
      week("2026-09-28", { title: "打到一半的主題", target: "node-1", status: "published" }),
    ];

    const merged = mergeUnsavedWeekEdits(server, local);

    expect(merged[1]).toMatchObject({ title: "打到一半的主題", target: "node-1", status: "published" });
  });

  test("檔案清單等伺服器欄位以伺服器為準", () => {
    const server = [week("2026-09-21", { files: [{ id: "f1", filename: "lab.pdf" }] })];
    const local = [week("2026-09-21", { title: "未存主題" })];

    const [merged] = mergeUnsavedWeekEdits(server, local);

    expect(merged.files).toEqual([{ id: "f1", filename: "lab.pdf" }]);
    expect(merged.title).toBe("未存主題");
  });

  test("上課日期改過、對不到的週直接用伺服器的值", () => {
    const server = [week("2026-10-05", { title: "伺服器的主題" })];
    const local = [week("2026-09-28", { title: "舊日期的主題" })];

    expect(mergeUnsavedWeekEdits(server, local)[0].title).toBe("伺服器的主題");
  });

  test("不改動傳進來的陣列與物件", () => {
    const server = [week("2026-09-21")];
    const local = [week("2026-09-21", { title: "未存主題" })];

    mergeUnsavedWeekEdits(server, local);

    expect(server[0].title).toBe("");
  });
});
