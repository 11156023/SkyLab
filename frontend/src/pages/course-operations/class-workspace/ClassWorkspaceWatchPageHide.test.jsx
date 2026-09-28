// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, test, vi } from "vitest";
import { StudentMachines } from "./ClassWorkspacePage";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { ClassroomService } from "../../../services/classroom";

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

const classItem = {
  id: "c1",
  nodes: [{ id: "n1", name: "Web", resource_type: "qemu" }],
  students: [{ id: "s1", full_name: "Amy", email: "amy@example.com", machines: [{ vmid: 101, status: "running", machine_node_id: "n1" }] }],
};

describe("StudentMachines 關閉分頁時收掉觀看 session", () => {
  test("pagehide 會停止目前的觀看 session，之後卸載不會重複送出", async () => {
    vi.spyOn(TeachingClassesService, "resourceUsage").mockResolvedValue({ items: [{ vmid: 101, status: "running", cpu_usage_pct: 12 }] });
    vi.spyOn(ClassroomService, "listClassBroadcastSources").mockResolvedValue([]);
    vi.spyOn(ClassroomService, "listSessions").mockResolvedValue([]);
    const stopSession = vi.spyOn(ClassroomService, "stopSession").mockResolvedValue({});
    vi.spyOn(ClassroomService, "createSession").mockResolvedValue({ id: "m1" });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => { root.render(<StudentMachines item={classItem} />); });
    await act(async () => { await flush(); });

    const cell = container.querySelector("button[aria-label]:not([disabled])[title*='VM 101']");
    expect(cell).toBeTruthy();
    await act(async () => {
      cell.click();
      await flush();
    });
    expect(stopSession).not.toHaveBeenCalled();

    await act(async () => {
      window.dispatchEvent(new Event("pagehide"));
      await flush();
    });
    expect(stopSession).toHaveBeenCalledTimes(1);
    expect(stopSession).toHaveBeenCalledWith("m1");

    act(() => root.unmount());
    container.remove();
    expect(stopSession).toHaveBeenCalledTimes(1);
  });
});
