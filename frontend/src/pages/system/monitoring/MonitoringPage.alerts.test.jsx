// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

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
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
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
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

test("同號 VM 在不同叢集的告警各自標出叢集名稱", async () => {
  mocks.listAlerts.mockResolvedValue([
    { ...BASE, id: "a1", target: "100", connection_id: 1, connection_name: "機房A" },
    { ...BASE, id: "a2", target: "100", connection_id: 2, connection_name: "機房B" },
  ]);
  await act(async () => root.render(<AlertsCard />));
  await flush();

  const text = host.textContent;
  expect(text).toContain("MonitoringPage.alertCluster:機房A");
  expect(text).toContain("MonitoringPage.alertCluster:機房B");
});

test("無法判定叢集（平台告警、舊資料）時不顯示叢集標籤", async () => {
  mocks.listAlerts.mockResolvedValue([
    { ...BASE, id: "s1", scope: "system", metric: "health", target: "component:redis", connection_id: null, connection_name: null },
  ]);
  await act(async () => root.render(<AlertsCard />));
  await flush();

  expect(host.textContent).toContain("component:redis");
  expect(host.textContent).not.toContain("MonitoringPage.alertCluster");
});
