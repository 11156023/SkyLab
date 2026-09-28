/**
 * resources.batch.test.js
 * 驗證分批批次操作：整班一次送會超過後端 100 台上限與代理逾時，
 * 必須拆成小批依序送，而且某一批失敗不能讓其餘批次停下。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { BATCH_CHUNK_SIZE, ResourcesService } from "./resources";

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

function okFor(vmids) {
  return jsonRes(200, {
    total: vmids.length,
    succeeded: vmids.length,
    failed: 0,
    results: vmids.map((vmid) => ({ vmid, success: true, message: "ok" })),
  });
}

let fetchMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  fetchMock = vi.fn(async (_url, init) => okFor(JSON.parse(init.body).vmids));
  vi.stubGlobal("fetch", fetchMock);
});

const range = (count) => Array.from({ length: count }, (_, index) => 1000 + index);

describe("ResourcesService.batchActionInChunks", () => {
  test("整班 102 台拆成每批不超過上限的請求", async () => {
    const vmids = range(102);

    const result = await ResourcesService.batchActionInChunks(vmids, "start");

    const sent = fetchMock.mock.calls.map(([, init]) => JSON.parse(init.body));
    expect(sent).toHaveLength(Math.ceil(102 / BATCH_CHUNK_SIZE));
    expect(sent.every((body) => body.vmids.length <= BATCH_CHUNK_SIZE)).toBe(true);
    expect(sent.every((body) => body.action === "start")).toBe(true);
    expect(sent.flatMap((body) => body.vmids)).toEqual(vmids);
    expect(result).toMatchObject({ total: 102, succeeded: 102, failed: 0 });
  });

  test("某一批請求失敗只算那批失敗，後面照送", async () => {
    fetchMock
      .mockImplementationOnce(async (_url, init) => okFor(JSON.parse(init.body).vmids))
      .mockImplementationOnce(async () => jsonRes(504, { detail: "timeout" }));

    const result = await ResourcesService.batchActionInChunks(range(25), "shutdown", { chunkSize: 10 });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(result).toMatchObject({ total: 25, succeeded: 15, failed: 10 });
    expect(result.results.filter((item) => !item.success).map((item) => item.vmid)).toEqual(range(25).slice(10, 20));
  });

  test("每送完一批回報進度", async () => {
    const progress = [];

    await ResourcesService.batchActionInChunks(range(12), "start", {
      chunkSize: 5,
      onProgress: (value) => progress.push(value),
    });

    expect(progress).toEqual([
      { done: 5, total: 12 },
      { done: 10, total: 12 },
      { done: 12, total: 12 },
    ]);
  });
});
