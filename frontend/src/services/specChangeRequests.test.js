/**
 * specChangeRequests.test.js
 * 驗證規格調整申請 service 的 URL／method，以及前端共用的狀態判斷 helper。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  SpecChangeRequestsService,
  canApplySpecRequest,
  canCancelSpecRequest,
  isOpenSpecRequest,
  specRequestChangeLabel,
  specRequestDisplayStatus,
} from "./specChangeRequests";

function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const jsonRes = (status, body = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

/* 假的 t()：把 key 與參數攤成可比對的字串 */
const fakeT = (key, params) =>
  params ? `${key}(${Object.entries(params).map(([k, v]) => `${k}=${v}`).join(",")})` : key;

let fetchMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

describe("SpecChangeRequestsService", () => {
  test("listMy 打 /my 並帶 limit", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }));

    await SpecChangeRequestsService.listMy();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/spec-change-requests/my?limit=100");
    expect(init.method ?? "GET").toBe("GET");
  });

  test("apply 以 POST 打 /{id}/apply（202 背景任務）", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(202, { message: "ok", task_id: "spec-apply-abc", request: {} }),
    );

    const res = await SpecChangeRequestsService.apply("abc");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/spec-change-requests/abc/apply");
    expect(init.method).toBe("POST");
    expect(res.task_id).toBe("spec-apply-abc");
  });

  test("cancel 以 POST 打 /{id}/cancel", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { status: "cancelled" }));

    await SpecChangeRequestsService.cancel("abc");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/spec-change-requests/abc/cancel");
    expect(init.method).toBe("POST");
  });

  test("listAll 只帶有值的篩選", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }));

    await SpecChangeRequestsService.listAll({ status: "pending" });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("status=pending");
    expect(url).not.toContain("vmid=");
  });
});

describe("spec request display helpers", () => {
  const approved = (apply_status) => ({ status: "approved", apply_status });

  test("open = 待審核或已核准未套用完成", () => {
    expect(isOpenSpecRequest({ status: "pending" })).toBe(true);
    expect(isOpenSpecRequest(approved("ready"))).toBe(true);
    expect(isOpenSpecRequest(approved("applying"))).toBe(true);
    expect(isOpenSpecRequest(approved("applied"))).toBe(false);
    expect(isOpenSpecRequest({ status: "rejected" })).toBe(false);
    expect(isOpenSpecRequest({ status: "cancelled" })).toBe(false);
  });

  test("套用按鈕只在 ready / failed / interrupted 出現", () => {
    expect(canApplySpecRequest(approved("ready"))).toBe(true);
    expect(canApplySpecRequest(approved("failed"))).toBe(true);
    expect(canApplySpecRequest(approved("interrupted"))).toBe(true);
    expect(canApplySpecRequest(approved("applying"))).toBe(false);
    expect(canApplySpecRequest(approved("applied"))).toBe(false);
    expect(canApplySpecRequest({ status: "pending" })).toBe(false);
  });

  test("撤銷：待審核或還沒開始套用；套用中不能撤", () => {
    expect(canCancelSpecRequest({ status: "pending" })).toBe(true);
    expect(canCancelSpecRequest(approved("ready"))).toBe(true);
    expect(canCancelSpecRequest(approved("applying"))).toBe(false);
    expect(canCancelSpecRequest(approved("applied"))).toBe(false);
  });

  test("狀態徽章依 apply_status 細分，文案交給 labelKey", () => {
    expect(specRequestDisplayStatus(approved("ready"))).toEqual({
      key: "ready", color: "warning", labelKey: "SpecRequest.statusReady",
    });
    expect(specRequestDisplayStatus(approved("applying")).key).toBe("applying");
    expect(specRequestDisplayStatus(approved("applied")).color).toBe("success");
    expect(specRequestDisplayStatus(approved("failed")).labelKey).toBe("SpecRequest.statusApplyFailed");
    expect(specRequestDisplayStatus({ status: "cancelled" }).color).toBe("muted");
  });

  test("變更摘要只列有申請的項目，記憶體換算成 GB", () => {
    expect(
      specRequestChangeLabel(
        { current_cpu: 2, requested_cpu: 4, current_memory: 2048, requested_memory: 3072 },
        fakeT,
      ),
    ).toBe(
      "SpecRequest.changeCpu(from=2,to=4) / SpecRequest.changeMemory(from=SpecRequest.memUnit(value=2),to=SpecRequest.memUnit(value=3))",
    );
    expect(specRequestChangeLabel({ current_disk: 20, requested_disk: 40 }, fakeT)).toBe(
      "SpecRequest.changeDisk(from=20,to=40)",
    );
    expect(specRequestChangeLabel({}, fakeT)).toBe("—");
  });
});
