/**
 * submitConnection.test.js
 * 蓋住 ConnectionDialog 的送出流程，重點是多筆發布途中失敗的處理：
 * 已經成功的那幾條必須回報出來，呼叫端才知道要先刷新畫面。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../../services/firewall", () => ({
  createConnection: vi.fn(),
  createVmRule: vi.fn(),
  publishService: vi.fn(),
  replacePublishedService: vi.fn(),
}));

import {
  createConnection,
  createVmRule,
  publishService,
  replacePublishedService,
} from "../../services/firewall";
import { submitEdge, submitInbound, submitRule } from "./submitConnection";

beforeEach(() => {
  vi.clearAllMocks();
  createConnection.mockResolvedValue({});
  createVmRule.mockResolvedValue({});
  publishService.mockResolvedValue({});
  replacePublishedService.mockResolvedValue({});
});

describe("submitRule", () => {
  test("成功時回傳 rule 結果", async () => {
    const res = await submitRule({ vmid: 101, body: { type: "in", action: "ACCEPT" } });
    expect(createVmRule).toHaveBeenCalledWith(101, { type: "in", action: "ACCEPT" });
    expect(res).toEqual({ ok: true, result: { kind: "rule", vmid: 101 } });
  });

  test("失敗時優先帶出後端訊息", async () => {
    createVmRule.mockRejectedValue(new Error("rule rejected"));
    const res = await submitRule({ vmid: 101, body: {} });
    expect(res.ok).toBe(false);
    expect(res.error).toEqual({ key: "ConnectionDialog.createFailed", text: "rule rejected" });
  });
});

describe("submitInbound：新增發布", () => {
  const publish = [
    { port: 80, protocol: "tcp", mode: "firewall_only" },
    { port: 443, protocol: "tcp", mode: "firewall_only" },
  ];

  test("逐筆發布並回報總數", async () => {
    const res = await submitInbound({ vmid: 101, publish, raw: [] });
    expect(publishService).toHaveBeenCalledTimes(2);
    expect(res).toEqual({ ok: true, result: { kind: "publish", vmid: 101, count: 2 } });
  });

  test("無 port 協定另外走 createConnection，並計入總數", async () => {
    const raw = [{ port: 0, protocol: "icmp" }];
    const res = await submitInbound({ vmid: 101, publish, raw });
    expect(createConnection).toHaveBeenCalledWith({
      source_vmid: null,
      target_vmid: 101,
      ports: raw,
      direction: "one_way",
    });
    expect(res.result.count).toBe(3);
  });

  test("只有無 port 協定時不呼叫 publishService", async () => {
    const res = await submitInbound({ vmid: 101, publish: [], raw: [{ port: 0, protocol: "icmp" }] });
    expect(publishService).not.toHaveBeenCalled();
    expect(res.result.count).toBe(1);
  });

  test("第二筆失敗時回報已成功一筆，並帶出是哪個 port", async () => {
    publishService
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new Error("port in use"));

    const res = await submitInbound({ vmid: 101, publish, raw: [] });
    expect(res.ok).toBe(false);
    expect(res.partialDone).toBe(1);
    expect(res.error.key).toBe("ConnectionDialog.partialFailed");
    expect(res.error.params).toEqual({ done: 1, port: "443/tcp", message: "port in use" });
  });

  test("第一筆就失敗時 partialDone 為 0，且不再送出後續", async () => {
    publishService.mockRejectedValue(new Error("boom"));
    const res = await submitInbound({ vmid: 101, publish, raw: [] });
    expect(publishService).toHaveBeenCalledTimes(1);
    expect(res.partialDone).toBe(0);
    expect(res.error.params.port).toBe("80/tcp");
  });

  test("後端沒給訊息時留空，讓呼叫端填通用文案", async () => {
    publishService.mockRejectedValue({});
    const res = await submitInbound({ vmid: 101, publish, raw: [] });
    expect(res.error.params.message).toBeNull();
  });

  test("發布都成功但 raw 失敗時，仍回報已完成的筆數", async () => {
    createConnection.mockRejectedValue(new Error("conn failed"));
    const res = await submitInbound({
      vmid: 101,
      publish,
      raw: [{ port: 0, protocol: "icmp" }],
    });
    expect(res.ok).toBe(false);
    expect(res.partialDone).toBe(2);
    expect(res.error.text).toBe("conn failed");
  });
});

describe("submitInbound：編輯既有發布", () => {
  const service = { port: 8080, protocol: "tcp" };
  const next = { port: 9090, protocol: "tcp", mode: "port_forward", external_port: 19090 };

  test("走替換而不是新增", async () => {
    const res = await submitInbound({ vmid: 101, publish: [next], raw: [], service });
    expect(replacePublishedService).toHaveBeenCalledWith(101, { port: 8080, protocol: "tcp" }, next);
    expect(publishService).not.toHaveBeenCalled();
    expect(res).toEqual({ ok: true, result: { kind: "replace", vmid: 101 } });
  });

  test("替換失敗時帶出後端訊息", async () => {
    replacePublishedService.mockRejectedValue(new Error("replace failed"));
    const res = await submitInbound({ vmid: 101, publish: [next], raw: [], service });
    expect(res.ok).toBe(false);
    expect(res.error.text).toBe("replace failed");
  });
});

describe("submitEdge", () => {
  test("出站帶 null 當作網際網路那一端", async () => {
    const ports = [{ port: 0, protocol: "tcp" }];
    const res = await submitEdge({
      sourceVmid: 101,
      targetVmid: null,
      ports,
      direction: "one_way",
    });
    expect(createConnection).toHaveBeenCalledWith({
      source_vmid: 101,
      target_vmid: null,
      ports,
      direction: "one_way",
    });
    expect(res.result).toEqual({ kind: "connection", source_vmid: 101, target_vmid: null });
  });

  test("VM 對 VM 照實帶出方向", async () => {
    await submitEdge({
      sourceVmid: 101,
      targetVmid: 102,
      ports: [{ port: 5432, protocol: "tcp" }],
      direction: "bidirectional",
    });
    expect(createConnection.mock.calls[0][0].direction).toBe("bidirectional");
  });

  test("失敗時帶出後端訊息", async () => {
    createConnection.mockRejectedValue(new Error("nope"));
    const res = await submitEdge({ sourceVmid: 101, targetVmid: 102, ports: [], direction: "one_way" });
    expect(res.ok).toBe(false);
    expect(res.error.text).toBe("nope");
  });
});
