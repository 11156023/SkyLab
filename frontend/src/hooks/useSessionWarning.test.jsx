// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("../services/resources", () => ({
  ResourcesService: { list: vi.fn(), sessionStatus: vi.fn() },
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
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

test("只輪詢自己在跑的機器，老師看得到的學生機器與別人分享的機器不輪詢也不跳警告", async () => {
  ResourcesService.list.mockResolvedValue([
    { vmid: 101, status: "running" }, // 舊資料沒帶 access_role，視為自己的
    { vmid: 102, status: "running", access_role: "class_member" },
    { vmid: 201, status: "running", access_role: "class_teacher" },
    { vmid: 301, status: "running", access_role: "shared" },
    { vmid: 103, status: "stopped", access_role: "owner" },
  ]);
  ResourcesService.sessionStatus.mockImplementation(async (vmid) => ({
    vmid,
    should_warn: vmid === 201,
    auto_stop_at: "2026-09-27T10:00:00Z",
  }));

  await act(async () => root.render(<Probe />));
  await act(async () => {});

  const polled = ResourcesService.sessionStatus.mock.calls.map(([vmid]) => vmid);
  expect(polled.sort()).toEqual([101, 102]);
  expect(hook.active).toBeNull();
});
