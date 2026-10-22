// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import { monitoringKeys } from "../../../queries/monitoring";

const mocks = vi.hoisted(() => ({
  listAlerts: vi.fn(),
  ackAlert: vi.fn(),
  /* 穩定參照：載入函式不依賴 t，但仍避免每次 render 換新 */
  t: (key, params) => (params?.name ? `${key}:${params.name}` : key),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/monitoring", () => ({
  MonitoringService: { listAlerts: mocks.listAlerts, ackAlert: mocks.ackAlert },
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/usePveOverview", () => ({ default: () => ({}) }));
vi.mock("../../../components/RrdChart/RrdChart", () => ({ default: () => null }));
vi.mock("./MiningIncidentsPanel", () => ({ default: () => null }));
vi.mock("./SystemHealthCard", () => ({ default: () => null }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const { AlertsCard } = await import("./MonitoringPage");

const BASE = {
  scope: "vm",
  metric: "cpu",
  value: 97,
  threshold: 90,
  message: "m",
  created_at: "2026-10-12T08:00:00Z",
  resolved_at: null,
  acknowledged_at: null,
};

let host;
let root;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

test("同號 VM 在不同叢集的告警各自標出叢集名稱", async () => {
  mocks.listAlerts.mockResolvedValue([
    { ...BASE, id: "a1", target: "100", connection_id: 1, connection_name: "機房A" },
    { ...BASE, id: "a2", target: "100", connection_id: 2, connection_name: "機房B" },
  ]);
  await act(async () => root.render(withQueryClient(<AlertsCard />)));
  await flush();

  expect(mocks.listAlerts).toHaveBeenCalledWith({ active: true });
  const text = host.textContent;
  expect(text).toContain("MonitoringPage.alertCluster:機房A");
  expect(text).toContain("MonitoringPage.alertCluster:機房B");
});

test("無法判定叢集（平台告警、舊資料）時不顯示叢集標籤", async () => {
  mocks.listAlerts.mockResolvedValue([
    { ...BASE, id: "s1", scope: "system", metric: "health", target: "component:redis", connection_id: null, connection_name: null },
  ]);
  await act(async () => root.render(withQueryClient(<AlertsCard />)));
  await flush();

  expect(host.textContent).toContain("component:redis");
  expect(host.textContent).not.toContain("MonitoringPage.alertCluster");
});

test("首次就抓不到告警時顯示空狀態並回報 0 筆，不卡在載入中", async () => {
  mocks.listAlerts.mockRejectedValue(new Error("503"));
  const onCountChange = vi.fn();
  await act(async () => root.render(withQueryClient(<AlertsCard onCountChange={onCountChange} />)));
  await flush();

  expect(host.textContent).toContain("MonitoringPage.emptyNoAlerts");
  expect(onCountChange).toHaveBeenLastCalledWith(0);
});

test("背景刷新失敗沿用上一份清單", async () => {
  mocks.listAlerts
    .mockResolvedValueOnce([{ ...BASE, id: "a1", target: "vm-keep" }])
    .mockRejectedValueOnce(new Error("503"));
  const client = createTestQueryClient();
  await act(async () => root.render(withQueryClient(<AlertsCard />, client)));
  await flush();

  await act(async () => { await client.refetchQueries({ queryKey: monitoringKeys.alerts({ active: true }) }); });
  await flush();

  expect(mocks.listAlerts).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("vm-keep");
});

test("確認告警後重抓清單，按鈕在重抓完成前保持停用", async () => {
  let resolveReload;
  mocks.listAlerts
    .mockResolvedValueOnce([{ ...BASE, id: "a1", target: "vm-ack" }])
    .mockImplementationOnce(() => new Promise((resolve) => { resolveReload = resolve; }));
  mocks.ackAlert.mockResolvedValue({});
  await act(async () => root.render(withQueryClient(<AlertsCard />)));
  await flush();

  const ackButton = host.querySelector("button");
  await act(async () => ackButton.click());
  await flush();
  expect(mocks.ackAlert).toHaveBeenCalledWith("a1");
  expect(mocks.listAlerts).toHaveBeenCalledTimes(2);
  expect(host.querySelector("button").disabled).toBe(true);

  await act(async () => resolveReload([{ ...BASE, id: "a1", target: "vm-ack", acknowledged_at: "2026-10-12T09:00:00Z" }]));
  await flush();
  expect(host.textContent).toContain("MonitoringPage.acknowledged");
  expect(host.querySelector("button")).toBeNull();
});
