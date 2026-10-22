// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({ getStatus: vi.fn() }));
vi.mock("../../services/setup", () => ({ SetupService: { getStatus: mocks.getStatus } }));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const INCOMPLETE = { completed: false, steps: { admin: false, proxmox: false, subnet: false } };
const COMPLETED = { completed: true, steps: { admin: true, proxmox: true, subnet: true } };

/* fetchSetupStatus／markSetupCompleted 用的是 App 唯一的 queryClient：每個測試重新載入模組拿一個新的 */
let mod;
let queryClient;
let withQueryClient;
let host;
let root;
let seen;

function Probe({ name }) {
  const value = mod.useSetupStatus();
  seen[name] = value;
  return null;
}

async function mount(ui) {
  await act(async () => { root.render(withQueryClient(ui, queryClient)); });
}

beforeEach(async () => {
  vi.resetAllMocks();
  vi.resetModules();
  mod = await import("./useSetupStatus");
  ({ queryClient } = await import("../../queries/queryClient"));
  ({ withQueryClient } = await import("../../queries/testing"));
  seen = {};
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  queryClient.clear();
});

describe("useSetupStatus", () => {
  test("loading until the status arrives; an incomplete setup requires the wizard", async () => {
    const status = deferred();
    mocks.getStatus.mockReturnValue(status.promise);
    await mount(<Probe name="login" />);
    expect(seen.login).toMatchObject({ status: null, loading: true, setupRequired: false });

    await act(async () => { status.resolve(INCOMPLETE); });
    await flush();
    expect(seen.login).toMatchObject({ status: INCOMPLETE, loading: false, error: null, setupRequired: true });
  });

  test("backend unreachable: not loading, no status, setup not required; refresh asks again", async () => {
    mocks.getStatus.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(COMPLETED);
    await mount(<Probe name="setup" />);
    await flush();
    expect(seen.setup.loading).toBe(false);
    expect(seen.setup.status).toBeNull();
    expect(seen.setup.setupRequired).toBe(false);
    expect(seen.setup.error).toMatchObject({ message: "offline" });

    let refreshed;
    await act(async () => { refreshed = seen.setup.refresh(); });
    await flush();
    await expect(refreshed).resolves.toEqual(COMPLETED);
    expect(seen.setup).toMatchObject({ status: COMPLETED, loading: false, error: null, setupRequired: false });
    expect(mocks.getStatus).toHaveBeenCalledTimes(2);
  });

  test("refresh resolves null when the retry fails as well", async () => {
    mocks.getStatus.mockRejectedValue(new Error("offline"));
    await mount(<Probe name="setup" />);
    await flush();

    let refreshed;
    await act(async () => { refreshed = seen.setup.refresh(); });
    await flush();
    await expect(refreshed).resolves.toBeNull();
    expect(seen.setup.error).toMatchObject({ message: "offline" });
  });

  test("markSetupCompleted updates mounted hooks without calling the API", async () => {
    mocks.getStatus.mockResolvedValue(INCOMPLETE);
    await mount(<><Probe name="login" /><Probe name="setup" /></>);
    await flush();
    expect(seen.login.setupRequired).toBe(true);

    act(() => { mod.markSetupCompleted(); });
    await flush();
    expect(seen.login.status).toEqual({ ...INCOMPLETE, completed: true });
    expect(seen.login.setupRequired).toBe(false);
    expect(seen.setup.setupRequired).toBe(false);
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
  });

  test("markSetupCompleted before any status was fetched still marks it completed", async () => {
    act(() => { mod.markSetupCompleted(); });
    await mount(<Probe name="login" />);
    await flush();
    expect(seen.login.status).toEqual({ steps: { admin: true, proxmox: false, subnet: false }, completed: true });
    expect(seen.login.loading).toBe(false);
    expect(mocks.getStatus).not.toHaveBeenCalled();
  });

  test("AuthContext's queryClient.clear() keeps the status: no spinner, no redirect flip, no new request", async () => {
    mocks.getStatus.mockResolvedValue(COMPLETED);
    await mount(<Probe name="login" />);
    await flush();
    expect(seen.login.status).toEqual(COMPLETED);

    /* 已掛著的登入頁（例如裝置授權頁登入後）：換身分清快取後重繪，畫面不變 */
    queryClient.clear();
    await mount(<Probe name="login" />);
    expect(seen.login).toMatchObject({ status: COMPLETED, loading: false, setupRequired: false });

    /* 登出後重新掛上的登入頁：第一次 render 就有狀態 */
    act(() => root.render(withQueryClient(<></>, queryClient)));
    queryClient.clear();
    await mount(<Probe name="afterLogout" />);
    expect(seen.afterLogout).toMatchObject({ status: COMPLETED, loading: false, setupRequired: false });
    await flush();
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
  });

  test("markSetupCompleted survives the clear() that the wizard's auto-login triggers", async () => {
    mocks.getStatus.mockResolvedValue(INCOMPLETE);
    await mount(<Probe name="setup" />);
    await flush();

    act(() => { mod.markSetupCompleted(); });
    act(() => root.render(withQueryClient(<></>, queryClient)));
    queryClient.clear();
    await mount(<Probe name="login" />);
    expect(seen.login).toMatchObject({ status: { ...INCOMPLETE, completed: true }, setupRequired: false });
    await flush();
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
  });
});

describe("fetchSetupStatus", () => {
  test("shares one request, returns the cached status afterwards, and force asks again", async () => {
    const first = deferred();
    mocks.getStatus.mockReturnValueOnce(first.promise).mockResolvedValueOnce(COMPLETED);

    const a = mod.fetchSetupStatus();
    const b = mod.fetchSetupStatus();
    first.resolve(INCOMPLETE);
    await expect(a).resolves.toEqual(INCOMPLETE);
    await expect(b).resolves.toEqual(INCOMPLETE);
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);

    await expect(mod.fetchSetupStatus()).resolves.toEqual(INCOMPLETE);
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);

    await expect(mod.fetchSetupStatus({ force: true })).resolves.toEqual(COMPLETED);
    expect(mocks.getStatus).toHaveBeenCalledTimes(2);
  });

  test("rejects when the backend is unreachable", async () => {
    mocks.getStatus.mockRejectedValue(new Error("offline"));
    await expect(mod.fetchSetupStatus()).rejects.toThrow("offline");
  });
});
