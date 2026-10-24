// @vitest-environment happy-dom
/* courses.js 老師端（課程 CMS）那一段：courseAdminKeys 與其 hooks */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  courseAdminKeys,
  useAdminCoursePathsQuery,
  useCourseAdminCacheUpdater,
  useCourseQuestionsQuery,
  useCourseRoomsQuery,
  useCourseTasksQuery,
  usePathProgressQuery,
} from "./courses";

const mocks = vi.hoisted(() => ({
  listPaths: vi.fn(), getPathProgress: vi.fn(), listRooms: vi.fn(), listTasks: vi.fn(), listQuestions: vi.fn(),
}));
vi.mock("../services/courses", () => ({ CourseAdminService: mocks, CoursesService: {} }));

let host;
let root;
let seen;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("content tree queries", () => {
  function TreeProbe({ pathId, roomId, taskId, open }) {
    const rooms = useCourseRoomsQuery(pathId, { enabled: open });
    const tasks = useCourseTasksQuery(roomId);
    const questions = useCourseQuestionsQuery(taskId);
    seen = { rooms: rooms.data, tasks: tasks.data, questions: questions.data };
    return null;
  }

  test("children load only once their parent is known (rooms wait for the path to be expanded)", async () => {
    mocks.listRooms.mockResolvedValue([{ id: "r1" }]);
    mocks.listTasks.mockResolvedValue([{ id: "t1" }]);
    mocks.listQuestions.mockResolvedValue([{ id: "q1" }]);
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<TreeProbe pathId="p1" roomId={null} taskId={null} open={false} />, client)); });
    await flush();
    expect(mocks.listRooms).not.toHaveBeenCalled();
    expect(mocks.listTasks).not.toHaveBeenCalled();
    expect(mocks.listQuestions).not.toHaveBeenCalled();

    await act(async () => { root.render(withQueryClient(<TreeProbe pathId="p1" roomId="r1" taskId="t1" open />, client)); });
    await flush();
    expect(mocks.listRooms).toHaveBeenCalledWith("p1");
    expect(seen).toEqual({ rooms: [{ id: "r1" }], tasks: [{ id: "t1" }], questions: [{ id: "q1" }] });
  });
});

describe("useCourseAdminCacheUpdater", () => {
  function PathsProbe() {
    const paths = useAdminCoursePathsQuery({ refetchInterval: false });
    const progress = usePathProgressQuery("p1", { refetchInterval: false, refetchOnWindowFocus: false });
    const updater = useCourseAdminCacheUpdater();
    seen = { paths: paths.data, progress: progress.data, updater };
    return null;
  }

  test("invalidatePaths() resolves after the path list has been refetched", async () => {
    mocks.listPaths.mockResolvedValue([{ id: "p1" }]);
    mocks.getPathProgress.mockResolvedValue({ students: [] });
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<PathsProbe />, client)); });
    await flush();
    const firstUpdater = seen.updater;

    mocks.listPaths.mockResolvedValue([{ id: "p1" }, { id: "p2" }]);
    await act(async () => { await seen.updater.invalidatePaths(); });
    expect(client.getQueryData(courseAdminKeys.paths)).toHaveLength(2);
    await flush();
    expect(seen.paths).toHaveLength(2);
    expect(mocks.getPathProgress).toHaveBeenCalledTimes(1);
    expect(seen.updater).toBe(firstUpdater);
  });

  test("setPathProgress() writes a pushed snapshot and a slower in-flight fetch cannot overwrite it", async () => {
    let finishInitial;
    mocks.listPaths.mockResolvedValue([]);
    mocks.getPathProgress.mockImplementationOnce(() => new Promise((resolve) => { finishInitial = resolve; }));
    await act(async () => { root.render(withQueryClient(<PathsProbe />)); });

    await act(async () => { await seen.updater.setPathProgress("p1", { students: [{ user_id: "latest" }] }); });
    await flush();
    expect(seen.progress).toEqual({ students: [{ user_id: "latest" }] });

    await act(async () => { finishInitial({ students: [{ user_id: "stale" }] }); });
    await flush();
    expect(seen.progress).toEqual({ students: [{ user_id: "latest" }] });
  });
});
