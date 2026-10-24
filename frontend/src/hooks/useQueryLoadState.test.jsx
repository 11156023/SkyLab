// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../queries/testing";
import { useLoadFailureMessage, useQueryView } from "./useQueryLoadState";

let host;
let root;
let client;
let seen;
const fetcher = vi.fn();

function Probe({ filter, keepPrevious = false }) {
  const query = useQuery({
    queryKey: ["probe", filter],
    queryFn: () => fetcher(filter),
    ...(keepPrevious && { placeholderData: keepPreviousData }),
  });
  const view = useQueryView(query);
  seen = { data: query.data, ...view };
  return null;
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function render(ui) {
  await act(async () => { root.render(withQueryClient(ui, client)); });
  await flush();
}

beforeEach(() => {
  fetcher.mockReset();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("useQueryView", () => {
  test("loading → data, refreshing only while a background fetch runs with data", async () => {
    fetcher.mockResolvedValue(["a"]);
    await render(<Probe filter="all" />);
    expect(seen).toMatchObject({ data: ["a"], loading: false, error: null, refreshing: false });

    let resolve;
    fetcher.mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    act(() => { client.refetchQueries({ queryKey: ["probe", "all"] }); });
    await flush();
    expect(seen).toMatchObject({ data: ["a"], loading: false, refreshing: true });
    await act(async () => resolve(["b"]));
    await flush();
    expect(seen).toMatchObject({ data: ["b"], refreshing: false });
  });

  test("a background refetch failure keeps the old data instead of the error screen", async () => {
    fetcher.mockResolvedValueOnce(["a"]).mockRejectedValueOnce(new Error("down"));
    await render(<Probe filter="all" />);
    await act(async () => { await client.refetchQueries({ queryKey: ["probe", "all"] }); });
    await flush();
    expect(seen).toMatchObject({ data: ["a"], loading: false, error: null });
  });

  test("the error screen stays up during a background retry instead of flashing to loading", async () => {
    fetcher.mockRejectedValueOnce(new Error("down"));
    await render(<Probe filter="all" />);
    expect(seen.error?.message).toBe("down");

    let reject;
    fetcher.mockImplementationOnce(() => new Promise((_r, rj) => { reject = rj; }));
    act(() => { client.refetchQueries({ queryKey: ["probe", "all"] }); });
    await flush();
    expect(seen).toMatchObject({ loading: false });
    expect(seen.error?.message).toBe("down");

    await act(async () => reject(new Error("still down")));
    await flush();
    expect(seen.error?.message).toBe("still down");
  });

  test("retry() returns to loading until the next result, and a repeated failure shows again", async () => {
    const same = new Error("down");
    fetcher.mockRejectedValue(same);
    await render(<Probe filter="all" />);
    expect(seen.error).toBe(same);

    act(() => { seen.retry(); });
    expect(seen.loading).toBe(true);
    expect(seen.error).toBeNull();
    await flush();
    expect(seen.error).toBe(same);
    expect(seen.loading).toBe(false);
  });

  test("a failure on a new key (filter change) shows the error even after failures on the old key", async () => {
    fetcher.mockRejectedValue(new Error("down"));
    await render(<Probe filter="all" />);
    await act(async () => { await client.refetchQueries({ queryKey: ["probe", "all"] }); });
    await flush();
    expect(seen.error?.message).toBe("down");

    fetcher.mockRejectedValue(new Error("filter down"));
    await render(<Probe filter="running" />);
    expect(seen.error?.message).toBe("filter down");
  });

  test("with keepPreviousData, a failed filter keeps its error screen during background retries instead of flashing the previous filter's rows", async () => {
    fetcher.mockResolvedValueOnce(["all-rows"]);
    await render(<Probe filter="all" keepPrevious />);
    expect(seen.data).toEqual(["all-rows"]);

    fetcher.mockRejectedValueOnce(new Error("filter down"));
    await render(<Probe filter="running" keepPrevious />);
    expect(seen.error?.message).toBe("filter down");

    /* 背景重試期間 data 會變回上一組的佔位資料；錯誤畫面要留著，不能換成上一組的清單 */
    let reject;
    fetcher.mockImplementationOnce(() => new Promise((_r, rj) => { reject = rj; }));
    act(() => { client.refetchQueries({ queryKey: ["probe", "running"] }); });
    await flush();
    expect(seen.error?.message).toBe("filter down");
    expect(seen.loading).toBe(false);

    await act(async () => reject(new Error("still down")));
    await flush();
    expect(seen.error?.message).toBe("still down");
  });
});

function MessageProbe() {
  const query = useQuery({ queryKey: ["probe-message"], queryFn: () => fetcher() });
  seen = { data: query.data, message: useLoadFailureMessage(query, "fallback") };
  return null;
}

describe("useLoadFailureMessage", () => {
  test("without data the message stays the same through background retries", async () => {
    fetcher.mockRejectedValueOnce(new Error("down"));
    await render(<MessageProbe />);
    expect(seen.message).toBe("down");

    let reject;
    fetcher.mockImplementationOnce(() => new Promise((_r, rj) => { reject = rj; }));
    act(() => { client.refetchQueries({ queryKey: ["probe-message"] }); });
    await flush();
    expect(seen.message).toBe("down");

    await act(async () => reject(new Error("down")));
    await flush();
    expect(seen.message).toBe("down");
  });

  test("with data a failing refetch reports once, and a success clears it", async () => {
    fetcher.mockResolvedValueOnce(["row"]);
    await render(<MessageProbe />);
    expect(seen.message).toBeNull();

    fetcher.mockRejectedValueOnce(new Error("refresh failed"));
    await act(async () => { await client.refetchQueries({ queryKey: ["probe-message"] }); });
    await flush();
    expect(seen.data).toEqual(["row"]);
    expect(seen.message).toBe("refresh failed");

    let reject;
    fetcher.mockImplementationOnce(() => new Promise((_r, rj) => { reject = rj; }));
    act(() => { client.refetchQueries({ queryKey: ["probe-message"] }); });
    await flush();
    expect(seen.message).toBe("refresh failed");
    await act(async () => reject(new Error("refresh failed")));
    await flush();
    expect(seen.message).toBe("refresh failed");

    fetcher.mockResolvedValueOnce(["row", "row2"]);
    await act(async () => { await client.refetchQueries({ queryKey: ["probe-message"] }); });
    await flush();
    expect(seen.message).toBeNull();
  });

  test("an error without a message falls back", async () => {
    fetcher.mockRejectedValueOnce({ status: 500 });
    await render(<MessageProbe />);
    expect(seen.message).toBe("fallback");
  });
});
