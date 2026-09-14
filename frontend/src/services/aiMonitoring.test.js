import { beforeEach, describe, expect, test, vi } from "vitest";

const { apiGetMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
}));

vi.mock("./api", () => ({ apiGet: apiGetMock }));

import { AiMonitoringService } from "./aiMonitoring";

describe("AiMonitoringService", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiGetMock.mockResolvedValue({});
  });

  test("overview 傳送時間 bucket 與比較設定", async () => {
    await AiMonitoringService.overview({
      startDate: "2026-09-01T00:00:00.000Z",
      endDate: "2026-09-08T00:00:00.000Z",
      bucket: "hour",
      compare: true,
    });

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/monitoring/overview?start_date=2026-09-01T00%3A00%3A00.000Z&end_date=2026-09-08T00%3A00%3A00.000Z&bucket=hour&compare=true",
    );
  });

  test("runtime 使用管理員專用的觀測端點", async () => {
    await AiMonitoringService.runtime();

    expect(apiGetMock).toHaveBeenCalledWith(
      "/api/v1/ai-api/monitoring/litellm-runtime",
    );
  });
});
