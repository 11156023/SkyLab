// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import { useUserGroupCacheUpdater, useUserGroupMembersQuery, useUserGroupsQuery } from "./userGroups";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  members: vi.fn(),
}));
vi.mock("../services/userGroups", () => ({ UserGroupsService: mocks }));

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const group = (id, extra = {}) => ({ id, name: `Group ${id}`, member_count: 2, student_count: 1, ...extra });

let host;
let root;
let client;
let seen;

function Probe() {
  const [groupId, setGroupId] = useState("");
  const groups = useUserGroupsQuery();
  const members = useUserGroupMembersQuery(groupId);
  seen = {
    groups: groups.data,
    members: members.data,
    membersFetching: members.isFetching,
    setGroupId,
    cache: useUserGroupCacheUpdater(),
  };
  return null;
}

async function renderProbe() {
  await act(async () => { root.render(withQueryClient(<Probe />, client)); });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
});

describe("user group queries", () => {
  test("the list unwraps the response's data array", async () => {
    mocks.list.mockResolvedValue({ data: [group("a"), group("b")], count: 2 });
    await renderProbe();
    await flush();
    expect(seen.groups).toEqual([group("a"), group("b")]);
  });

  test("members are fetched only once a group is chosen", async () => {
    mocks.list.mockResolvedValue({ data: [group("a")], count: 1 });
    mocks.members.mockResolvedValue({ data: [{ id: "u1" }, { id: "u2" }], count: 2 });
    await renderProbe();
    await flush();
    expect(mocks.members).not.toHaveBeenCalled();
    expect(seen.members).toBeUndefined();

    /*
     * 要在 async act 裡選群組：同步 act 讓成員資料在 flush 排好計時器之後才回來，
     * TanStack 的通知計時器若輸給 act 收尾的 setImmediate，斷言時還沒拿到資料（偶發失敗）
     */
    await act(async () => { seen.setGroupId("a"); });
    await flush();
    expect(mocks.members).toHaveBeenCalledWith("a");
    expect(seen.members).toEqual([{ id: "u1" }, { id: "u2" }]);
  });

  test("invalidate refetches both the list and the chosen group's members", async () => {
    mocks.list
      .mockResolvedValueOnce({ data: [group("a")], count: 1 })
      .mockResolvedValueOnce({ data: [group("a", { member_count: 3 })], count: 1 });
    mocks.members
      .mockResolvedValueOnce({ data: [{ id: "u1" }], count: 1 })
      .mockResolvedValueOnce({ data: [{ id: "u1" }, { id: "u3" }], count: 2 });
    await renderProbe();
    act(() => { seen.setGroupId("a"); });
    await flush();

    await act(async () => { await seen.cache.invalidate(); });
    await flush();
    expect(seen.groups[0].member_count).toBe(3);
    expect(seen.members).toEqual([{ id: "u1" }, { id: "u3" }]);
  });
});
