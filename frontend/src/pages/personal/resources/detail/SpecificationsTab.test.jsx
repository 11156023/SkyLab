// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { resources, specRequests, quotas, toastApi, auth, i18n } = vi.hoisted(() => ({
  i18n: { t: (key, opts) => (opts?.value != null ? `${key}:${opts.value}` : key), i18n: { language: "zh-TW" } },
  resources: { getSpecs: vi.fn(), get: vi.fn(), updateSpecDirect: vi.fn() },
  specRequests: { listMy: vi.fn(), create: vi.fn(), apply: vi.fn(), cancel: vi.fn() },
  quotas: { getMyUsage: vi.fn() },
  toastApi: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  auth: { user: { id: "u1", role: "student" } },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => i18n }));
vi.mock("../../../../services/resources", () => ({ ResourcesService: resources }));
vi.mock("../../../../services/quotas", () => ({ QuotasService: quotas }));
vi.mock("../../../../services/specChangeRequests", async (original) => ({
  ...await original(),
  SpecChangeRequestsService: specRequests,
}));
vi.mock("../../../../hooks/useToast", () => ({ useToast: () => toastApi }));
vi.mock("../../../../contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => true }));

import SpecificationsTab from "./SpecificationsTab";
import { createTestQueryClient, withQueryClient } from "../../../../queries/testing";
import { specChangeRequestKeys } from "../../../../queries/specChangeRequests";

let host, root;

/* TanStack Query 的通知排在 setTimeout(0)：先等一個 timer tick，再把 microtask 清掉 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function render(client = createTestQueryClient()) {
  await act(async () => root.render(withQueryClient(<SpecificationsTab vmid={300} />, client)));
  await flush();
  await flush();
}

const valueSetter = (el) => Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set;

async function slide(id, value) {
  const input = host.querySelector(`#${id}`);
  await act(async () => {
    valueSetter(input).call(input, String(value));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function typeReason(text) {
  const textarea = host.querySelector("#spec-reason");
  await act(async () => {
    valueSetter(textarea).call(textarea, text);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function submit() {
  const button = [...host.querySelectorAll("button")].find((b) =>
    /SpecificationsTab\.(submitRequest|applyChanges)/.test(b.textContent));
  await act(async () => button.click());
  await flush();
  await flush();
}

const coresNumberInput = () => host.querySelector('input[type="number"][aria-label="SpecificationsTab.cpuCoresLabel"]');

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  auth.user = { id: "u1", role: "student" };
  resources.get.mockResolvedValue({ vmid: 300, can_request_spec_change: true });
  resources.updateSpecDirect.mockResolvedValue({});
  specRequests.listMy.mockResolvedValue({ data: [] });
  specRequests.create.mockResolvedValue({ id: "r1", vmid: 300, status: "pending" });
  quotas.getMyUsage.mockResolvedValue(null);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

describe("LXC 沒設核心上限", () => {
  beforeEach(() => {
    resources.getSpecs.mockResolvedValue({
      vmid: 300, resource_type: "lxc", cpu_cores: null, memory_mb: 512, disk_gb: 8,
    });
  });

  it("CPU 顯示「不限」，不是 1 核", async () => {
    await render();
    expect(host.textContent).toContain("SpecificationsTab.currentCoresUnlimited");
    expect(host.textContent).not.toContain("SpecificationsTab.currentLabel");
    expect(coresNumberInput().value).toBe("");
    expect(coresNumberInput().getAttribute("placeholder")).toBe("SpecificationsTab.coresUnlimited");
  });

  it("只改記憶體時不送 requested_cpu", async () => {
    await render();
    await slide("spec-memory", 1024);
    await typeReason("需要更多記憶體跑課程作業");
    await submit();
    expect(specRequests.create).toHaveBeenCalledTimes(1);
    const body = specRequests.create.mock.calls[0][0];
    expect(body.requested_cpu).toBeUndefined();
    expect(body.requested_memory).toBe(1024);
  });

  it("什麼都沒動時擋下「規格沒有變更」", async () => {
    await render();
    await typeReason("需要更多記憶體跑課程作業");
    await submit();
    expect(specRequests.create).not.toHaveBeenCalled();
    expect(toastApi.error).toHaveBeenCalledWith("SpecificationsTab.noChanges");
  });

  it("動了 CPU 拉桿就送出核心上限", async () => {
    await render();
    await slide("spec-cores", 2);
    expect(host.textContent).toContain("SpecificationsTab.newCoresCap:2");
    await typeReason("限制核心數避免影響其他人");
    await submit();
    expect(specRequests.create.mock.calls[0][0].requested_cpu).toBe(2);
  });

  it("數字框輸入核心數也會設定上限", async () => {
    await render();
    const input = coresNumberInput();
    await act(async () => {
      valueSetter(input).call(input, "4");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => input.dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
    await typeReason("限制核心數避免影響其他人");
    await submit();
    expect(specRequests.create.mock.calls[0][0].requested_cpu).toBe(4);
  });

  it("管理員只改記憶體時直接套用也不帶 cores", async () => {
    auth.user = { id: "a1", role: "admin" };
    await render();
    await slide("spec-memory", 2048);
    await submit();
    expect(resources.updateSpecDirect).toHaveBeenCalledWith(300, {
      cores: undefined, memory: 2048, disk_size: undefined,
    });
  });
});

describe("已設核心上限", () => {
  beforeEach(() => {
    resources.getSpecs.mockResolvedValue({
      vmid: 300, resource_type: "lxc", cpu_cores: 2, memory_mb: 1024, disk_gb: 8,
    });
  });

  it("沒動 CPU 時不送 requested_cpu，動了才送", async () => {
    await render();
    expect(host.textContent).toContain("SpecificationsTab.currentLabel:2");
    await slide("spec-memory", 2048);
    await typeReason("需要更多記憶體跑課程作業");
    await submit();
    expect(specRequests.create.mock.calls[0][0].requested_cpu).toBeUndefined();
  });

  it("CPU 拉回原值等於沒變更", async () => {
    auth.user = { id: "a1", role: "admin" };
    await render();
    await slide("spec-cores", 4);
    await slide("spec-cores", 2);
    await submit();
    expect(resources.updateSpecDirect).not.toHaveBeenCalled();
    expect(toastApi.error).toHaveBeenCalledWith("SpecificationsTab.noChanges");
  });

  it("管理員直接套用後重抓規格，表單回到新的目前值", async () => {
    auth.user = { id: "a1", role: "admin" };
    await render();
    resources.getSpecs.mockResolvedValue({
      vmid: 300, resource_type: "lxc", cpu_cores: 2, memory_mb: 2048, disk_gb: 8,
    });
    await slide("spec-memory", 2048);
    await submit();
    expect(resources.updateSpecDirect).toHaveBeenCalledTimes(1);
    expect(resources.getSpecs).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("SpecificationsTab.currentMemoryLabel:2");
  });
});

describe("規格調整申請的進度", () => {
  const applying = {
    id: "r1", vmid: 300, status: "approved", apply_status: "applying",
    change_type: "combined", requested_memory: 2048, created_at: "2026-10-20T00:00:00Z",
  };

  beforeEach(() => {
    resources.getSpecs.mockResolvedValue({
      vmid: 300, resource_type: "lxc", cpu_cores: 2, memory_mb: 1024, disk_gb: 8,
    });
  });

  it("送出後重抓我的申請：通知列出現、表單鎖住", async () => {
    await render();
    specRequests.listMy.mockResolvedValue({
      data: [{ id: "r1", vmid: 300, status: "pending", change_type: "combined", created_at: "2026-10-20T00:00:00Z" }],
    });
    await slide("spec-memory", 2048);
    await typeReason("需要更多記憶體跑課程作業");
    await submit();

    expect(specRequests.create).toHaveBeenCalledTimes(1);
    expect(specRequests.listMy).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("SpecificationsTab.noticePending");
    expect(host.querySelector("#spec-reason").disabled).toBe(true);
    /* 送出後剩餘配額會變：重抓一次 */
    expect(quotas.getMyUsage).toHaveBeenCalledTimes(2);
  });

  it("套用中結束後重抓規格，「目前」數字更新、通知列消失", async () => {
    specRequests.listMy.mockResolvedValue({ data: [applying] });
    const client = createTestQueryClient();
    await render(client);
    expect(host.textContent).toContain("SpecificationsTab.noticeApplying");
    expect(host.textContent).toContain("SpecificationsTab.currentMemoryLabel:1");

    specRequests.listMy.mockResolvedValue({ data: [{ ...applying, apply_status: "applied" }] });
    resources.getSpecs.mockResolvedValue({
      vmid: 300, resource_type: "lxc", cpu_cores: 2, memory_mb: 2048, disk_gb: 8,
    });
    /* 模擬 5 秒一輪的進度輪詢 */
    await act(async () => { await client.refetchQueries({ queryKey: specChangeRequestKeys.mine }); });
    await flush();
    await flush();

    expect(resources.getSpecs).toHaveBeenCalledTimes(2);
    expect(host.textContent).not.toContain("SpecificationsTab.noticeApplying");
    expect(host.textContent).toContain("SpecificationsTab.currentMemoryLabel:2");
  });

  it("撤銷後就地換成已結案的申請，不必重抓清單", async () => {
    const pending = { id: "r2", vmid: 300, status: "pending", change_type: "combined", created_at: "2026-10-20T00:00:00Z" };
    specRequests.listMy.mockResolvedValue({ data: [pending] });
    specRequests.cancel.mockResolvedValue({ ...pending, status: "cancelled" });
    await render();
    expect(host.textContent).toContain("SpecificationsTab.noticePending");

    const cancelButton = [...host.querySelectorAll("button")].find((b) => b.textContent.includes("SpecificationsTab.cancelRequest"));
    await act(async () => cancelButton.click());
    await flush();

    expect(specRequests.cancel).toHaveBeenCalledWith("r2");
    expect(specRequests.listMy).toHaveBeenCalledTimes(1);
    expect(host.textContent).not.toContain("SpecificationsTab.noticePending");
  });
});
