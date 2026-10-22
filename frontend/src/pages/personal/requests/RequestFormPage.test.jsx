// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  vmRequests: { advise: vi.fn(), create: vi.fn() },
  resources: { listLxcOsImages: vi.fn(), listVmOsTemplates: vi.fn() },
  policies: { getUsernameRules: vi.fn(), checkUsername: vi.fn() },
  translation: { t: (key) => key, i18n: { language: "zh-TW" } },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => mocks.translation }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "teacher" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock("../../../services/vmRequests", () => ({ VmRequestsService: mocks.vmRequests }));
vi.mock("../../../services/vmRequestAvailability", () => ({ VmRequestAvailabilityService: { windowAvailability: vi.fn() } }));
vi.mock("../../../services/gpu", () => ({ GpuService: { listOptions: vi.fn().mockResolvedValue([]) } }));
vi.mock("../../../services/templates", () => ({
  TemplatesService: { list: vi.fn().mockResolvedValue({ data: [] }), catalog: vi.fn().mockResolvedValue({ data: [] }) },
}));
vi.mock("../../../services/resources", () => ({ ResourcesService: mocks.resources }));
vi.mock("../../../services/quotas", () => ({ QuotasService: { getMyUsage: vi.fn().mockResolvedValue(null) } }));
vi.mock("../../../services/policies", () => ({ PoliciesService: mocks.policies }));
vi.mock("../../../components/AvailabilityPanel/AvailabilityPanel", () => ({ default: () => null }));

const { default: RequestFormPage } = await import("./RequestFormPage");
const { withQueryClient } = await import("../../../queries/testing");

/* TanStack Query 以 macrotask 批次通知，等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}
async function renderForm() {
  await act(async () => root.render(withQueryClient(<RequestFormPage onBack={() => {}} />)));
  await flush();
}

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.vmRequests.advise.mockResolvedValue({ resource_type: "vm" });
  mocks.vmRequests.create.mockResolvedValue({ id: "req-1" });
  mocks.resources.listLxcOsImages.mockResolvedValue([]);
  mocks.resources.listVmOsTemplates.mockResolvedValue([{ vmid: 9001, name: "ubuntu-24.04", node: "pve1", disk_gb: 20 }]);
  mocks.policies.getUsernameRules.mockResolvedValue({ max_length: 32, reserved_prefixes: [] });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

/* React 受控欄位要透過原生 setter 改值再丟事件，onChange 才收得到 */
function typeInto(element, value) {
  const proto = Object.getPrototypeOf(element);
  Object.getOwnPropertyDescriptor(proto, "value").set.call(element, value);
  element.dispatchEvent(new Event(element.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}

it("locks the submit button while the last-moment username check is still in flight", async () => {
  await renderForm();

  const button = (label) => [...host.querySelectorAll("button")].find((el) => el.textContent.includes(label));
  await act(async () => button("RequestFormPage.modeImmediate").click());
  await act(async () => typeInto(host.querySelector("select"), "vm:9001"));
  await act(async () => typeInto(host.querySelector('[data-field="hostname"] input'), "lab-01"));
  await act(async () => typeInto(host.querySelector('[data-field="password"] input'), "Passw0rd!"));
  await act(async () => typeInto(host.querySelector("textarea"), "course project for the network lab"));

  /* 帳號剛打完就送出：防抖中的即時檢查還沒回來，送出時要先補查 */
  let resolveCheck;
  mocks.policies.checkUsername.mockReturnValue(new Promise((resolve) => { resolveCheck = resolve; }));
  await act(async () => typeInto(host.querySelector('[data-field="username"] input'), "student"));

  const submit = host.querySelector('button[type="submit"][form="request-form"]');
  await act(async () => {
    host.querySelector("form#request-form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });

  /* 補查還在路上：送出鈕必須已經鎖住，否則連按兩下會送出兩張申請 */
  expect(mocks.policies.checkUsername).toHaveBeenCalledWith("student");
  expect(submit.disabled).toBe(true);

  await act(async () => resolveCheck({ violations: [] }));
  expect(mocks.vmRequests.create).toHaveBeenCalledTimes(1);
});

it("unlocks the submit button again when validation fails", async () => {
  await renderForm();
  const submit = host.querySelector('button[type="submit"][form="request-form"]');

  await act(async () => {
    host.querySelector("form#request-form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });

  expect(mocks.vmRequests.create).not.toHaveBeenCalled();
  expect(submit.disabled).toBe(false);
});

it("loads GPU options for a GPU template on its node once the OS is chosen", async () => {
  const { GpuService } = await import("../../../services/gpu");
  GpuService.listOptions.mockResolvedValue([{ mapping_id: "gpu-a", description: "H200", available_count: 1, device_count: 1 }]);
  mocks.resources.listVmOsTemplates.mockResolvedValue([{ vmid: 9002, name: "ubuntu-gpu", node: "pve2", disk_gb: 20 }]);
  await renderForm();

  const button = (label) => [...host.querySelectorAll("button")].find((el) => el.textContent.includes(label));
  await act(async () => button("RequestFormPage.modeImmediate").click());
  await act(async () => typeInto(host.querySelector("select"), "vm:9002"));
  await flush();
  await flush();

  expect(GpuService.listOptions).toHaveBeenCalledWith({ node: "pve2" });
  expect(host.querySelector('option[value="gpu-a"]')).toBeTruthy();
  expect(host.textContent).not.toContain("RequestFormPage.gpuRecalculatingHint");
  expect(host.textContent).not.toContain("RequestFormPage.noGpuAvailableHint");
});

it("offers a retry when an OS source fails and re-fetches only the list that has no data", async () => {
  mocks.resources.listLxcOsImages
    .mockRejectedValueOnce(new Error("500"))
    .mockResolvedValue([{ volid: "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst" }]);
  await renderForm();

  const retry = [...host.querySelectorAll("button")].find((el) => el.textContent.includes("RequestFormPage.retry"));
  expect(retry).toBeTruthy();
  expect(mocks.resources.listVmOsTemplates).toHaveBeenCalledTimes(1);

  await act(async () => retry.click());
  await flush();

  expect(mocks.resources.listLxcOsImages).toHaveBeenCalledTimes(2);
  expect(mocks.resources.listVmOsTemplates).toHaveBeenCalledTimes(1);
  expect([...host.querySelectorAll("button")].some((el) => el.textContent.includes("RequestFormPage.retry"))).toBe(false);
});
