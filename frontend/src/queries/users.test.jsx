// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  useAllUsersQuery,
  usePendingRegistrationsQuery,
  useRegistrationPolicyQuery,
  useUserCacheUpdater,
} from "./users";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  listPendingRegistrations: vi.fn(),
  getRegistrationPolicy: vi.fn(),
}));
vi.mock("../services/users", () => ({ UsersService: mocks }));

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const user = (id, extra = {}) => ({ id, email: `${id}@example.edu`, ...extra });

let host;
let root;
let client;
let seen;

function Probe() {
  const users = useAllUsersQuery({ refetchInterval: false });
  const pending = usePendingRegistrationsQuery({ refetchInterval: false });
  const policy = useRegistrationPolicyQuery();
  seen = {
    users: users.data,
    refetchUsers: users.refetch,
    pending: pending.data,
    policy: policy.data,
    cache: useUserCacheUpdater(),
  };
  return null;
}

async function renderProbe() {
  await act(async () => { root.render(withQueryClient(<Probe />, client)); });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listPendingRegistrations.mockResolvedValue({ data: [user("p")] });
  mocks.getRegistrationPolicy.mockResolvedValue({ signup_enabled: true });
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

describe("useUserCacheUpdater", () => {
  test("update / updateMany / patchUser / prepend / remove / removeMany patch the list without refetching", async () => {
    mocks.listAll.mockResolvedValue([user("a"), user("b"), user("c")]);
    await renderProbe();
    await flush();

    await act(async () => { await seen.cache.update(user("a", { full_name: "A" })); });
    await act(async () => { await seen.cache.updateMany([user("b", { is_active: false })]); });
    await act(async () => { await seen.cache.patchUser("c", { totp_enabled: false }); });
    await act(async () => { await seen.cache.prepend(user("n")); });
    await flush();
    expect(seen.users).toEqual([
      user("n"),
      user("a", { full_name: "A" }),
      user("b", { is_active: false }),
      user("c", { totp_enabled: false }),
    ]);

    await act(async () => { await seen.cache.remove("n"); });
    await act(async () => { await seen.cache.removeMany(["a", "b"]); });
    await flush();
    expect(seen.users).toEqual([user("c", { totp_enabled: false })]);
    expect(mocks.listAll).toHaveBeenCalledTimes(1);
  });

  test("a refresh sent before a local change cannot overwrite it", async () => {
    const stale = deferred();
    mocks.listAll
      .mockResolvedValueOnce([user("a"), user("b")])
      .mockReturnValueOnce(stale.promise);
    await renderProbe();
    await flush();

    act(() => { seen.refetchUsers(); });
    await act(async () => { await seen.cache.remove("b"); });
    await act(async () => { stale.resolve([user("a"), user("b")]); });
    await flush();

    expect(seen.users).toEqual([user("a")]);
  });

  test("a change made while the first load is still in flight restarts the load so it is not lost", async () => {
    const firstLoad = deferred();
    mocks.listAll
      .mockReturnValueOnce(firstLoad.promise)
      .mockResolvedValueOnce([user("n"), user("a")]);
    await renderProbe();

    await act(async () => { await seen.cache.prepend(user("n")); });
    await flush();
    expect(mocks.listAll).toHaveBeenCalledTimes(2);

    /* 改動前送出的那次晚回來（看不到新帳號）：已作廢，不會蓋掉 */
    await act(async () => { firstLoad.resolve([user("a")]); });
    await flush();
    expect(seen.users).toEqual([user("n"), user("a")]);
  });

  test("removePendingRegistration and setRegistrationPolicy update their own caches", async () => {
    mocks.listAll.mockResolvedValue([]);
    await renderProbe();
    await flush();

    await act(async () => { await seen.cache.removePendingRegistration("p"); });
    await act(async () => { await seen.cache.setRegistrationPolicy({ signup_enabled: false }); });
    await flush();

    expect(seen.pending).toEqual({ data: [] });
    expect(seen.policy).toEqual({ signup_enabled: false });
    expect(mocks.listPendingRegistrations).toHaveBeenCalledTimes(1);
    expect(mocks.getRegistrationPolicy).toHaveBeenCalledTimes(1);
  });

  test("invalidate refetches users, pending registrations and the policy", async () => {
    mocks.listAll.mockResolvedValue([]);
    await renderProbe();
    await flush();

    await act(async () => { await seen.cache.invalidate(); });
    expect(mocks.listAll).toHaveBeenCalledTimes(2);
    expect(mocks.listPendingRegistrations).toHaveBeenCalledTimes(2);
    expect(mocks.getRegistrationPolicy).toHaveBeenCalledTimes(2);
  });
});
