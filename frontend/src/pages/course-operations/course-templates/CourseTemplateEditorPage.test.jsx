// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import CourseTemplateEditorPage from "./CourseTemplateEditorPage";

const mocks = vi.hoisted(() => ({
  saveDraft: vi.fn(), publish: vi.fn(), get: vi.fn(), setVisibility: vi.fn(), confirm: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
  t: (key) => key,
}));
vi.mock("../../../services/courseEnvironments", () => ({
  courseNodeHasUsableSource: () => true,
  CourseEnvironmentsService: { saveDraft: mocks.saveDraft, publish: mocks.publish, get: mocks.get, setVisibility: mocks.setVisibility },
}));
vi.mock("../../../services/teachingClasses", () => ({ TeachingClassesService: { list: async () => [] } }));
vi.mock("../../../services/templates", () => ({ TemplatesService: { list: async () => [] } }));
vi.mock("../../../services/api", () => ({ apiGet: async () => [] }));
vi.mock("../../../services/auth", () => ({ AuthStorage: { getSnapshot: () => ({ sessionId: "test" }) } }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("react-i18next", async (importOriginal) => ({ ...await importOriginal(), useTranslation: () => ({ t: mocks.t }) }));
vi.mock("@xyflow/react", async () => {
  const { useState } = await import("react");
  return {
    ReactFlow: ({ children }) => <div>{children}</div>,
    Panel: ({ children }) => <div>{children}</div>, Background: () => null, Handle: () => null,
    Position: { Left: "left", Right: "right" }, useNodesState: (nodes) => [...useState(nodes), () => {}],
    BaseEdge: () => null, EdgeLabelRenderer: ({ children }) => <div>{children}</div>, getBezierPath: () => ["", 0, 0],
  };
});

const initial = {
  id: "new", name: "Linux", description: "", status: "draft", usageScope: "course",
  nodes: [{ id: "web", name: "Web", role: "server", type: "lxc", sourceType: "custom", customImageRef: "debian", cpu: 1, memory: 1, disk: 8 }], edges: [], publications: [],
};
let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  sessionStorage.clear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  mocks.confirm.mockResolvedValue(true);
  mocks.saveDraft.mockImplementation(async (_, item) => ({ ...item, id: "env-1", version: 1 }));
  mocks.publish.mockResolvedValue({ ...initial, id: "env-1", status: "published" });
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function renderNew(draft = initial, tab = "machines") {
  if (draft) sessionStorage.setItem("courseTemplateEditorDraft:test:new", JSON.stringify(draft));
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[`/course-template-management/new?tab=${tab}&returnTo=/done`]}>
      <Routes><Route path="/course-template-management/new" element={<CourseTemplateEditorPage />} /><Route path="/done" element={<p>Published</p>} /></Routes>
    </MemoryRouter>);
  });
}

async function renderPublished(overrides = {}) {
  mocks.get.mockResolvedValue({ ...initial, id: "env-1", status: "published", version: 2, updatedAt: "2026-09-16", ...overrides });
  await act(async () => {
    root.render(<MemoryRouter initialEntries={["/course-template-management/env-1?tab=basic"]}>
      <Routes><Route path="/course-template-management/:templateId" element={<CourseTemplateEditorPage />} /></Routes>
    </MemoryRouter>);
  });
}

function setSelect(select, value) {
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(select, value);
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

it("automatically saves basic input even before any machine is configured", async () => {
  vi.useFakeTimers();
  try {
    await renderNew(null, "basic");
    expect(mocks.saveDraft).not.toHaveBeenCalled();
    const name = host.querySelector("input");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(name, "New lab");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => vi.advanceTimersByTimeAsync(700));
    expect(mocks.saveDraft).toHaveBeenCalledWith(null, expect.objectContaining({ name: "New lab", nodes: [] }));
    expect(host.textContent).toContain("CourseTemplateEditorPage.autosave.saved");
  } finally { vi.useRealTimers(); }
});

it("removes manual save and publishes a new environment with the latest draft", async () => {
  await renderNew();
  expect(host.textContent).not.toContain("CourseTemplateEditorPage.saveDraftBtn");
  const publish = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("CourseTemplateEditorPage.publishLabel"));
  expect(publish.disabled).toBe(false);
  await act(async () => publish.click());
  expect(mocks.saveDraft).toHaveBeenCalledWith(null, expect.objectContaining({ name: "Linux" }));
  expect(mocks.publish).toHaveBeenCalledExactlyOnceWith("env-1");
  expect(host.textContent).toBe("Published");
});

it("does not publish on save failure, and retry keeps the edits", async () => {
  mocks.saveDraft.mockRejectedValueOnce(new Error("offline"));
  await renderNew();
  const publish = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("CourseTemplateEditorPage.publishLabel"));
  await act(async () => publish.click());
  expect(mocks.publish).not.toHaveBeenCalled();
  expect(host.textContent).toContain("CourseTemplateEditorPage.autosave.error");
  const retry = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("CourseTemplateEditorPage.retryAutosave"));
  await act(async () => retry.click());
  expect(mocks.saveDraft).toHaveBeenLastCalledWith(null, expect.objectContaining({ name: "Linux" }));
  expect(host.textContent).toContain("CourseTemplateEditorPage.autosave.saved");
});

it("lets a published environment change how it is offered without a new version", async () => {
  mocks.setVisibility.mockImplementation(async (_id, item) => ({ ...item, status: "published" }));
  await renderPublished({ usageScope: "both" });
  const [usageScope] = [...host.querySelectorAll("select")];
  /* 機器設定仍然凍結，只有提供方式這組欄位解鎖 */
  expect(host.querySelector("input").disabled).toBe(true);
  expect(usageScope.disabled).toBe(false);
  const save = () => [...host.querySelectorAll("button")].find((button) => button.textContent.includes("CourseTemplateEditorPage.saveOfferingBtn"));
  expect(save().disabled).toBe(true);
  await act(async () => setSelect(usageScope, "course"));
  await act(async () => save().click());
  expect(mocks.setVisibility).toHaveBeenCalledWith("env-1", expect.objectContaining({ usageScope: "course" }));
  expect(mocks.saveDraft).not.toHaveBeenCalled();
});
