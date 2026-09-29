// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const { service, i18n } = vi.hoisted(() => ({
  service: { getTemplate: vi.fn(), launch: vi.fn() },
  i18n: {
    t: (key, options) => (options ? `${key}:${JSON.stringify(options)}` : key),
    i18n: { language: "zh-TW" },
  },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => i18n }));
vi.mock("../../../services/quickPractice", () => ({ QuickPracticeService: service }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));

import QuickTemplateFormPage from "./QuickTemplateFormPage";
import { nodeMemoryGb } from "./templateFolder";

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("converts a node's memory from MB without rounding to whole GB", () => {
  expect(nodeMemoryGb({ memory_mb: 512 })).toBe(0.5);
  expect(nodeMemoryGb({ memory_mb: 2048 })).toBe(2);
});

it("shows sub-GB machines and their total as they will actually be allocated", async () => {
  /* 服務層把 memory_mb 四捨五入成整數 GB 的 memory（最小 1），畫面不能拿它來顯示或加總 */
  const nodes = [1, 2, 3].map((n) => ({
    id: `n${n}`, name: `node${n}`, role: "web", type: "lxc", cpu: 1, memory_mb: 512, memory: 1, disk: 8,
  }));
  service.getTemplate.mockResolvedValue({ id: "7", name: "Tiny", nodes, duration_hours: 2, version: 1 });
  await act(async () => root.render(
    <MemoryRouter initialEntries={["/quick/7"]}>
      <Routes><Route path="/quick/:id" element={<QuickTemplateFormPage />} /></Routes>
    </MemoryRouter>,
  ));
  await act(async () => {});
  expect(host.textContent).toContain('QuickTemplateFormPage.machineSpec:{"cpu":1,"memory":0.5,"disk":8}');
  expect(host.textContent).toContain('QuickTemplateFormPage.environmentTotalSpec:{"cpu":3,"memory":1.5,"disk":24}');
});
