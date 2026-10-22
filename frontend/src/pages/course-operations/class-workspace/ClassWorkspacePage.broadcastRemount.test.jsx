// @vitest-environment happy-dom
/*
 * 開始廣播後切到別的步驟、5 秒內再切回課堂監看：session 清單的快取是開始廣播前抓的，
 * 沒有標成過期的話會被當成新鮮的清單，找不回進行中的廣播，「停止廣播」按鈕就消失了。
 */
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, test, vi } from "vitest";
import { StudentMachines } from "./ClassWorkspacePage";
import { createQueryClient } from "../../../queries/queryClient";
import { withQueryClient } from "../../../queries/testing";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { ClassroomService } from "../../../services/classroom";
import i18n from "../../../i18n";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() } }));
vi.mock("../../../components/Classroom/ClassroomWatchDialog", () => ({ default: () => null }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));

afterEach(() => {
  vi.restoreAllMocks();
});

async function flush() {
  for (let tick = 0; tick < 3; tick += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  }
}

const classItem = {
  id: "c1",
  nodes: [{ id: "n1", name: "Web", resource_type: "qemu" }],
  students: [{ id: "s1", full_name: "Amy", email: "amy@example.com", machines: [{ vmid: 101, status: "running", machine_node_id: "n1" }] }],
};

function mount(client) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => { root.render(withQueryClient(<StudentMachines item={classItem} />, client)); });
  return {
    container,
    cleanup: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

const stopLabel = () => i18n.t("ClassWorkspacePage.stopBroadcastBtn", { ns: "teaching" });
const stopButton = (container) => [...container.querySelectorAll("button")].find((b) => b.textContent === stopLabel());

test("coming back within the stale time after starting a broadcast still finds it and shows Stop", async () => {
  /* 與正式環境相同的快取設定（staleTime 5 秒、快取保留），不輪詢 */
  const client = createQueryClient({ queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } });
  const live = { id: "b9", mode: "broadcast", class_id: "c1", vmid: 900 };
  vi.spyOn(TeachingClassesService, "resourceUsage").mockResolvedValue({ items: [{ vmid: 101, status: "running" }] });
  vi.spyOn(ClassroomService, "listClassBroadcastSources").mockResolvedValue([{ vmid: 900, name: "Teacher demo" }]);
  const listSessions = vi.spyOn(ClassroomService, "listSessions").mockResolvedValue([]);
  vi.spyOn(ClassroomService, "createSession").mockImplementation(async () => {
    listSessions.mockResolvedValue([live]);
    return live;
  });

  const first = mount(client);
  await act(async () => { await flush(); });
  expect(stopButton(first.container)).toBeUndefined();

  const select = first.container.querySelector("select");
  await act(async () => {
    select.value = "900";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await flush();
  });
  expect(stopButton(first.container)).toBeTruthy();
  first.cleanup();

  /* 馬上切回來（還在 staleTime 內） */
  const second = mount(client);
  await act(async () => { await flush(); });
  expect(stopButton(second.container)).toBeTruthy();
  second.cleanup();
});
