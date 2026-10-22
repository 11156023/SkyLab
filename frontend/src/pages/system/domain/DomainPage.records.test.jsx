// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  listZones: vi.fn(),
  listDnsRecords: vi.fn(),
  deleteDnsRecord: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/cloudflare", () => ({
  CloudflareService: {
    getConfig: mocks.getConfig,
    listZones: mocks.listZones,
    listDnsRecords: mocks.listDnsRecords,
    deleteDnsRecord: mocks.deleteDnsRecord,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));

const { default: DomainPage } = await import("./DomainPage");
const { withQueryClient } = await import("../../../queries/testing");

let root;
let host;

/* TanStack Query 以 macrotask 批次通知；zone 清單回來後紀錄才開始抓，所以多等兩輪 */
async function flush() {
  for (let round = 0; round < 3; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    });
  }
}

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

const record = (id, name) => ({ id, name, type: "A", content: "10.0.0.1", ttl: 1, proxied: false, managed_by_system: false });

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.getConfig.mockResolvedValue({ is_configured: true, account_id: "acc" });
  mocks.listZones.mockResolvedValue({
    items: [
      { id: "z1", name: "a.example.edu", status: "active" },
      { id: "z2", name: "b.example.edu", status: "active" },
    ],
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("DomainPage DNS records", () => {
  test("switching zones shows loading instead of the previous zone's records", async () => {
    const zoneB = deferred();
    mocks.listDnsRecords.mockImplementation((zoneId) => (
      zoneId === "z1" ? Promise.resolve({ items: [record("r1", "www.a.example.edu")] }) : zoneB.promise
    ));

    await act(async () => { root.render(withQueryClient(<DomainPage />)); });
    await flush();
    expect(host.textContent).toContain("www.a.example.edu");

    await act(async () => { buttonByText("b.example.edu").click(); });
    await flush();
    /* 這時若還顯示 A 的紀錄，按刪除會拿 A 的 record id 打到 B 的 zone */
    expect(host.textContent).not.toContain("www.a.example.edu");
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();

    await act(async () => { zoneB.resolve({ items: [record("r2", "www.b.example.edu")] }); });
    await flush();
    expect(host.textContent).toContain("www.b.example.edu");
    expect(mocks.listDnsRecords).toHaveBeenLastCalledWith("z2", { per_page: 100, search: undefined });
  });

  test("the search box only queries on Enter, and deleting a record re-queries with the typed keyword", async () => {
    mocks.listDnsRecords.mockResolvedValue({ items: [record("r1", "www.a.example.edu")] });
    mocks.confirm.mockResolvedValue(true);
    mocks.deleteDnsRecord.mockResolvedValue({});

    await act(async () => { root.render(withQueryClient(<DomainPage />)); });
    await flush();
    expect(mocks.listDnsRecords).toHaveBeenCalledTimes(1);

    const search = host.querySelector('input[placeholder="DomainPage.recordSearchPlaceholder"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(search, "www");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await flush();
    expect(mocks.listDnsRecords).toHaveBeenCalledTimes(1);

    await act(async () => { search.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
    await flush();
    expect(mocks.listDnsRecords).toHaveBeenLastCalledWith("z1", { per_page: 100, search: "www" });

    const calls = mocks.listDnsRecords.mock.calls.length;
    await act(async () => { host.querySelector('button[title="DomainPage.delete"]').click(); });
    await flush();
    expect(mocks.deleteDnsRecord).toHaveBeenCalledWith("z1", "r1");
    expect(mocks.listDnsRecords.mock.calls.length).toBeGreaterThan(calls);
    expect(mocks.listDnsRecords).toHaveBeenLastCalledWith("z1", { per_page: 100, search: "www" });
  });
});
