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

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

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
    root.render(
      <MemoryRouter>
        <SubnetBanner />
      </MemoryRouter>,
    ),
  );
  expect(host.textContent).toContain("SubnetBanner.adminMessage");

  getStatus.mockResolvedValueOnce({ configured: true });
  await act(async () => window.dispatchEvent(new Event(SUBNET_CHANGED_EVENT)));
  expect(host.textContent).toBe("");

  getStatus.mockResolvedValueOnce({ configured: false });
  await act(async () => window.dispatchEvent(new Event(SUBNET_CHANGED_EVENT)));
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
    root.render(
      <MemoryRouter initialEntries={["/ip-management"]}>
        <Nav />
        <SubnetBanner />
      </MemoryRouter>,
    ),
  );
  expect(host.textContent).toContain("SubnetBanner.adminMessage");
  expect(getStatus).toHaveBeenCalledTimes(1);

  // 一分鐘內換頁不重查（每個使用者每次換頁都打一次太多）
  await act(async () => navigate("/resources"));
  expect(getStatus).toHaveBeenCalledTimes(1);

  nowSpy.mockReturnValue(1_000_000 + 60_000);
  getStatus.mockResolvedValueOnce({ configured: true });
  await act(async () => navigate("/firewall"));
  expect(getStatus).toHaveBeenCalledTimes(2);
  expect(host.textContent).toBe("");
  nowSpy.mockRestore();
});

test("the listener is removed on unmount", async () => {
  getStatus.mockResolvedValue({ configured: true });
  await act(async () =>
    root.render(
      <MemoryRouter>
        <SubnetBanner />
      </MemoryRouter>,
    ),
  );
  await act(async () => root.unmount());
  root = createRoot(host);
  getStatus.mockClear();
  window.dispatchEvent(new Event(SUBNET_CHANGED_EVENT));
  expect(getStatus).not.toHaveBeenCalled();
});
