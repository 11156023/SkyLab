// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AiMonitoringPage from "./AiMonitoringPage";

const mocks = vi.hoisted(() => ({
  overview: vi.fn(),
  runtime: vi.fn(),
  listProxyCalls: vi.fn(),
  listTemplateCalls: vi.fn(),
  listUsersUsage: vi.fn(),
  createGrafanaSession: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/aiMonitoring", () => ({
  AiMonitoringService: {
    overview: mocks.overview,
    runtime: mocks.runtime,
    listProxyCalls: mocks.listProxyCalls,
    listTemplateCalls: mocks.listTemplateCalls,
    listUsersUsage: mocks.listUsersUsage,
  },
}));
vi.mock("../../../services/monitoring", () => ({
  MonitoringService: { createGrafanaSession: mocks.createGrafanaSession },
}));
vi.mock("../../../i18n", () => ({ default: { language: "en" } }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({
  default: ({ options = [], value, onChange, ariaLabel }) => (
    <div data-group={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          data-value={option.value}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  ),
}));

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function overviewWith(totalCalls) {
  return {
    summary: { total_calls: totalCalls, total_tokens: 0, error_rate: 0, failed_calls: 0 },
    series: [],
    model_breakdown: [],
  };
}

let root;
let host;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.runtime.mockResolvedValue({ gateway: { status: "available", readiness: true }, models: [], summary: {} });
  mocks.listProxyCalls.mockResolvedValue({ data: [], count: 0 });
  mocks.listTemplateCalls.mockResolvedValue({ data: [], count: 0 });
  mocks.listUsersUsage.mockResolvedValue({ data: [], count: 0 });
  mocks.createGrafanaSession.mockResolvedValue({ enabled: false });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("AiMonitoringPage", () => {
  test("a slow response for the previous range does not overwrite the new range", async () => {
    const first = deferred();
    const second = deferred();
    mocks.overview
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    await act(async () => { root.render(<AiMonitoringPage />); });
    await flush();
    const ninety = host.querySelector('[data-group="AiMonitoringPage.rangeLabel"] [data-value="90d"]');
    await act(async () => { ninety.click(); });
    await flush();
    expect(mocks.overview).toHaveBeenCalledTimes(2);

    await act(async () => { second.resolve(overviewWith(9090)); });
    await flush();
    await act(async () => { first.resolve(overviewWith(7777)); });
    await flush();

    expect(host.textContent).toContain("9,090");
    expect(host.textContent).not.toContain("7,777");
  });

  test("AI Gateway readiness alert opens the models tab on this page", async () => {
    mocks.overview.mockResolvedValue(overviewWith(1));
    mocks.runtime.mockRejectedValue(new Error("gateway down"));

    await act(async () => { root.render(<AiMonitoringPage />); });
    await flush();

    const proxyTab = [...host.querySelectorAll('[role="tab"]')].find((b) => b.textContent.includes("AiMonitoringPage.tabProxy"));
    await act(async () => { proxyTab.click(); });
    expect(proxyTab.getAttribute("aria-selected")).toBe("true");

    const alertRow = [...host.querySelectorAll("button")].find((b) => b.textContent.includes("AiMonitoringPage.attentionGatewayTitle"));
    expect(alertRow).toBeTruthy();
    await act(async () => { alertRow.click(); });

    const modelsTab = [...host.querySelectorAll('[role="tab"]')].find((b) => b.textContent.includes("AiMonitoringPage.tabModels"));
    expect(modelsTab.getAttribute("aria-selected")).toBe("true");
  });
});
