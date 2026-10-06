import { beforeEach, describe, expect, test, vi } from "vitest";

const { apiDeleteMock, apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiDeleteMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("./api", () => ({
  apiDelete: apiDeleteMock,
  apiGet: apiGetMock,
  apiPatch: vi.fn(),
  apiPost: apiPostMock,
}));

import { AiApiService } from "./aiApi";

test("deleteCredential 使用 DELETE 刪除指定金鑰", async () => {
  await AiApiService.deleteCredential("key-id");
  expect(apiDeleteMock).toHaveBeenCalledWith("/api/v1/ai-api/credentials/key-id");
});

test("getCredential 讀取單把金鑰詳細資料並支援取消請求", async () => {
  const controller = new AbortController();
  await AiApiService.getCredential("key-id", { signal: controller.signal });
  expect(apiGetMock).toHaveBeenCalledWith(
    "/api/v1/ai-api/credentials/key-id", { signal: controller.signal },
  );
});

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

describe("AiApiService.listAllRequests", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiGetMock.mockResolvedValue({});
  });

  test("傳送狀態與筆數篩選", async () => {
    await AiApiService.listAllRequests({ status: "pending", limit: 100 });
    expect(apiGetMock).toHaveBeenCalledWith("/api/v1/ai-api/requests?status=pending&limit=100");
  });

  test("只帶筆數時不送狀態", async () => {
    await AiApiService.listAllRequests({ limit: 1 });
    expect(apiGetMock).toHaveBeenCalledWith("/api/v1/ai-api/requests?limit=1");
  });

  test("不帶參數時維持原本的 URL，由後端套用預設 limit=100", async () => {
    await AiApiService.listAllRequests();
    expect(apiGetMock).toHaveBeenCalledWith("/api/v1/ai-api/requests");
  });

  test("狀態為 all 時不送狀態篩選", async () => {
    await AiApiService.listAllRequests({ status: "all", limit: 100 });
    expect(apiGetMock).toHaveBeenCalledWith("/api/v1/ai-api/requests?limit=100");
  });

  test("傳送狀態、分頁起點與筆數", async () => {
    await AiApiService.listAllRequests({ status: "approved", skip: 100, limit: 50 });
    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/requests?status=approved&skip=100&limit=50",
    );
  });
});

describe("AiApiService.bulkRejectRequests", () => {
  beforeEach(() => {
    apiPostMock.mockReset();
    apiPostMock.mockResolvedValue({});
  });

  test("以同一理由送出選取的申請", async () => {
    await AiApiService.bulkRejectRequests(["request-a", "request-b"], "用途不符");
    expect(apiPostMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/requests/bulk-reject",
      { request_ids: ["request-a", "request-b"], review_comment: "用途不符" },
    );
  });
});

describe("AiApiService 金鑰 API 用量", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiGetMock.mockResolvedValue({});
  });

  test("getMyUsage 帶上日期範圍且只查詢申請金鑰用量", async () => {
    const Original = Intl.DateTimeFormat;
    const spy = vi.spyOn(Intl, "DateTimeFormat").mockImplementation((...args) => {
      const fmt = new Original(...args);
      return { ...fmt, resolvedOptions: () => ({ ...fmt.resolvedOptions(), timeZone: "Asia/Taipei" }) };
    });
    try {
      await AiApiService.getMyUsage({
        start_date: "2026-09-01",
        end_date: "2026-09-11",
      });
    } finally {
      spy.mockRestore();
    }

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/usage/my?start_date=2026-09-01&end_date=2026-09-11&tz=Asia%2FTaipei",
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
