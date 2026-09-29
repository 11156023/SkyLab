import { describe, expect, test, vi } from "vitest";

vi.mock("./api", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));

import { apiGet } from "./api";
import { AiContextualHelpService, matchSurface } from "./aiContextualHelp";

describe("surfaces 快取", () => {
  test("同一個身分只載一次，resetSurfaces 之後會重新向後端要", async () => {
    apiGet.mockReset();
    AiContextualHelpService.resetSurfaces();
    apiGet.mockResolvedValueOnce([{ id: "teacher-only" }]);
    apiGet.mockResolvedValueOnce([{ id: "student-only" }]);

    expect(await AiContextualHelpService.surfaces()).toEqual([{ id: "teacher-only" }]);
    expect(await AiContextualHelpService.surfaces()).toEqual([{ id: "teacher-only" }]);
    expect(apiGet).toHaveBeenCalledTimes(1);

    AiContextualHelpService.resetSurfaces();
    expect(await AiContextualHelpService.surfaces()).toEqual([{ id: "student-only" }]);
    expect(apiGet).toHaveBeenCalledTimes(2);
  });
});

const SURFACES = [
  { id: "my-requests", path: "/my-requests", title: "我的申請" },
  { id: "request-form", path: "/my-requests", title: "申請虛擬機 / 容器" },
  { id: "my-resources", path: "/my-resources", title: "我的資源" },
  { id: "resource-detail", path: "/my-resources/:vmid", title: "資源詳細" },
];

describe("matchSurface", () => {
  test("完全相同的路徑優先", () => {
    expect(matchSurface(SURFACES, "/my-resources").id).toBe("my-resources");
  });

  test("帶參數的路徑靠樣板比對", () => {
    expect(matchSurface(SURFACES, "/my-resources/108").id).toBe("resource-detail");
  });

  test("忽略查詢字串與結尾斜線", () => {
    expect(matchSurface(SURFACES, "/my-resources/?tab=all").id).toBe("my-resources");
  });

  test("段數不同不算命中", () => {
    expect(matchSurface(SURFACES, "/my-resources/108/extra")).toBeNull();
  });

  test("沒有對應的畫面時回 null，讓呼叫端交給下一個能力", () => {
    expect(matchSurface(SURFACES, "/settings")).toBeNull();
    expect(matchSurface([], "/my-requests")).toBeNull();
    expect(matchSurface(SURFACES, "")).toBeNull();
  });
});
