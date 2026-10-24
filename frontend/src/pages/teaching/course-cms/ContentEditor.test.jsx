// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listRooms: vi.fn(), listTasks: vi.fn(), listQuestions: vi.fn(), createRoom: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../../services/courses", () => ({
  CourseAdminService: {
    listRooms: mocks.listRooms, listTasks: mocks.listTasks, listQuestions: mocks.listQuestions, createRoom: mocks.createRoom,
  },
  CoursesService: {},
}));
vi.mock("../../../contexts/UnsavedChangesContext", () => ({
  useUnsavedChanges: () => ({ confirmLeave: async () => true }),
  useUnsavedChangesGuard: () => {},
}));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => true }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));

import { withQueryClient } from "../../../queries/testing";
import ContentEditor from "./ContentEditor";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let host;
let root;

async function flush() {
  await act(async () => {
    for (let tick = 0; tick < 2; tick += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    }
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const paths = [{ id: "p1", title: "Linux" }, { id: "p2", title: "Network" }];
const buttonByText = (text) => [...host.querySelectorAll("button")].find((button) => button.textContent.includes(text));
/* 展開鈕與標題鈕並排：找標題是這一列的那顆展開鈕 */
const toggleFor = (title) => [...host.querySelectorAll("button[aria-expanded]")]
  .find((button) => button.nextSibling?.textContent.includes(title));

async function renderEditor(props = {}) {
  await act(async () => {
    root.render(withQueryClient(<ContentEditor paths={paths} teachingClasses={[]} initialPathId="" onReloadPaths={vi.fn(async () => {})} {...props} />));
  });
  await flush();
}

test("each tree level loads its children only when expanded, and a selected task shows its questions", async () => {
  mocks.listRooms.mockResolvedValue([{ id: "r1", title: "Shell", difficulty: "easy" }]);
  mocks.listTasks.mockResolvedValue([{ id: "t1", title: "ls", content: "" }]);
  mocks.listQuestions.mockResolvedValue([{ id: "q1", prompt: "What does ls do?", question_type: "flag", points: 10 }]);
  await renderEditor();
  expect(mocks.listRooms).not.toHaveBeenCalled();

  await act(async () => toggleFor("Linux").click());
  await flush();
  expect(mocks.listRooms).toHaveBeenCalledExactlyOnceWith("p1");
  expect(host.textContent).toContain("Shell");

  await act(async () => toggleFor("Shell").click());
  await flush();
  expect(mocks.listTasks).toHaveBeenCalledExactlyOnceWith("r1");

  await act(async () => buttonByText("ls").click());
  await flush();
  expect(mocks.listQuestions).toHaveBeenCalledWith("t1");
  expect(host.textContent).toContain("What does ls do?");
  /* 樹與工作區共用同一份快取：選中任務不會再抓一次關卡或任務 */
  expect(mocks.listRooms).toHaveBeenCalledTimes(1);
  expect(mocks.listTasks).toHaveBeenCalledTimes(1);
});

test("creating a room refetches that path's rooms and selects the new one", async () => {
  mocks.listRooms.mockResolvedValueOnce([]).mockResolvedValue([{ id: "r9", title: "New room", difficulty: "easy" }]);
  mocks.createRoom.mockResolvedValue({ id: "r9" });
  const onReloadPaths = vi.fn(async () => {});
  await renderEditor({ onReloadPaths });
  await act(async () => toggleFor("Linux").click());
  await flush();

  await act(async () => buttonByText("CourseCmsPage.addRoomLabel").click());
  const input = host.querySelector("input");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "New room");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => input.closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await flush();

  expect(mocks.createRoom).toHaveBeenCalledWith({ path_id: "p1", title: "New room", difficulty: "easy", order: 0 });
  expect(mocks.listRooms).toHaveBeenCalledTimes(2);
  expect(onReloadPaths).toHaveBeenCalled();
  expect(host.querySelector("[aria-current='true']")?.textContent).toContain("New room");
});
