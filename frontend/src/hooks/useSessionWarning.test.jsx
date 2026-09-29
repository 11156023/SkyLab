// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("../services/resources", () => ({
  ResourcesService: { list: vi.fn(), sessionStatus: vi.fn(), mySessionStatuses: vi.fn() },
}));

import { ResourcesService } from "../services/resources";
import useSessionWarning from "./useSessionWarning";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
let hook;

function Probe() {
  hook = useSessionWarning();
  return null;
}

beforeEach(() => {
  localStorage.clear();
  ResourcesService.list.mockReset();
  ResourcesService.sessionStatus.mockReset();
  ResourcesService.mySessionStatuses.mockReset();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

test("一次向後端取回本人所有機器的狀態，不再逐台輪詢", async () => {
  // 「只算自己的機器」由後端依 user_id 篩（老師的學生機、別人分享的機器不會回來）
  ResourcesService.mySessionStatuses.mockResolvedValue([
    { vmid: 101, should_warn: false, auto_stop_at: null },
    { vmid: 102, should_warn: true, auto_stop_at: "2026-09-27T10:00:00Z" },
  ]);

  await act(async () => root.render(<Probe />));
  await act(async () => {});

  expect(ResourcesService.mySessionStatuses).toHaveBeenCalledTimes(1);
  expect(ResourcesService.list).not.toHaveBeenCalled();
  expect(ResourcesService.sessionStatus).not.toHaveBeenCalled();
  expect(hook.active?.vmid).toBe(102);
});

test("稍後再說只隱藏這一次的警告", async () => {
  ResourcesService.mySessionStatuses.mockResolvedValue([
    { vmid: 102, should_warn: true, auto_stop_at: "2026-09-27T10:00:00Z" },
  ]);

  await act(async () => root.render(<Probe />));
  await act(async () => {});
  await act(async () => hook.dismiss());

  expect(hook.active).toBeNull();
});
