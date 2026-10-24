// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { formatBytes } from "./templateFormat";
import TemplateCloneDialog from "./TemplateCloneDialog";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  clone: vi.fn(),
  listOptions: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/templates", () => ({ TemplatesService: { clone: mocks.clone } }));
vi.mock("../../../services/gpu", () => ({ GpuService: { listOptions: mocks.listOptions } }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: (key) => key }),
}));

describe("formatBytes", () => {
  test("依大小換成 B／KB／MB", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(3.5 * 1024 * 1024)).toBe("3.5 MB");
  });
});

describe("TemplateCloneDialog", () => {
  let host;
  let root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    mocks.clone.mockReset().mockResolvedValue({ tasks: [{}, {}, {}] });
    mocks.listOptions.mockReset().mockResolvedValue([]);
    mocks.toast.error.mockReset();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  test("一律顯示台數欄位，送出時帶上夾回 1–50 的台數", async () => {
    const onClose = vi.fn();
    await act(async () => {
      root.render(withQueryClient(<TemplateCloneDialog template={{ id: "tpl-1", name: "Ubuntu" }} onClose={onClose} />));
    });

    const countInput = document.querySelector("#clone-count");
    expect(countInput).not.toBeNull();

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(countInput, "3");
      countInput.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const submit = [...document.querySelectorAll("button")]
      .find((button) => button.textContent.includes("TemplateCloneDialog.submit"));
    await act(async () => { submit.click(); });

    expect(mocks.clone).toHaveBeenCalledWith("tpl-1", expect.objectContaining({ count: 3 }));
    expect(mocks.toast.success).toHaveBeenCalledWith("TemplateCloneDialog.cloneQueuedMultiple");
    expect(onClose).toHaveBeenCalled();
  });

  test("台數打成小數時捨去成整數再送出（後端 count 是整數）", async () => {
    await act(async () => {
      root.render(withQueryClient(<TemplateCloneDialog template={{ id: "tpl-1", name: "Ubuntu" }} onClose={vi.fn()} />));
    });

    const countInput = document.querySelector("#clone-count");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(countInput, "2.5");
      countInput.dispatchEvent(new Event("input", { bubbles: true }));
    });

    const submit = [...document.querySelectorAll("button")]
      .find((button) => button.textContent.includes("TemplateCloneDialog.submit"));
    await act(async () => { submit.click(); });

    expect(mocks.clone).toHaveBeenCalledWith("tpl-1", expect.objectContaining({ count: 2 }));
  });

  test("不需要 GPU 的範本不抓 GPU 清單", async () => {
    await act(async () => {
      root.render(withQueryClient(<TemplateCloneDialog template={{ id: "tpl-1", name: "Ubuntu" }} onClose={vi.fn()} />));
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

    expect(mocks.listOptions).not.toHaveBeenCalled();
    expect(document.querySelector("#clone-gpu")).toBeNull();
  });

  test("需要 GPU 的範本只列同節點叢集的 GPU；載不到時提示", async () => {
    const gpuTemplate = { id: "tpl-g", name: "CUDA", requires_gpu: true, resource_type: "qemu", node: "pve-a" };
    mocks.listOptions.mockResolvedValueOnce([
      { mapping_id: "h200", description: "H200", available_count: 2, capacity_count: 4, profiles: [] },
    ]);
    await act(async () => {
      root.render(withQueryClient(<TemplateCloneDialog template={gpuTemplate} onClose={vi.fn()} />));
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

    expect(mocks.listOptions).toHaveBeenCalledWith({ node: "pve-a" });
    const select = document.querySelector("#clone-gpu");
    expect(select.disabled).toBe(false);
    expect([...select.options].map((o) => o.value)).toEqual(["", "h200"]);
    expect(document.body.textContent).not.toContain("TemplateCloneDialog.gpuNoneWarning");

    await act(async () => root.unmount());
    root = createRoot(host);
    mocks.listOptions.mockRejectedValueOnce(new Error("boom"));
    await act(async () => {
      root.render(withQueryClient(<TemplateCloneDialog template={gpuTemplate} onClose={vi.fn()} />));
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

    expect(mocks.toast.error).toHaveBeenCalledWith("Error.generic");
    expect(document.querySelector("#clone-gpu").disabled).toBe(false);
    expect(document.body.textContent).toContain("TemplateCloneDialog.gpuNoneWarning");
  });
});
