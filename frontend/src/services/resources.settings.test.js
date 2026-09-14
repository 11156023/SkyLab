/**
 * resources.settings.test.js
 * 驗證 ResourcesService 進階設定端點（規格摘要、開機選項、憑證、標籤備註、共享轉移）的 URL 與 body。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { ResourcesService } from "./resources";

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

function lastCall() {
  const [url, init] = fetchMock.mock.calls.at(-1);
  return { url, init, body: init?.body ? JSON.parse(init.body) : undefined };
}

describe("ResourcesService 進階設定端點", () => {
  test("getSpecs 打 /specs", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { cpu_cores: 2 }));
    await ResourcesService.getSpecs(105);
    expect(lastCall().url).toContain("/api/v1/resources/105/specs");
  });

  test("updateBootOptions 用 PUT 送出 boot_order", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));
    await ResourcesService.updateBootOptions(105, { boot_order: ["scsi0", "net0"] });
    const { url, init, body } = lastCall();
    expect(url).toContain("/api/v1/resources/105/boot-options");
    expect(init.method).toBe("PUT");
    expect(body).toEqual({ boot_order: ["scsi0", "net0"] });
  });

  test("resetPassword 留空時送 null", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { password: "x" }));
    await ResourcesService.resetPassword(105);
    const { url, init, body } = lastCall();
    expect(url).toContain("/api/v1/resources/105/credentials/reset-password");
    expect(init.method).toBe("POST");
    expect(body).toEqual({ password: null });
  });

  test("removeAuthorizedKey 用 DELETE 帶 JSON body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { authorized_keys: [] }));
    await ResourcesService.removeAuthorizedKey(105, "ssh-ed25519 AAAA test");
    const { url, init, body } = lastCall();
    expect(url).toContain("/api/v1/resources/105/credentials/authorized-keys");
    expect(init.method).toBe("DELETE");
    expect(body).toEqual({ public_key: "ssh-ed25519 AAAA test" });
  });

  test("updateMetadata 送出 tags 與 description", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));
    await ResourcesService.updateMetadata(105, { tags: ["db"], description: "期末專題" });
    const { url, body } = lastCall();
    expect(url).toContain("/api/v1/resources/105/metadata");
    expect(body).toEqual({ tags: ["db"], description: "期末專題" });
  });

  test("addShare / removeShare / transferOwnership 的路徑與 body", async () => {
    fetchMock.mockResolvedValue(jsonRes(200, {}));
    await ResourcesService.addShare(105, "friend@example.com");
    expect(lastCall().url).toContain("/api/v1/resources/105/shares");
    expect(lastCall().body).toEqual({ email: "friend@example.com" });

    await ResourcesService.removeShare(105, "share-1");
    expect(lastCall().url).toContain("/api/v1/resources/105/shares/share-1");
    expect(lastCall().init.method).toBe("DELETE");

    await ResourcesService.transferOwnership(105, "new@example.com", true);
    expect(lastCall().url).toContain("/api/v1/resources/105/transfer");
    expect(lastCall().body).toEqual({ email: "new@example.com", keep_access: true });
  });
});
