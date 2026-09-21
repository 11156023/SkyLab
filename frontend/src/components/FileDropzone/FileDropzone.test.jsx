// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import FileDropzone from "./FileDropzone";
import FileDropOverlay from "./FileDropOverlay";
import translations from "../../locales/zh-TW/common.json";

vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: (key) => translations[key] ?? key }),
}));

let host;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(ui) {
  act(() => root.render(ui));
  return host.querySelector("label");
}

const fileA = () => new File(["a"], "a.pdf", { type: "application/pdf" });
const fileB = () => new File(["b"], "b.pdf", { type: "application/pdf" });

/* happy-dom 的 DataTransfer 不完整，直接把 dataTransfer 掛到原生事件上，
   React 的合成 DragEvent 會從原生事件讀它。拖檔案時瀏覽器的 types 會帶 "Files" */
function dispatchDrag(target, type, files = [], types = ["Files"]) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: { files, types } });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

describe("FileDropzone", () => {
  test("待機時顯示預設文字、說明與瀏覽鈕，accept 帶到 input", () => {
    const zone = render(<FileDropzone onFiles={() => {}} accept=".pdf,.png" hint="單檔 50MB 內" />);
    expect(zone.textContent).toContain(translations["FileDropzone.title"]);
    expect(zone.textContent).toContain("單檔 50MB 內");
    expect(zone.textContent).toContain(translations["FileDropzone.browse"]);
    const input = zone.querySelector("input[type=file]");
    expect(input.accept).toBe(".pdf,.png");
    expect(input.disabled).toBe(false);
  });

  test("拖入多個檔案時，非 multiple 只交出第一個", () => {
    const onFiles = vi.fn();
    const zone = render(<FileDropzone onFiles={onFiles} />);
    const a = fileA();
    dispatchDrag(zone, "drop", [a, fileB()]);
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles.mock.calls[0][0]).toEqual([a]);
  });

  test("multiple 時交出全部檔案", () => {
    const onFiles = vi.fn();
    const zone = render(<FileDropzone onFiles={onFiles} multiple />);
    dispatchDrag(zone, "drop", [fileA(), fileB()]);
    expect(onFiles.mock.calls[0][0]).toHaveLength(2);
  });

  test("multiple 時 input 可多選，預設標題換成多檔文案", () => {
    const zone = render(<FileDropzone onFiles={() => {}} multiple />);
    expect(zone.querySelector("input[type=file]").multiple).toBe(true);
    expect(zone.textContent).toContain(translations["FileDropzone.titleMultiple"]);
  });

  test("拖曳經過時高亮，離開整塊後取消", () => {
    const zone = render(<FileDropzone onFiles={() => {}} />);
    dispatchDrag(zone, "dragover");
    expect(zone.className).toMatch(/dragging/);
    dispatchDrag(zone, "drop", []);
    expect(zone.className).not.toMatch(/dragging/);
  });

  test("上傳中顯示載入動畫與文字、停用 input，拖入的檔案被忽略", () => {
    const onFiles = vi.fn();
    const zone = render(<FileDropzone onFiles={onFiles} uploading hint="不該出現" />);
    expect(zone.getAttribute("aria-busy")).toBe("true");
    expect(zone.textContent).toContain(translations["FileDropzone.uploading"]);
    expect(zone.textContent).not.toContain("不該出現");
    expect(zone.querySelector("[role=status]")).not.toBeNull();
    expect(zone.querySelector("input[type=file]").disabled).toBe(true);
    dispatchDrag(zone, "drop", [fileA()]);
    expect(onFiles).not.toHaveBeenCalled();
  });

  test("disabled 時拖入的檔案被忽略，也不高亮", () => {
    const onFiles = vi.fn();
    const zone = render(<FileDropzone onFiles={onFiles} disabled />);
    dispatchDrag(zone, "dragover");
    expect(zone.className).not.toMatch(/dragging/);
    dispatchDrag(zone, "drop", [fileA()]);
    expect(onFiles).not.toHaveBeenCalled();
  });

  test("拖的不是檔案（例如選取的文字）時不高亮、不攔截，也不交出", () => {
    const onFiles = vi.fn();
    const zone = render(<FileDropzone onFiles={onFiles} />);
    const over = dispatchDrag(zone, "dragover", [], ["text/plain"]);
    expect(zone.className).not.toMatch(/dragging/);
    expect(over.defaultPrevented).toBe(false);
    dispatchDrag(zone, "drop", [], ["text/plain"]);
    expect(onFiles).not.toHaveBeenCalled();
  });

  test("停用時仍攔下拖放，避免瀏覽器直接開啟丟進來的檔案", () => {
    const zone = render(<FileDropzone onFiles={() => {}} disabled />);
    expect(dispatchDrag(zone, "dragover").defaultPrevented).toBe(true);
    expect(dispatchDrag(zone, "drop", [fileA()]).defaultPrevented).toBe(true);
  });

  test("compact 單行版：顯示標題與說明，上傳中換成載入動畫", () => {
    const zone = render(<FileDropzone onFiles={() => {}} compact hint="JPG、PNG" />);
    expect(zone.className).toMatch(/compact/);
    expect(zone.textContent).toContain(translations["FileDropzone.title"]);
    expect(zone.textContent).toContain("JPG、PNG");
    act(() => root.render(<FileDropzone onFiles={() => {}} compact uploading />));
    expect(zone.querySelector("[role=status]").textContent).toContain(translations["FileDropzone.uploading"]);
  });

  test("className 會併到外層，供呼叫端調整位置", () => {
    const zone = render(<FileDropzone onFiles={() => {}} className="placed" />);
    expect(zone.classList.contains("placed")).toBe(true);
  });

  test("從選擇器選檔後交出檔案並清空 input，才能再選同一個檔案", () => {
    const onFiles = vi.fn();
    const zone = render(<FileDropzone onFiles={onFiles} />);
    const input = zone.querySelector("input[type=file]");
    const a = fileA();
    Object.defineProperty(input, "files", { value: [a], configurable: true });
    act(() => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(onFiles.mock.calls[0][0]).toEqual([a]);
    expect(input.value).toBe("");
  });
});

describe("FileDropOverlay", () => {
  test("顯示預設提示，也可以換成呼叫端的文字", () => {
    act(() => root.render(<FileDropOverlay />));
    expect(host.textContent).toContain(translations["FileDropzone.dropToAdd"]);
    act(() => root.render(<FileDropOverlay label="放開以加入附件" />));
    expect(host.textContent).toContain("放開以加入附件");
  });
});
