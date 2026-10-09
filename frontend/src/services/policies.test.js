/**
 * policies.test.js
 * VM 登入帳號命名政策：規則查詢與即時檢查端點。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";

const apiGet = vi.fn();
const apiPost = vi.fn();

vi.mock("./api", () => ({
  apiGet: (...args) => apiGet(...args),
  apiPost: (...args) => apiPost(...args),
}));

const { PoliciesService } = await import("./policies");

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
});

describe("PoliciesService 帳號命名政策", () => {
  test("getUsernameRules 打 GET /api/v1/policies/username 並轉交 signal", async () => {
    const rules = { pattern: "^[a-z][a-z0-9_-]{0,31}$", max_length: 32 };
    apiGet.mockResolvedValue(rules);
    const controller = new AbortController();

    await expect(
      PoliciesService.getUsernameRules({ signal: controller.signal }),
    ).resolves.toEqual(rules);
    expect(apiGet).toHaveBeenCalledWith("/api/v1/policies/username", {
      signal: controller.signal,
    });
  });

  test("checkUsername 以 JSON body 送出帳號，回傳 violations", async () => {
    const result = {
      ok: false,
      violations: [
        { code: "LNX_FORMAT", message: "…", severity: "error" },
        { code: "PLATFORM_RESERVED", message: "…", severity: "error" },
      ],
    };
    apiPost.mockResolvedValue(result);

    await expect(PoliciesService.checkUsername("Skylab")).resolves.toEqual(result);
    expect(apiPost).toHaveBeenCalledWith(
      "/api/v1/policies/username/check",
      { username: "Skylab" },
      {},
    );
  });
});
