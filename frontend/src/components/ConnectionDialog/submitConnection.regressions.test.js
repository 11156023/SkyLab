/**
 * 多筆發布途中失敗後的重送：已成功的那幾筆要回報出來並從表單拿掉，
 * 否則重送會先撞上後端「此 port 已發布」而到不了修好的那一列。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../../services/firewall", () => ({
  createConnection: vi.fn(),
  createVmRule: vi.fn(),
  publishService: vi.fn(),
}));

import { createConnection, publishService } from "../../services/firewall";
import { buildInboundPayload, removePublishedRows } from "./connectionPayload";
import { submitInbound } from "./submitConnection";

beforeEach(() => {
  vi.clearAllMocks();
  createConnection.mockResolvedValue({});
  publishService.mockResolvedValue({});
});

const fwRows = [
  { id: 1, port: "80", protocol: "tcp" },
  { id: 2, port: "8080", protocol: "tcp" },
  { id: 3, port: "", protocol: "icmp" },
];

describe("partial publish failure then retry", () => {
  test("reports which rows were published", async () => {
    publishService.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error("502"));
    const built = buildInboundPayload({ mode: "firewall_only", firewallRows: fwRows });
    const res = await submitInbound({ vmid: 101, publish: built.publish, raw: built.raw });
    expect(res.ok).toBe(false);
    expect(res.partialDone).toBe(1);
    expect(res.published).toEqual(["80/tcp"]);
    expect(createConnection).not.toHaveBeenCalled();
  });

  test("retrying with the pruned rows only publishes what is left", async () => {
    publishService.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error("502"));
    const first = buildInboundPayload({ mode: "firewall_only", firewallRows: fwRows });
    const res = await submitInbound({ vmid: 101, publish: first.publish, raw: first.raw });

    const left = removePublishedRows(fwRows, res.published, "port");
    expect(left.map((r) => r.id)).toEqual([2, 3]);

    publishService.mockClear();
    publishService.mockResolvedValue({});
    const retry = buildInboundPayload({ mode: "firewall_only", firewallRows: left });
    const again = await submitInbound({ vmid: 101, publish: retry.publish, raw: retry.raw });
    expect(publishService).toHaveBeenCalledTimes(1);
    expect(publishService).toHaveBeenCalledWith(101, { port: 8080, protocol: "tcp", mode: "firewall_only" });
    expect(again.ok).toBe(true);
  });

  test("when only the raw ICMP rule failed, the retry only creates the connection", async () => {
    createConnection.mockRejectedValueOnce(new Error("conn failed"));
    const first = buildInboundPayload({ mode: "firewall_only", firewallRows: fwRows });
    const res = await submitInbound({ vmid: 101, publish: first.publish, raw: first.raw });
    expect(res.published).toEqual(["80/tcp", "8080/tcp"]);

    const left = removePublishedRows(fwRows, res.published, "port");
    expect(left.map((r) => r.id)).toEqual([3]);

    publishService.mockClear();
    const retry = buildInboundPayload({ mode: "firewall_only", firewallRows: left });
    const again = await submitInbound({ vmid: 101, publish: retry.publish, raw: retry.raw });
    expect(publishService).not.toHaveBeenCalled();
    expect(createConnection).toHaveBeenCalledTimes(2);
    expect(again.ok).toBe(true);
  });

  test("port_forward rows are matched on internal port and protocol, once per key", () => {
    const rows = [
      { id: 1, externalPort: "10080", internalPort: "80", protocol: "tcp" },
      { id: 2, externalPort: "10081", internalPort: "80", protocol: "udp" },
      { id: 3, externalPort: "10082", internalPort: "80", protocol: "tcp" },
    ];
    expect(removePublishedRows(rows, ["80/tcp"], "internalPort").map((r) => r.id)).toEqual([2, 3]);
  });
});
