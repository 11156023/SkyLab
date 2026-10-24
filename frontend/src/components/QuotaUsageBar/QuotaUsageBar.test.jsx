// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getMyUsage: vi.fn() }));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: (key) => key }),
}));
vi.mock("../../services/quotas", () => ({ QuotasService: { getMyUsage: mocks.getMyUsage } }));

import QuotaUsageBar from "./QuotaUsageBar";
import { createTestQueryClient, withQueryClient } from "../../queries/testing";
import { quotaKeys } from "../../queries/quotas";

const usage = {
  used_cpu_cores: 2, used_memory_mb: 2048, used_disk_gb: 20, used_instances: 1,
  quota: { max_cpu_cores: 8, max_memory_mb: 8192, max_disk_gb: 100, max_instances: 4 },
};

let host;
let root;
let client;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function render() {
  await act(async () => root.render(withQueryClient(<MemoryRouter><QuotaUsageBar /></MemoryRouter>, client)));
  await flush();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

test("QuotaUsageBar is exported from components/QuotaUsageBar", () => {
  expect(typeof QuotaUsageBar).toBe("function");
});

test("renders nothing until the usage arrives, then the meters", async () => {
  let resolveUsage;
  mocks.getMyUsage.mockImplementationOnce(() => new Promise((resolve) => { resolveUsage = resolve; }));
  await render();
  expect(host.innerHTML).toBe("");

  await act(async () => resolveUsage(usage));
  await flush();
  expect(host.textContent).toContain("QuotaUsageBar.title");
  expect(host.textContent).toContain("QuotaUsageBar.usedOfMax");
  expect(host.textContent).not.toContain("Error.generic");
});

test("a background refresh failure keeps the last numbers", async () => {
  mocks.getMyUsage.mockResolvedValueOnce(usage).mockRejectedValueOnce(new Error("503"));
  await render();
  await act(async () => { await client.refetchQueries({ queryKey: quotaKeys.myUsage }); });
  await flush();

  expect(mocks.getMyUsage).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("QuotaUsageBar.usedOfMax");
  expect(host.textContent).not.toContain("Error.generic");
});

test("a first-load failure shows the placeholder and keeps it while the next poll retries", async () => {
  let resolveRetry;
  mocks.getMyUsage
    .mockRejectedValueOnce(new Error("503"))
    .mockImplementationOnce(() => new Promise((resolve) => { resolveRetry = resolve; }));
  await render();
  expect(host.textContent).toContain("Error.generic");

  act(() => { client.refetchQueries({ queryKey: quotaKeys.myUsage }); });
  await flush();
  expect(host.textContent).toContain("Error.generic");

  await act(async () => resolveRetry(usage));
  await flush();
  expect(host.textContent).toContain("QuotaUsageBar.usedOfMax");
  expect(host.textContent).not.toContain("Error.generic");
});
