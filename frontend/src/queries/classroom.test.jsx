// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  classroomKeys,
  useClassBroadcastSourcesQuery,
  useClassroomCacheUpdater,
  useClassroomLiveQuery,
  useClassroomSessionsQuery,
  useClassStudentsQuery,
} from "./classroom";

const mocks = vi.hoisted(() => ({
  listClassStudents: vi.fn(), listClassBroadcastSources: vi.fn(), listSessions: vi.fn(), getLive: vi.fn(),
}));
vi.mock("../services/classroom", () => ({ ClassroomService: mocks }));

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

const countStudents = (students) => students.length;

describe("class-scoped queries", () => {
  function Probe({ classId }) {
    const students = useClassStudentsQuery(classId, { refetchInterval: false, select: countStudents });
    const sources = useClassBroadcastSourcesQuery(classId);
    const cache = useClassroomCacheUpdater();
    seen = { students: students.data, sources: sources.data, cache };
    return null;
  }

  test("students and broadcast sources are fetched per class; a missing class id fetches nothing", async () => {
    mocks.listClassStudents.mockResolvedValue([{ id: 1 }, { id: 2 }]);
    mocks.listClassBroadcastSources.mockResolvedValue([{ vmid: 900 }]);
    await act(async () => { root.render(withQueryClient(<Probe classId={undefined} />)); });
    await flush();
    expect(mocks.listClassStudents).not.toHaveBeenCalled();

    await act(async () => { root.render(withQueryClient(<Probe classId="c1" />)); });
    await flush();
    expect(mocks.listClassStudents).toHaveBeenCalledWith("c1");
    expect(seen.students).toBe(2);
    expect(seen.sources).toEqual([{ vmid: 900 }]);
  });

  test("invalidateStudents() refetches only that class's roster", async () => {
    mocks.listClassStudents.mockResolvedValue([{ id: 1 }]);
    mocks.listClassBroadcastSources.mockResolvedValue([]);
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<Probe classId="c1" />, client)); });
    await flush();

    mocks.listClassStudents.mockResolvedValue([{ id: 1 }, { id: 2 }, { id: 3 }]);
    await act(async () => { await seen.cache.invalidateStudents("c1"); });
    await flush();
    expect(seen.students).toBe(3);
    expect(mocks.listClassBroadcastSources).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(classroomKeys.students("c1"))).toHaveLength(3);
  });
});

describe("useClassroomSessionsQuery", () => {
  function SessionsProbe() {
    const sessions = useClassroomSessionsQuery({ refetchInterval: false });
    seen = { data: sessions.data, isError: sessions.isError };
    return null;
  }

  test("lists running classroom sessions", async () => {
    mocks.listSessions.mockResolvedValue([{ id: "b1", mode: "broadcast" }]);
    await act(async () => { root.render(withQueryClient(<SessionsProbe />)); });
    await flush();
    expect(seen.data).toEqual([{ id: "b1", mode: "broadcast" }]);
  });
});

describe("useClassroomLiveQuery", () => {
  function LiveProbe({ enabled }) {
    const live = useClassroomLiveQuery({ enabled });
    seen = { refetch: live.refetch };
    return null;
  }

  test("with enabled: false nothing is fetched until the caller refetches, and refetch resolves with the snapshot", async () => {
    mocks.getLive.mockResolvedValue({ session: { id: "s1" }, taken_over_vmids: [101] });
    await act(async () => { root.render(withQueryClient(<LiveProbe enabled={false} />)); });
    await flush();
    expect(mocks.getLive).not.toHaveBeenCalled();

    let result;
    await act(async () => { result = await seen.refetch(); });
    expect(mocks.getLive).toHaveBeenCalledTimes(1);
    expect(result.status).toBe("success");
    expect(result.data).toEqual({ session: { id: "s1" }, taken_over_vmids: [101] });
  });

  test("once a snapshot exists, a newer refetch cancels the older one and both resolve with the newest snapshot", async () => {
    let finishSlow;
    let finishLatest;
    mocks.getLive
      .mockResolvedValueOnce({ session: null })
      .mockImplementationOnce(() => new Promise((resolve) => { finishSlow = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { finishLatest = resolve; }));
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<LiveProbe enabled={false} />, client)); });
    await act(async () => { await seen.refetch(); });

    let slow;
    let latest;
    await act(async () => {
      slow = seen.refetch();
      latest = seen.refetch();
    });
    /* 被取代的那次不會先拿舊快照回來：等較新的那次回來，兩邊拿到同一份（ClassroomStudentLayer 套用兩次也一樣） */
    await act(async () => { finishLatest({ session: { id: "s2" } }); });
    const [slowResult, latestResult] = await Promise.all([slow, latest]);
    expect(slowResult.data).toEqual({ session: { id: "s2" } });
    expect(latestResult.fetchStatus).toBe("idle");
    expect(latestResult.data).toEqual({ session: { id: "s2" } });

    /* 被取消的那支晚回來也不會蓋掉 */
    await act(async () => { finishSlow({ session: { id: "stale" } }); });
    await flush();
    expect(client.getQueryData(classroomKeys.live)).toEqual({ session: { id: "s2" } });
  });
});
