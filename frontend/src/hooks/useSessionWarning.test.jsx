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
import useSessionWarning from "./useSessionWarning";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

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

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  localStorage.clear();
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
  await act(async () => root.render(<Harness userId="user-1" onValue={onValue} />));
  await flush();

  expect(ResourcesService.mySessionStatuses).toHaveBeenCalledTimes(1);
  expect(ResourcesService.list).not.toHaveBeenCalled();
  expect(ResourcesService.sessionStatus).not.toHaveBeenCalled();
  expect(latest.active?.vmid).toBe(101);
});

test("shows machines individually and records each warning as soon as it is displayed", async () => {
  let latest;
  const onValue = (value) => { latest = value; };
  await act(async () => root.render(<Harness userId="user-1" onValue={onValue} />));
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
  await act(async () => root.render(<Harness userId="user-1" onValue={onValue} />));
  await flush();
  expect(latest.active?.vmid).toBe(101);

  await act(async () => root.unmount());
  root = createRoot(host);
  await act(async () => root.render(<Harness userId="user-1" onValue={onValue} />));
  await flush();
  expect(latest.active?.vmid).toBe(102);

  await act(async () => root.render(<Harness userId="user-2" onValue={onValue} />));
  await flush();
  expect(latest.active?.vmid).toBe(101);
});
