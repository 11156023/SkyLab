// @vitest-environment happy-dom
/**
 * 驗證子網存檔／刪除成功後才發出 SUBNET_CHANGED_EVENT（SubnetBanner 靠它立即重查），
 * 失敗時不發、錯誤原樣往外拋。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const { apiPut, apiDelete } = vi.hoisted(() => ({ apiPut: vi.fn(), apiDelete: vi.fn() }));

vi.mock("./api", () => ({ apiGet: vi.fn(), apiPut, apiDelete }));

import { IpManagementService, SUBNET_CHANGED_EVENT } from "./ipManagement";

let listener;

beforeEach(() => {
  apiPut.mockReset();
  apiDelete.mockReset();
  listener = vi.fn();
  window.addEventListener(SUBNET_CHANGED_EVENT, listener);
});

afterEach(() => {
  window.removeEventListener(SUBNET_CHANGED_EVENT, listener);
});

describe("SUBNET_CHANGED_EVENT", () => {
  test("事件名稱與 SubnetBanner 監聽的字面值一致", () => {
    expect(SUBNET_CHANGED_EVENT).toBe("skylab:subnet-changed");
  });

  test("upsertSubnet 成功後發出一次事件並回傳結果", async () => {
    const res = { cidr: "10.10.0.0/24" };
    apiPut.mockResolvedValueOnce(res);

    await expect(IpManagementService.upsertSubnet({ cidr: "10.10.0.0/24" })).resolves.toBe(res);

    expect(apiPut).toHaveBeenCalledWith("/api/v1/ip-management/subnet", { cidr: "10.10.0.0/24" });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  test("deleteSubnet 成功後發出一次事件並回傳結果", async () => {
    const res = { message: "ok" };
    apiDelete.mockResolvedValueOnce(res);

    await expect(IpManagementService.deleteSubnet()).resolves.toBe(res);

    expect(apiDelete).toHaveBeenCalledWith("/api/v1/ip-management/subnet");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  test("upsertSubnet 失敗時不發事件，錯誤原樣拋出", async () => {
    const err = new Error("boom");
    apiPut.mockRejectedValueOnce(err);

    await expect(IpManagementService.upsertSubnet({})).rejects.toBe(err);

    expect(listener).not.toHaveBeenCalled();
  });

  test("deleteSubnet 失敗時不發事件，錯誤原樣拋出", async () => {
    const err = new Error("boom");
    apiDelete.mockRejectedValueOnce(err);

    await expect(IpManagementService.deleteSubnet()).rejects.toBe(err);

    expect(listener).not.toHaveBeenCalled();
  });
});
