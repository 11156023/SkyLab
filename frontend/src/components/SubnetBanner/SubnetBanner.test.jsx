// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { getStatus } = vi.hoisted(() => ({ getStatus: vi.fn() }));

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));
vi.mock("../../services/ipManagement", () => ({ IpManagementService: { getStatus } }));

import SubnetBanner, { SUBNET_CHANGED_EVENT } from "./SubnetBanner";
import { withQueryClient } from "../../queries/testing";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

/* TanStack Query 以 macrotask 批次通知，先等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  getStatus.mockReset();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

test("the banner re-checks the subnet status when the subnet is saved or deleted", async () => {
  getStatus.mockResolvedValueOnce({ configured: false });
  await act(async () =>
    root.render(withQueryClient(
      <MemoryRouter>
        <SubnetBanner />
      </MemoryRouter>,
    )),
  );
  await flush();
  expect(host.textContent).toContain("SubnetBanner.adminMessage");

  getStatus.mockResolvedValueOnce({ configured: true });
  await act(async () => window.dispatchEvent(new Event(SUBNET_CHANGED_EVENT)));
  await flush();
  expect(host.textContent).toBe("");

  getStatus.mockResolvedValueOnce({ configured: false });
  await act(async () => window.dispatchEvent(new Event(SUBNET_CHANGED_EVENT)));
  await flush();
  expect(host.textContent).toContain("SubnetBanner.adminMessage");
});

test("the banner re-checks the subnet status on page change once the last check is a minute old", async () => {
  const nowSpy = vi.spyOn(Date, "now").mockReturnValue(1_000_000);
  let navigate;
  function Nav() {
    navigate = useNavigate();
    return null;
  }
  getStatus.mockResolvedValueOnce({ configured: false });
  await act(async () =>
    root.render(withQueryClient(
      <MemoryRouter initialEntries={["/ip-management"]}>
        <Nav />
        <SubnetBanner />
      </MemoryRouter>,
    )),
  );
  await flush();
  expect(host.textContent).toContain("SubnetBanner.adminMessage");
  expect(getStatus).toHaveBeenCalledTimes(1);

  // 一分鐘內換頁不重查（每個使用者每次換頁都打一次太多）
  await act(async () => navigate("/resources"));
  await flush();
  expect(getStatus).toHaveBeenCalledTimes(1);

  nowSpy.mockReturnValue(1_000_000 + 60_000);
  getStatus.mockResolvedValueOnce({ configured: true });
  await act(async () => navigate("/firewall"));
  await flush();
  expect(getStatus).toHaveBeenCalledTimes(2);
  expect(host.textContent).toBe("");
  nowSpy.mockRestore();
});

test("a page change while the first check is in flight still shows the banner", async () => {
  let navigate;
  function Nav() {
    navigate = useNavigate();
    return null;
  }
  let resolveStatus;
  getStatus.mockReturnValueOnce(new Promise((resolve) => { resolveStatus = resolve; }));
  await act(async () =>
    root.render(withQueryClient(
      <MemoryRouter initialEntries={["/"]}>
        <Nav />
        <SubnetBanner />
      </MemoryRouter>,
    )),
  );
  // 例如 / 立刻轉址到 /dashboard：查詢進行中不重發，第一次的結果不能被丟掉
  await act(async () => navigate("/dashboard"));
  await flush();
  expect(getStatus).toHaveBeenCalledTimes(1);

  await act(async () => resolveStatus({ configured: false }));
  await flush();
  expect(host.textContent).toContain("SubnetBanner.adminMessage");
});

test("a failed re-check keeps the last known status", async () => {
  getStatus.mockResolvedValueOnce({ configured: false });
  await act(async () =>
    root.render(withQueryClient(
      <MemoryRouter>
        <SubnetBanner />
      </MemoryRouter>,
    )),
  );
  await flush();
  expect(host.textContent).toContain("SubnetBanner.adminMessage");

  getStatus.mockRejectedValueOnce(new Error("503"));
  await act(async () => window.dispatchEvent(new Event(SUBNET_CHANGED_EVENT)));
  await flush();
  expect(getStatus).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("SubnetBanner.adminMessage");
});

test("the listener is removed on unmount", async () => {
  getStatus.mockResolvedValue({ configured: true });
  await act(async () =>
    root.render(withQueryClient(
      <MemoryRouter>
        <SubnetBanner />
      </MemoryRouter>,
    )),
  );
  await flush();
  await act(async () => root.unmount());
  root = createRoot(host);
  getStatus.mockClear();
  window.dispatchEvent(new Event(SUBNET_CHANGED_EVENT));
  expect(getStatus).not.toHaveBeenCalled();
});
