// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import TemplateFormDialog from "./TemplateFormDialog";
import { withQueryClient } from "../../../queries/testing";
import resource from "../../../locales/zh-TW/resource.json";
import common from "../../../locales/zh-TW/common.json";

const mocks = vi.hoisted(() => ({
  listAttachments: vi.fn(),
  uploadAttachment: vi.fn(),
  removeAttachment: vi.fn(),
  create: vi.fn(),
  listResources: vi.fn(),
  listAllResources: vi.fn(),
  user: { role: "teacher" },
  confirm: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/templates", () => ({
  TemplatesService: {
    listAttachments: mocks.listAttachments,
    uploadAttachment: mocks.uploadAttachment,
    removeAttachment: mocks.removeAttachment,
    create: mocks.create,
    update: vi.fn(),
  },
}));
vi.mock("../../../services/resources", () => ({
  ResourcesService: { list: mocks.listResources, listAll: mocks.listAllResources },
}));
/* 來源機器清單走共用的資源查詢，會順帶抓快速練習 session */
vi.mock("../../../services/quickPractice", () => ({
  QuickPracticeService: { listMySessions: vi.fn(async () => []), listAllSessions: vi.fn(async () => []) },
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: mocks.user }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
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
  mocks.user = { role: "teacher" };
  mocks.listResources.mockResolvedValue([]);
  mocks.listAllResources.mockResolvedValue([]);
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

/* 等 macrotask，讓上傳迴圈裡一連串已 resolve 的 await 都跑完；
   附件清單在 TanStack Query 快取裡，快取更新以 macrotask 批次通知，再多等一輪才會重繪 */
const tick = async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
};

async function render(template, props = {}) {
  await act(async () => {
    root.render(withQueryClient(
      <TemplateFormDialog template={template} onClose={() => {}} onSaved={() => {}} {...props} />,
    ));
  });
  /* 掛載後才開始抓附件；等它回來、重繪完 */
  await act(async () => {
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

describe("TemplateFormDialog 從資源列表轉成範本", () => {
  const SOURCE = { vmid: 123, name: "web-lab", type: "qemu" };

  test("來源機器預選並鎖定、名稱預填，不另外抓機器清單；確認後用該 vmid 建立", async () => {
    mocks.confirm.mockResolvedValue(true);
    mocks.create.mockResolvedValue({ template: { id: "tpl-new" } });
    const onSaved = vi.fn();
    await render(undefined, { sourceResource: SOURCE, onSaved });

    expect(mocks.listResources).not.toHaveBeenCalled();
    expect(mocks.listAllResources).not.toHaveBeenCalled();
    const source = document.body.querySelector("#tpl-source");
    expect(source.disabled).toBe(true);
    expect(source.value).toBe("123");
    expect(document.body.querySelector("#tpl-name").value).toBe("web-lab");

    const submit = [...document.body.querySelectorAll("button")].find(
      (b) => b.textContent === dict["TemplateFormDialog.startConvert"],
    );
    await act(async () => {
      submit.click();
      await tick();
    });

    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({ source_vmid: 123, name: "web-lab" }));
    expect(onSaved).toHaveBeenCalledTimes(1);
  });
});

describe("TemplateFormDialog 來源機器清單", () => {
  const sourceOptions = () =>
    [...document.body.querySelectorAll("#tpl-source option")].map((o) => o.value).filter(Boolean);

  test("老師建立範本：抓自己的機器，佔位列不列入", async () => {
    mocks.listResources.mockResolvedValue([
      { vmid: 101, name: "mine", type: "qemu" },
      { vmid: 102, name: "booking", type: "lxc", is_placeholder: true },
    ]);
    await render(undefined);

    expect(mocks.listResources).toHaveBeenCalledTimes(1);
    expect(mocks.listAllResources).not.toHaveBeenCalled();
    expect(sourceOptions()).toEqual(["101"]);
    expect(document.body.textContent).not.toContain(dict["TemplateFormDialog.sourceNoneWarning"]);
  });

  test("管理員建立範本：從全部機器挑", async () => {
    mocks.user = { role: "admin", is_superuser: true };
    mocks.listAllResources.mockResolvedValue([{ vmid: 201, name: "someone", type: "qemu" }]);
    await render(undefined);

    expect(mocks.listAllResources).toHaveBeenCalledTimes(1);
    expect(mocks.listResources).not.toHaveBeenCalled();
    expect(sourceOptions()).toEqual(["201"]);
  });

  test("機器清單載不到：靜默當成沒有機器並提示", async () => {
    mocks.listResources.mockRejectedValue(new Error("pve down"));
    await render(undefined);

    expect(sourceOptions()).toEqual([]);
    expect(document.body.textContent).toContain(dict["TemplateFormDialog.sourceNoneWarning"]);
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  test("編輯模式不抓機器清單；刪除附件後清單就地更新、不重抓", async () => {
    mocks.listAttachments.mockResolvedValue({ data: [attachment("att-a", "a.pdf"), attachment("att-b", "b.pdf")] });
    mocks.removeAttachment.mockResolvedValue(undefined);
    await render(TEMPLATE);

    expect(mocks.listResources).not.toHaveBeenCalled();
    expect(mocks.listAllResources).not.toHaveBeenCalled();
    expect(attachmentCount()).toBe(2);

    const removeButtons = document.body.querySelectorAll(
      `button[title="${dict["TemplateFormDialog.removeAttachmentTitle"]}"]`,
    );
    await act(async () => {
      removeButtons[0].click();
      await tick();
    });

    expect(mocks.removeAttachment).toHaveBeenCalledWith("tpl-1", "att-a");
    expect(attachmentCount()).toBe(1);
    expect(document.body.textContent).toContain("b.pdf");
    expect(mocks.listAttachments).toHaveBeenCalledTimes(1);
  });
});
