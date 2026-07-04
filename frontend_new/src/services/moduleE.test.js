/**
 * moduleE.test.js
 * 驗證 classroom / teaching / pairSessions / quotas 四個 service 的 URL 與 body。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { ClassroomService } from "./classroom";
import { PairSessionsService } from "./pairSessions";
import { QuotasService } from "./quotas";
import { TeachingService } from "./teaching";

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

let fetchMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

describe("ClassroomService", () => {
  test("createSession 送出 vmid/mode/group_id", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await ClassroomService.createSession({ vmid: 105, mode: "broadcast", group_id: "g-1" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/classroom/sessions");
    expect(JSON.parse(init.body)).toEqual({ vmid: 105, mode: "broadcast", group_id: "g-1" });
  });

  test("setControl 打到 control 並帶 action", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await ClassroomService.setControl("cs-1", "take");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/classroom/sessions/cs-1/control");
    expect(JSON.parse(init.body)).toEqual({ action: "take" });
  });
});

describe("TeachingService", () => {
  test("getHeatmap 帶 group_id 參數", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));

    await TeachingService.getHeatmap("g-1");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/teaching/heatmap?group_id=g-1");
  });

  test("startConfigPush 以 multipart 送出 file/target_path/vmids", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(202, { task_id: "t-1" }));

    const file = new Blob(["hello"], { type: "text/plain" });
    await TeachingService.startConfigPush({
      file,
      targetPath: "/etc/motd",
      vmids: [101, 102],
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/teaching/config-push");
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.body.get("target_path")).toBe("/etc/motd");
    expect(init.body.getAll("vmids")).toEqual(["101", "102"]);
  });
});

describe("PairSessionsService", () => {
  test("create 送出 vmid 與 invitee_user_id", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(201, {}));

    await PairSessionsService.create(105, "u-2");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/pair-sessions");
    expect(JSON.parse(init.body)).toEqual({ vmid: 105, invitee_user_id: "u-2" });
  });
});

describe("QuotasService", () => {
  test("update 以 PUT 打到 quota id", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await QuotasService.update("q-1", { max_instances: 5 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/quotas/q-1");
    expect(init.method).toBe("PUT");
  });
});
