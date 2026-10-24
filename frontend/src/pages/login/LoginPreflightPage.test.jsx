// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import LoginPreflightPage from "./LoginPreflightPage";
import { createTestQueryClient, withQueryClient } from "../../queries/testing";

const mocks = vi.hoisted(() => ({
  preflight: vi.fn(),
  finishLoginPreflight: vi.fn(),
  logout: vi.fn(),
  navigate: vi.fn(),
  user: null,
  t: (key) => key,
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: mocks.user,
    logout: mocks.logout,
    finishLoginPreflight: mocks.finishLoginPreflight,
  }),
}));
vi.mock("../../services/account", () => ({ AccountService: { preflight: mocks.preflight } }));

const KEYS = ["database", "redis", "worker", "pve", "gateway", "ai"];
const ALL_OK = { ok: true, detailed: true, checks: KEYS.map((key) => ({ key, status: "ok", components: [] })) };
const PVE_DOWN = {
  ok: false,
  detailed: true,
  checks: KEYS.map((key) => ({ key, status: key === "pve" ? "fail" : "ok", components: [] })),
};

/* MIN_CHECK_MS 900 → 6 項各 260 → 最後一項後再 260 → 通過停 700；
   Query 的通知走 setTimeout(0)（假計時器下算 1 ms），每個檢查點多留一點餘裕 */
const SLACK = 20;
const RESULT_AT = 900;
const SETTLED_AT = RESULT_AT + 6 * 260;
const FINISH_AT = SETTLED_AT + 700;

let host;
let root;
let client;

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  mocks.user = { role: "admin" };
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
  vi.useRealTimers();
});

async function advance(ms) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

async function renderPage() {
  await act(async () => { root.render(withQueryClient(<LoginPreflightPage />, client)); });
  await advance(0);
}

const title = () => host.querySelector("h1").textContent;
const count = (text) => host.textContent.split(text).length - 1;
const button = (text) => [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));

describe("LoginPreflightPage", () => {
  test("keeps spinning for the minimum time, ticks rows one by one, then enters the system", async () => {
    mocks.preflight.mockResolvedValue(ALL_OK);
    await renderPage();
    expect(mocks.preflight).toHaveBeenCalledWith({ refresh: false });
    expect(title()).toBe("LoginPreflight.admin.title");
    expect(count("LoginPreflight.status.checking")).toBe(6);

    /* API 早就回來了，但圈圈至少轉 900 ms */
    await advance(RESULT_AT - 10);
    expect(count("LoginPreflight.status.checking")).toBe(6);

    await advance(10 + SLACK);
    expect(count("LoginPreflight.status.ok")).toBe(1);
    expect(count("LoginPreflight.status.checking")).toBe(5);

    await advance(SETTLED_AT - RESULT_AT);
    expect(title()).toBe("LoginPreflight.admin.passedTitle");
    expect(count("LoginPreflight.status.ok")).toBe(6);
    expect(mocks.finishLoginPreflight).not.toHaveBeenCalled();

    await advance(FINISH_AT - SETTLED_AT);
    expect(mocks.finishLoginPreflight).toHaveBeenCalledTimes(1);
    expect(mocks.preflight).toHaveBeenCalledTimes(1);
  });

  test("a failed check offers retry; an admin retry skips the backend cache and starts over", async () => {
    mocks.preflight.mockResolvedValueOnce(PVE_DOWN).mockResolvedValueOnce(ALL_OK);
    await renderPage();
    await advance(SETTLED_AT + SLACK);
    expect(title()).toBe("LoginPreflight.admin.failedTitle");
    expect(count("LoginPreflight.status.fail")).toBe(1);

    await act(async () => { button("LoginPreflight.retry").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await advance(0);
    expect(mocks.preflight).toHaveBeenLastCalledWith({ refresh: true });
    /* 新的一輪從頭轉圈圈，不沿用上一輪的結果 */
    expect(title()).toBe("LoginPreflight.admin.title");
    expect(count("LoginPreflight.status.checking")).toBe(6);
    expect(button("LoginPreflight.retry")).toBeUndefined();

    await advance(FINISH_AT + SLACK);
    expect(mocks.finishLoginPreflight).toHaveBeenCalledTimes(1);
  });

  test("a student retry keeps using the backend cache", async () => {
    mocks.user = { role: "student" };
    mocks.preflight.mockResolvedValue(PVE_DOWN);
    await renderPage();
    await advance(SETTLED_AT + SLACK);
    expect(title()).toBe("LoginPreflight.user.failedTitle");

    await act(async () => { button("LoginPreflight.retry").dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await advance(0);
    expect(mocks.preflight).toHaveBeenCalledTimes(2);
    expect(mocks.preflight).toHaveBeenLastCalledWith({ refresh: false });
  });

  test("an old backend without the endpoint (404) lets everyone in without showing an error", async () => {
    mocks.preflight.mockRejectedValue({ status: 404, message: "Not Found" });
    await renderPage();
    await advance(SLACK);
    expect(mocks.finishLoginPreflight).toHaveBeenCalledTimes(1);
    expect(title()).toBe("LoginPreflight.admin.title");
    expect(host.querySelector("[role='alert']")).toBeNull();
  });

  test("other errors show the error state right away (no minimum wait)", async () => {
    mocks.preflight.mockRejectedValue({ status: 500, message: "db down" });
    await renderPage();
    /* 失敗不等最短顯示時間 */
    await advance(SLACK);
    expect(title()).toBe("LoginPreflight.admin.errorTitle");
    expect(count("LoginPreflight.status.fail")).toBe(6);
    expect(host.querySelector("[role='alert']")).not.toBeNull();
    expect(mocks.finishLoginPreflight).not.toHaveBeenCalled();
  });

  test("leaving mid-animation never enters the system afterwards", async () => {
    mocks.preflight.mockResolvedValue(ALL_OK);
    await renderPage();
    await advance(RESULT_AT + 300);
    expect(count("LoginPreflight.status.ok")).toBeGreaterThan(0);
    act(() => root.render(withQueryClient(<></>, client)));
    await advance(FINISH_AT + SLACK);
    expect(mocks.finishLoginPreflight).not.toHaveBeenCalled();
  });
});
