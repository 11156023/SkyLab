import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock("./api", () => ({
  apiDelete: vi.fn(),
  apiGet: apiGetMock,
  apiPatch: vi.fn(),
  apiPost: vi.fn(),
}));

import { AiApiService } from "./aiApi";

beforeEach(() => {
  apiGetMock.mockReset();
  apiGetMock.mockResolvedValue({ data: [], count: 0 });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AiApiService.listAllRequests", () => {
  test("不帶參數時沿用原本的 URL", async () => {
    await AiApiService.listAllRequests();
    expect(apiGetMock).toHaveBeenCalledWith("/api/v1/ai-api/requests");
  });

  test("狀態與筆數交給後端篩選", async () => {
    await AiApiService.listAllRequests({ status: "pending", limit: 1 });
    expect(apiGetMock).toHaveBeenCalledWith("/api/v1/ai-api/requests?status=pending&limit=1");
  });

  test("帶上分頁起點，status 為 all 或空值時不送狀態", async () => {
    await AiApiService.listAllRequests({ status: "all", skip: 0, limit: 50 });
    expect(apiGetMock).toHaveBeenLastCalledWith("/api/v1/ai-api/requests?skip=0&limit=50");

    await AiApiService.listAllRequests({ status: undefined, limit: 100 });
    expect(apiGetMock).toHaveBeenLastCalledWith("/api/v1/ai-api/requests?limit=100");
  });
});

describe("AiApiService.getMyUsage 時區", () => {
  function stubTimeZone(timeZone) {
    const Original = Intl.DateTimeFormat;
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation((...args) => {
      const fmt = new Original(...args);
      return { ...fmt, resolvedOptions: () => ({ ...fmt.resolvedOptions(), timeZone }) };
    });
  }

  test("帶上瀏覽器的 IANA 時區讓後端依當地日期切分", async () => {
    stubTimeZone("Asia/Taipei");
    await AiApiService.getMyUsage({ start_date: "2026-09-01", end_date: "2026-09-11" });
    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/usage/my?start_date=2026-09-01&end_date=2026-09-11&tz=Asia%2FTaipei",
    );
  });

  test("取不到時區時不送 tz（後端退回 UTC）", async () => {
    stubTimeZone(undefined);
    await AiApiService.getMyUsage({ start_date: "2026-09-01", end_date: "2026-09-11" });
    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/usage/my?start_date=2026-09-01&end_date=2026-09-11",
    );
  });
});
