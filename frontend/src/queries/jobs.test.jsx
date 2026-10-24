// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  JOB_DETAIL_POLL_INTERVAL,
  JOB_DETAIL_RETRY_INTERVAL,
  jobDetailPollInterval,
  jobKeys,
  useJobCacheUpdater,
  useJobDetailQuery,
  useJobsQuery,
} from "./jobs";

const mocks = vi.hoisted(() => ({ list: vi.fn(), detail: vi.fn() }));
vi.mock("../services/jobs", () => ({ JobsService: { list: mocks.list, detail: mocks.detail } }));

describe("jobKeys", () => {
  test("parameterised keys stay distinct per filter and stringify job ids", () => {
    expect(jobKeys.list(undefined)).toEqual(["jobs", "list", {}]);
    expect(jobKeys.list({ limit: 200 })).not.toEqual(jobKeys.list({ limit: 200, kinds: ["deletion"] }));
    expect(jobKeys.detail("vm_request:1")).toEqual(["jobs", "detail", "vm_request:1"]);
  });
});

describe("jobDetailPollInterval", () => {
  const state = (status, jobStatus) => ({ status, data: jobStatus ? { item: { status: jobStatus } } : undefined });

  test("polls every 3 seconds while the job is still active", () => {
    expect(jobDetailPollInterval(state("success", "running"))).toBe(JOB_DETAIL_POLL_INTERVAL);
    expect(jobDetailPollInterval(state("success", "pending"))).toBe(JOB_DETAIL_POLL_INTERVAL);
    expect(jobDetailPollInterval(state("success", "blocked"))).toBe(JOB_DETAIL_POLL_INTERVAL);
  });

  test("slows down to 6 seconds after a failed background refresh", () => {
    expect(jobDetailPollInterval(state("error", "running"))).toBe(JOB_DETAIL_RETRY_INTERVAL);
  });

  test("stops once the job is terminal or when the first load failed", () => {
    expect(jobDetailPollInterval(state("success", "completed"))).toBe(false);
    expect(jobDetailPollInterval(state("success", "failed"))).toBe(false);
    expect(jobDetailPollInterval(state("error", undefined))).toBe(false);
    expect(jobDetailPollInterval(undefined)).toBe(false);
  });
});

let host;
let root;
let seen;

function ListProbe({ params, options }) {
  const query = useJobsQuery(params, options);
  const updater = useJobCacheUpdater();
  seen = { data: query.data, isError: query.isError, updater };
  return null;
}

function DetailProbe({ jobId, options }) {
  const query = useJobDetailQuery(jobId, options);
  seen = { data: query.data, fetchStatus: query.fetchStatus };
  return null;
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("useJobsQuery", () => {
  test("passes the filter straight to JobsService.list", async () => {
    mocks.list.mockResolvedValue({ items: [{ id: "a" }] });
    const params = { statuses: ["failed"], historyDays: 7, limit: 50 };
    await act(async () => { root.render(withQueryClient(<ListProbe params={params} />)); });
    await flush();

    expect(mocks.list).toHaveBeenCalledWith(params);
    expect(seen.data).toEqual({ items: [{ id: "a" }] });
  });

  test("invalidate() refetches every jobs list", async () => {
    mocks.list.mockResolvedValue({ items: [] });
    await act(async () => { root.render(withQueryClient(<ListProbe params={{ limit: 200 }} />)); });
    await flush();

    await act(async () => { await seen.updater.invalidate(); });
    await flush();
    expect(mocks.list).toHaveBeenCalledTimes(2);
  });

  test("refetchOnWindowFocus is only set when the caller asks for it", async () => {
    mocks.list.mockResolvedValue({ items: [] });
    const client = createTestQueryClient();
    await act(async () => {
      root.render(withQueryClient(<ListProbe params={{ limit: 1 }} options={{ refetchOnWindowFocus: false }} />, client));
    });
    await flush();
    const observer = client.getQueryCache().find({ queryKey: jobKeys.list({ limit: 1 }) }).observers[0];
    expect(observer.options.refetchOnWindowFocus).toBe(false);

    await act(async () => { root.render(withQueryClient(<ListProbe params={{ limit: 2 }} />, client)); });
    await flush();
    const plain = client.getQueryCache().find({ queryKey: jobKeys.list({ limit: 2 }) }).observers[0];
    /* 沒給就沿用 QueryClient 預設（測試用 client 是 false），不會被 undefined 蓋掉 */
    expect(plain.options.refetchOnWindowFocus).toBe(false);
  });
});

describe("useJobDetailQuery", () => {
  test("does not fetch while disabled", async () => {
    await act(async () => { root.render(withQueryClient(<DetailProbe jobId="a" options={{ enabled: false }} />)); });
    await flush();
    expect(mocks.detail).not.toHaveBeenCalled();
  });

  test("does not fetch without a job id", async () => {
    await act(async () => { root.render(withQueryClient(<DetailProbe jobId={null} />)); });
    await flush();
    expect(mocks.detail).not.toHaveBeenCalled();
  });

  test("loads the detail by id", async () => {
    mocks.detail.mockResolvedValue({ item: { id: "deletion:9", status: "completed" } });
    await act(async () => { root.render(withQueryClient(<DetailProbe jobId="deletion:9" />)); });
    await flush();
    expect(mocks.detail).toHaveBeenCalledWith("deletion:9");
    expect(seen.data.item.status).toBe("completed");
  });
});
