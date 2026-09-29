// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { formatBytes } from "./templateFormat";
import TemplateCloneDialog from "./TemplateCloneDialog";

const mocks = vi.hoisted(() => ({
  clone: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/templates", () => ({ TemplatesService: { clone: mocks.clone } }));
vi.mock("../../../services/gpu", () => ({ GpuService: { listOptions: vi.fn(async () => []) } }));
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
      root.render(<TemplateCloneDialog template={{ id: "tpl-1", name: "Ubuntu" }} onClose={onClose} />);
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
});
