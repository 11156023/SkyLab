// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, test, vi } from "vitest";
import { toast } from "sonner";
import { StudentMachines, WeeklyContent } from "./ClassWorkspacePage";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { ClassroomService } from "../../../services/classroom";
import i18n from "../../../i18n";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() } }));
vi.mock("../../../components/Classroom/ClassroomWatchDialog", () => ({ default: () => null }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function mount(element) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(element);
  });
  return {
    container,
    cleanup: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

const week = { id: "1", week: 1, week_number: 1, date: "2026-09-07", session_date: "2026-09-07", title: "Linux 權限", target: "", status: "draft", files: [] };
const item = { id: "c1", status: "active", nodes: [], weeks: [week] };

describe("WeeklyContent 多檔上傳部分失敗", () => {
  test("前面已上傳成功的檔案留在清單上，之後儲存也會送出它的 id", async () => {
    const serverClass = {
      id: "c1",
      weeks: [{ id: 1, week_number: 1, session_date: "2026-09-07", title: "Linux 權限", status: "draft", files: [{ id: "f1", filename: "A.pdf" }] }],
    };
    const upload = vi.spyOn(TeachingClassesService, "uploadWeekFile")
      .mockResolvedValueOnce(serverClass)
      .mockRejectedValueOnce(new Error("檔案太大"));
    const replaceWeeks = vi.spyOn(TeachingClassesService, "replaceWeeks").mockResolvedValue(serverClass);

    const { container, cleanup } = mount(<WeeklyContent item={item} onRefresh={() => {}} />);
    const input = container.querySelector('input[type="file"]');
    Object.defineProperty(input, "files", { configurable: true, value: [new File(["a"], "A.pdf"), new File(["b"], "B.pdf")] });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await flush();
      await flush();
    });

    expect(upload).toHaveBeenCalledTimes(2);
    expect(toast.error).toHaveBeenCalledTimes(1);
    /* 用實際譯文比對：key 若漏在語系檔，t() 會回傳 key 本身，這裡就會失敗 */
    const failKey = "CourseTemplateEditorPage.filesUploadPartialFail";
    for (const lng of ["zh-TW", "en", "ja"]) {
      expect(i18n.getResource(lng, "teaching", failKey), `${lng} 缺少 ${failKey}`).toBeTypeOf("string");
    }
    const [toastText] = toast.error.mock.calls[0];
    expect(toastText).toContain("B.pdf");
    expect(toastText).not.toContain(failKey);
    expect(toast.success).not.toHaveBeenCalled();
    expect(container.textContent).toContain("A.pdf");

    const saveButton = container.querySelector("button[class*='btnPrimary']");
    await act(async () => {
      saveButton.click();
      await flush();
    });
    expect(replaceWeeks).toHaveBeenCalledTimes(1);
    const [, payload] = replaceWeeks.mock.calls[0];
    expect(payload[0].files).toEqual([{ id: "f1", target_path: null }]);
    cleanup();
  });
});

describe("StudentMachines 教室 session 不留孤兒", () => {
  const classItem = {
    id: "c1",
    nodes: [{ id: "n1", name: "Web", resource_type: "qemu" }],
    students: [{ id: "s1", full_name: "Amy", email: "amy@example.com", machines: [{ vmid: 101, status: "running", machine_node_id: "n1" }] }],
  };

  function mockServices(sessions) {
    vi.spyOn(TeachingClassesService, "resourceUsage").mockResolvedValue({ items: [{ vmid: 101, status: "running", cpu_usage_pct: 12 }] });
    vi.spyOn(ClassroomService, "listClassBroadcastSources").mockResolvedValue([{ vmid: 900, name: "Teacher demo" }]);
    vi.spyOn(ClassroomService, "listSessions").mockResolvedValue(sessions);
    return vi.spyOn(ClassroomService, "stopSession").mockResolvedValue({});
  }

  test("重新進入頁面時找回本班進行中的廣播，並能停止它", async () => {
    const stopSession = mockServices([
      { id: "other", mode: "broadcast", class_id: "c2", vmid: 1 },
      { id: "b1", mode: "broadcast", class_id: "c1", vmid: 900 },
    ]);
    const { container, cleanup } = mount(<StudentMachines item={classItem} />);
    await act(async () => { await flush(); });

    const stopLabel = i18n.t("ClassWorkspacePage.stopBroadcastBtn", { ns: "teaching" });
    const stopButton = [...container.querySelectorAll("button")].find((button) => button.textContent === stopLabel);
    expect(stopButton).toBeTruthy();
    await act(async () => {
      stopButton.click();
      await flush();
    });
    expect(stopSession).toHaveBeenCalledWith("b1");
    cleanup();
  });

  test("畫面卸載時收掉還開著的觀看 session", async () => {
    const stopSession = mockServices([]);
    const createSession = vi.spyOn(ClassroomService, "createSession").mockResolvedValue({ id: "m1" });
    const { container, cleanup } = mount(<StudentMachines item={classItem} />);
    await act(async () => { await flush(); });

    const cell = container.querySelector("button[aria-label]:not([disabled])[title*='VM 101']");
    expect(cell).toBeTruthy();
    await act(async () => {
      cell.click();
      await flush();
    });
    expect(createSession).toHaveBeenCalledWith({ vmid: 101, mode: "monitor", class_id: "c1" });
    expect(stopSession).not.toHaveBeenCalled();

    cleanup();
    expect(stopSession).toHaveBeenCalledWith("m1");
  });

  test("觀看 session 還在建立中就卸載時，建好後立刻收掉", async () => {
    const stopSession = mockServices([]);
    let resolveSession;
    vi.spyOn(ClassroomService, "createSession").mockImplementation(
      () => new Promise((resolve) => { resolveSession = resolve; }),
    );
    const { container, cleanup } = mount(<StudentMachines item={classItem} />);
    await act(async () => { await flush(); });

    const cell = container.querySelector("button[aria-label]:not([disabled])[title*='VM 101']");
    await act(async () => { cell.click(); });
    expect(resolveSession).toBeTypeOf("function");

    cleanup();
    expect(stopSession).not.toHaveBeenCalled();

    await act(async () => {
      resolveSession({ id: "m2" });
      await flush();
    });
    expect(stopSession).toHaveBeenCalledWith("m2");
  });
});
