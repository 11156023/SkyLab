/**
 * resources.osImages.test.js
 * 申請表單與課程範本編輯頁共用的作業系統映像／範本清單端點。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";

const apiGet = vi.fn();

vi.mock("./api", () => ({
  apiGet: (...args) => apiGet(...args),
  apiGetBlob: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
  apiDeleteJson: vi.fn(),
}));

const { ResourcesService } = await import("./resources");

beforeEach(() => {
  apiGet.mockReset();
  apiGet.mockResolvedValue([]);
});

describe("ResourcesService 作業系統清單", () => {
  test("listLxcOsImages 打 /api/v1/lxc/templates", async () => {
    await ResourcesService.listLxcOsImages();
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith("/api/v1/lxc/templates");
  });

  test("listVmOsTemplates 打 /api/v1/vm/templates", async () => {
    await ResourcesService.listVmOsTemplates();
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith("/api/v1/vm/templates");
  });

  test("直接回傳 apiGet 的結果", async () => {
    apiGet.mockResolvedValueOnce([{ volid: "local:vztmpl/debian.tar.zst" }]);
    await expect(ResourcesService.listLxcOsImages()).resolves.toEqual([
      { volid: "local:vztmpl/debian.tar.zst" },
    ]);
  });
});
