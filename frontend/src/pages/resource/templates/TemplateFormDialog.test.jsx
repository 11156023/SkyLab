// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import TemplateFormDialog from "./TemplateFormDialog";
import resource from "../../../locales/zh-TW/resource.json";
import common from "../../../locales/zh-TW/common.json";

const mocks = vi.hoisted(() => ({
  listAttachments: vi.fn(),
  uploadAttachment: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/templates", () => ({
  TemplatesService: {
    listAttachments: mocks.listAttachments,
    uploadAttachment: mocks.uploadAttachment,
    removeAttachment: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  },
}));
vi.mock("../../../services/resources", () => ({
  ResourcesService: { list: async () => [], listAll: async () => [] },
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "teacher" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: translate }),
}));

const dict = { ...common, ...resource };
function translate(key, vars = {}) {
  return (dict[key] ?? key).replace(/\{\{(\w+)\}\}/g, (_, name) => String(vars[name] ?? ""));
}

const TEMPLATE = { id: "tpl-1", name: "Ubuntu", visibility: "private", resource_type: "qemu" };

let host;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.resetAllMocks();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

/* 等 macrotask，讓上傳迴圈裡一連串已 resolve 的 await 都跑完 */
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function render(template) {
  await act(async () => {
    root.render(<TemplateFormDialog template={template} onClose={() => {}} onSaved={() => {}} />);
    await tick();
  });
}

const dropzone = () => document.body.querySelector("input[type=file]").closest("label");
const attachmentCount = () =>
  document.body.querySelectorAll(`button[title="${dict["TemplateFormDialog.removeAttachmentTitle"]}"]`).length;

function file(name, size) {
  const f = new File(["x"], name);
  if (size !== undefined) Object.defineProperty(f, "size", { value: size });
  return f;
}

const attachment = (id, filename) => ({ id, filename, size_bytes: 1 });

/* happy-dom 的 DataTransfer 不完整，直接把 dataTransfer 掛到原生事件上 */
async function drop(files) {
  const event = new Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: { files, types: ["Files"] } });
  await act(async () => {
    dropzone().dispatchEvent(event);
    await tick();
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("TemplateFormDialog 附件一次多檔", () => {
  test("建立模式：合格的全部暫存不上傳，不合格的依原因各合併成一則提示", async () => {
    await render(undefined);
    expect(dropzone().querySelector("input").multiple).toBe(true);

    await drop([file("a.pdf"), file("b.png"), file("c.exe"), file("d.sh"), file("big.zip", 60 * 1024 * 1024)]);

    expect(mocks.uploadAttachment).not.toHaveBeenCalled();
    expect(attachmentCount()).toBe(2);
    expect(document.body.textContent).toContain("a.pdf");
    expect(document.body.textContent).toContain("b.png");
    expect(mocks.toast.error).toHaveBeenCalledTimes(2);
    expect(mocks.toast.error).toHaveBeenCalledWith(
      translate("TemplateFormDialog.unsupportedFileType", { files: "c.exe、d.sh" }),
    );
    expect(mocks.toast.error).toHaveBeenCalledWith(
      translate("TemplateFormDialog.fileTooLarge", { files: "big.zip" }),
    );
  });

  test("編輯模式依序上傳：前一個傳完才傳下一個，上傳區塊顯示進度，傳完一個就進清單", async () => {
    mocks.listAttachments.mockResolvedValue({ data: [] });
    const first = deferred();
    const second = deferred();
    mocks.uploadAttachment.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await render(TEMPLATE);
    const a = file("a.pdf");
    const b = file("b.pdf");

    await drop([a, b]);
    expect(mocks.uploadAttachment).toHaveBeenCalledTimes(1);
    expect(mocks.uploadAttachment).toHaveBeenLastCalledWith("tpl-1", a);
    expect(dropzone().textContent).toContain(
      translate("FileDropzone.uploadingCount", { current: 1, total: 2 }),
    );

    await act(async () => {
      first.resolve(attachment("att-a", "a.pdf"));
      await tick();
    });
    expect(mocks.uploadAttachment).toHaveBeenCalledTimes(2);
    expect(mocks.uploadAttachment).toHaveBeenLastCalledWith("tpl-1", b);
    expect(attachmentCount()).toBe(1);
    expect(dropzone().textContent).toContain(
      translate("FileDropzone.uploadingCount", { current: 2, total: 2 }),
    );

    await act(async () => {
      second.resolve(attachment("att-b", "b.pdf"));
      await tick();
    });
    expect(attachmentCount()).toBe(2);
    expect(dropzone().getAttribute("aria-busy")).toBe("false");
    expect(mocks.toast.success).toHaveBeenCalledWith(
      translate("TemplateFormDialog.attachmentUploadedMultiple", { count: 2 }),
    );
    // 清單直接用上傳回傳的附件更新，不另外重抓
    expect(mocks.listAttachments).toHaveBeenCalledTimes(1);
  });

  test("編輯模式名額不足：只上傳放得下的，其餘列出檔名提示", async () => {
    mocks.listAttachments.mockResolvedValue({
      data: Array.from({ length: 9 }, (_, i) => attachment(`old-${i}`, `old-${i}.pdf`)),
    });
    mocks.uploadAttachment.mockImplementation(async (_, f) => attachment(`new-${f.name}`, f.name));
    await render(TEMPLATE);

    await drop([file("a.pdf"), file("b.pdf"), file("c.pdf")]);

    expect(mocks.uploadAttachment).toHaveBeenCalledTimes(1);
    expect(attachmentCount()).toBe(10);
    expect(mocks.toast.error).toHaveBeenCalledWith(
      translate("TemplateFormDialog.attachmentLimitReached", { max: 10, files: "b.pdf、c.pdf" }),
    );
    expect(mocks.toast.success).toHaveBeenCalledWith(translate("TemplateFormDialog.attachmentUploaded"));
    expect(dropzone().querySelector("input").disabled).toBe(true);
  });

  test("編輯模式部分失敗：成功的留在清單，失敗的合併成一則提示", async () => {
    mocks.listAttachments.mockResolvedValue({ data: [] });
    mocks.uploadAttachment
      .mockResolvedValueOnce(attachment("att-a", "a.pdf"))
      .mockRejectedValueOnce(new Error("磁碟已滿"));
    await render(TEMPLATE);

    await drop([file("a.pdf"), file("b.pdf")]);

    expect(attachmentCount()).toBe(1);
    expect(document.body.textContent).toContain("a.pdf");
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(mocks.toast.error).toHaveBeenCalledWith(
      translate("TemplateFormDialog.attachmentUploadPartialFail", { files: "b.pdf" }),
    );
  });

  test("編輯模式只傳一個檔失敗時沿用後端的錯誤原因", async () => {
    mocks.listAttachments.mockResolvedValue({ data: [] });
    mocks.uploadAttachment.mockRejectedValueOnce(new Error("檔案內容不合法"));
    await render(TEMPLATE);

    await drop([file("a.pdf")]);

    expect(attachmentCount()).toBe(0);
    expect(mocks.toast.error).toHaveBeenCalledWith("檔案內容不合法");
  });
});
