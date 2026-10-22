// @vitest-environment happy-dom
/* 搜尋找不到時的空狀態是「沒有符合的 GPU」，不是「尚未偵測到 GPU」（那句只給真的沒有 GPU 的叢集） */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { withQueryClient } from "../../../queries/testing";
import GpuMgmtPage from "./GpuMgmtPage";

const mocks = vi.hoisted(() => ({
  listMappings: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gpu", () => ({ GpuService: { listMappings: mocks.listMappings, deleteMapping: vi.fn() } }));
vi.mock("react-i18next", async (importOriginal) => ({ ...await importOriginal(), useTranslation: () => ({ t: mocks.t }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/EmptyState/EmptyState", () => ({ default: ({ title }) => <div data-testid="empty">{title}</div> }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  mocks.listMappings.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const MAPPING = { id: "a100-lab", description: "A100", maps: [{ node: "pve-01", path: "0000:41:00" }], device_count: 2, used_count: 0 };

test("a search with no match says nothing matches", async () => {
  mocks.listMappings.mockResolvedValue({ data: [MAPPING] });
  await act(async () => { root.render(withQueryClient(<GpuMgmtPage />)); });
  await flush();
  const search = host.querySelector('input[placeholder="GpuMgmtPage.searchPlaceholder"]');
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(search, "h200");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(host.querySelector('[data-testid="empty"]').textContent).toBe("GpuMgmtPage.emptyFilteredTitle");
});

test("a cluster without GPUs still says none were detected", async () => {
  mocks.listMappings.mockResolvedValue({ data: [] });
  await act(async () => { root.render(withQueryClient(<GpuMgmtPage />)); });
  await flush();
  expect(host.querySelector('[data-testid="empty"]').textContent).toBe("GpuMgmtPage.emptyTitle");
});
