import { beforeEach, describe, expect, test, vi } from "vitest";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock("./api", () => ({
  apiDelete: vi.fn(),
  apiGet: apiGetMock,
  apiPatch: vi.fn(),
  apiPost: vi.fn(),
}));

import { AiApiService } from "./aiApi";

describe("AiApiService.listAllCredentials", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiGetMock.mockResolvedValue({});
  });

  test("傳送管理者列表的狀態、全文搜尋、角色與分頁條件", async () => {
    await AiApiService.listAllCredentials({
      status: "active",
      query: "user-a",
      user_role: ["student", "admin"],
      created_after: "2026-09-01T00:00:00.000Z",
      skip: 50,
      limit: 50,
    });

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/credentials?status=active&query=user-a&user_role=student&user_role=admin&created_after=2026-09-01T00%3A00%3A00.000Z&skip=50&limit=50",
    );
  });
});

describe("AiApiService 統一用量", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiGetMock.mockResolvedValue({});
  });

  test("getMyUsage 帶上日期範圍查詢統一用量", async () => {
    await AiApiService.getMyUsage({
      start_date: "2026-09-01",
      end_date: "2026-09-11",
    });

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/usage/my?start_date=2026-09-01&end_date=2026-09-11",
    );
  });

  test("getMyUsageRecords 帶上日期範圍與分頁條件", async () => {
    await AiApiService.getMyUsageRecords({
      start_date: "2026-09-01",
      end_date: "2026-09-11",
      skip: 20,
      limit: 20,
    });

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/usage/records/my?start_date=2026-09-01&end_date=2026-09-11&skip=20&limit=20",
    );
  });
});
