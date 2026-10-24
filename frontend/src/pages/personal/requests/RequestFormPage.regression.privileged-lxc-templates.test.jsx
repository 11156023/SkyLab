// @vitest-environment happy-dom
/**
 * 回歸：老師／管理員的申請表單「容器範本」選項整組消失。
 *
 * 改版前 RequestFormPage 直接呼叫 TemplatesService.list()，把回應的 `res.data`（範本陣列）存進 sysTemplates。
 * 改版後改用 queries/templates 的 useTemplatesQuery，而它的快取存的已經是範本陣列本身（fetchTemplates 先拆掉
 * `{ data }` 外殼），頁面卻還是讀 `sysTemplatesQuery.data?.data` → 永遠 undefined → sysTemplates 一律是空陣列，
 * 「容器範本」optgroup 不見、老師也選不到平台 LXC 範本（requires_gpu 的判斷同樣失效）。
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  vmRequests: { advise: vi.fn(), create: vi.fn() },
  resources: { listLxcOsImages: vi.fn(), listVmOsTemplates: vi.fn() },
  templates: { list: vi.fn(), catalog: vi.fn() },
  policies: { getUsernameRules: vi.fn(), checkUsername: vi.fn() },
  translation: { t: (key) => key, i18n: { language: "zh-TW" } },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => mocks.translation }));
/* 老師：isPrivileged＝true，表單讀完整的平台範本清單（不是學生的應用範本目錄） */
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "teacher" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock("../../../services/vmRequests", () => ({ VmRequestsService: mocks.vmRequests }));
vi.mock("../../../services/vmRequestAvailability", () => ({ VmRequestAvailabilityService: { windowAvailability: vi.fn() } }));
vi.mock("../../../services/gpu", () => ({ GpuService: { listOptions: vi.fn().mockResolvedValue([]) } }));
vi.mock("../../../services/templates", () => ({ TemplatesService: mocks.templates }));
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

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.vmRequests.advise.mockResolvedValue({ resource_type: "vm" });
  mocks.resources.listLxcOsImages.mockResolvedValue([]);
  mocks.resources.listVmOsTemplates.mockResolvedValue([]);
  mocks.policies.getUsernameRules.mockResolvedValue({ max_length: 32, reserved_prefixes: [] });
  /* API 形狀與改版前相同：{ data: [...] } */
  mocks.templates.list.mockResolvedValue({
    data: [{
      id: "tpl-1",
      name: "nginx-app",
      resource_type: "lxc",
      status: "ready",
      version: 3,
      pve_vmid: 9100,
      default_cores: 1,
      default_memory: 1024,
      default_disk: 8,
    }],
  });
  mocks.templates.catalog.mockResolvedValue({ data: [] });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("lists the platform LXC templates for a teacher, like the pre-TanStack form did", async () => {
  await act(async () => root.render(withQueryClient(<RequestFormPage onBack={() => {}} />)));
  await flush();
  await flush();

  expect(mocks.templates.list).toHaveBeenCalledTimes(1);
  /* 改版前：「容器範本」群組裡有 tpl:tpl-1 這個選項 */
  expect(host.querySelector('optgroup[label="RequestFormPage.optgroupContainerTemplate"]')).toBeTruthy();
  expect(host.querySelector('option[value="tpl:tpl-1"]')).toBeTruthy();
});
