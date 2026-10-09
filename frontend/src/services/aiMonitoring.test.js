import { beforeEach, describe, expect, test, vi } from "vitest";

const { apiGetMock, apiGetBlobMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiGetBlobMock: vi.fn(),
}));

vi.mock("./api", () => ({ apiGet: apiGetMock, apiGetBlob: apiGetBlobMock }));

import { AiMonitoringService } from "./aiMonitoring";

describe("AiMonitoringService", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiGetBlobMock.mockReset();
    apiGetMock.mockResolvedValue({});
    apiGetBlobMock.mockResolvedValue(new Blob());
  });

  test("overview 傳送時間 bucket 與比較設定", async () => {
    await AiMonitoringService.overview({
      startDate: "2026-09-01T00:00:00.000Z",
      endDate: "2026-09-08T00:00:00.000Z",
      bucket: "hour",
      compare: true,
      source: "api_key",
    });

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/monitoring/overview?start_date=2026-09-01T00%3A00%3A00.000Z&end_date=2026-09-08T00%3A00%3A00.000Z&bucket=hour&compare=true&source=api_key",
    );
  });

  test("runtime 使用管理員專用的觀測端點", async () => {
    await AiMonitoringService.runtime();

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/monitoring/litellm-runtime",
    );
  });

  test("使用者用量可以限定為申請金鑰呼叫", async () => {
    await AiMonitoringService.listUsersUsage({
      startDate: "2026-09-01T00:00:00.000Z",
      endDate: "2026-09-08T00:00:00.000Z",
      limit: 100,
      source: "api_key",
    });

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/monitoring/users?start_date=2026-09-01T00%3A00%3A00.000Z&end_date=2026-09-08T00%3A00%3A00.000Z&limit=100&source=api_key",
    );
  });

  test("匯出使用 Blob API 並傳送完整篩選，不受明細 limit 影響", async () => {
    await AiMonitoringService.exportCsv({
      startDate: "2026-09-01T00:00:00.000Z",
      endDate: "2026-09-08T00:00:00.000Z",
      source: "platform",
      status: "error",
      modelName: "shared/model",
      callType: "teacher_judge_chat",
      userId: "user-id",
    });

    expect(apiGetBlobMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/monitoring/export?start_date=2026-09-01T00%3A00%3A00.000Z&end_date=2026-09-08T00%3A00%3A00.000Z&source=platform&status=error&model_name=shared%2Fmodel&call_type=teacher_judge_chat&user_id=user-id",
    );
    expect(apiGetMock).not.toHaveBeenCalled();
  });
});
