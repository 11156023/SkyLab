// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("../services/resources", () => ({
  ResourcesService: {
    list: vi.fn(),
    sessionStatus: vi.fn(),
    mySessionStatuses: vi.fn(),
  },
}));

import { ResourcesService } from "../services/resources";
import { createTestQueryClient, withQueryClient } from "../queries/testing";
import useSessionWarning from "./useSessionWarning";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
let client;

const warnings = [
  {
    vmid: 101,
    should_warn: true,
    warn_reason: "auto_stop",
    auto_stop_at: "2026-10-01T10:00:00Z",
  },
  {
    vmid: 102,
    should_warn: true,
    warn_reason: "expiry",
    expiry_at: "2026-10-02T00:00:00Z",
  },
];

function Harness({ userId, onValue }) {
  const value = useSessionWarning(userId);
  useEffect(() => onValue(value), [onValue, value]);
  return null;
}

// TanStack Query notifies on a zero-delay timer: wait one tick, then drain microtasks.
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function renderHarness(userId, onValue) {
  return act(async () => root.render(withQueryClient(<Harness userId={userId} onValue={onValue} />, client)));
}

beforeEach(() => {
  localStorage.clear();
  client = createTestQueryClient();
  ResourcesService.list.mockReset();
  ResourcesService.sessionStatus.mockReset();
  ResourcesService.mySessionStatuses.mockReset();
  ResourcesService.mySessionStatuses.mockResolvedValue(warnings);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

test("loads all owned machine statuses with one batch request", async () => {
  let latest;
  const onValue = (value) => { latest = value; };
  await renderHarness("user-1", onValue);
  await flush();

  expect(ResourcesService.mySessionStatuses).toHaveBeenCalledTimes(1);
  expect(ResourcesService.list).not.toHaveBeenCalled();
  expect(ResourcesService.sessionStatus).not.toHaveBeenCalled();
  expect(latest.active?.vmid).toBe(101);
});

test("shows machines individually and records each warning as soon as it is displayed", async () => {
  let latest;
  const onValue = (value) => { latest = value; };
  await renderHarness("user-1", onValue);
  await flush();

  expect(latest.active?.vmid).toBe(101);
  await act(async () => latest.dismiss());
  expect(latest.active?.vmid).toBe(102);

  const stored = JSON.parse(localStorage.getItem("skylab:session-warnings:v2:user-1"));
  expect(stored).toEqual([
    "101:auto_stop:2026-10-01T10:00:00Z",
    "102:expiry:2026-10-02T00:00:00Z",
  ]);
});

test("does not repeat an event after remount but keeps acknowledgement isolated by user", async () => {
  let latest;
  const onValue = (value) => { latest = value; };
  await renderHarness("user-1", onValue);
  await flush();
  expect(latest.active?.vmid).toBe(101);

  await act(async () => root.unmount());
  root = createRoot(host);
  await renderHarness("user-1", onValue);
  await flush();
  expect(latest.active?.vmid).toBe(102);

  await renderHarness("user-2", onValue);
  await flush();
  expect(latest.active?.vmid).toBe(101);
});

test("does not poll before a user is signed in", async () => {
  let latest;
  const onValue = (value) => { latest = value; };
  await renderHarness(null, onValue);
  await flush();

  expect(ResourcesService.mySessionStatuses).not.toHaveBeenCalled();
  expect(latest.active).toBeNull();
});

test("a failed poll keeps the current warning and the next poll retries", async () => {
  let latest;
  const onValue = (value) => { latest = value; };
  await renderHarness("user-1", onValue);
  await flush();
  expect(latest.active?.vmid).toBe(101);

  ResourcesService.mySessionStatuses.mockRejectedValueOnce(new Error("503"));
  await act(async () => { await client.refetchQueries(); });
  await flush();
  expect(latest.active?.vmid).toBe(101);

  await act(async () => { await client.refetchQueries(); });
  await flush();
  expect(ResourcesService.mySessionStatuses).toHaveBeenCalledTimes(3);
  expect(latest.active?.vmid).toBe(101);
});
