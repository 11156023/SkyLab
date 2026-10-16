// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import LoginPage from "./LoginPage";
import { AccountService } from "../../services/account";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: key => key }) }));
vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user", email: "user@example.org" } })
}));
vi.mock("../../services/auth", () => ({ getLoginMethods: async () => ({}) }));
vi.mock("../../services/account", () => ({
  AccountService: { approveDesktopDevice: vi.fn(), declineDesktopDevice: vi.fn() }
}));
vi.mock("./PageShell", () => ({ default: ({ children }) => <div>{children}</div> }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root;
let host;
let redirect;

beforeEach(() => {
  vi.clearAllMocks();
  window.google = { accounts: { id: { initialize() {}, renderButton() {} } } };
  window.history.replaceState({}, "", "/login?device_code=test-device");
  redirect = vi.spyOn(window.location, "replace").mockImplementation(() => {});
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

const button = key => [...host.querySelectorAll("button")].find(node => node.textContent === key);
const click = node => node.dispatchEvent(new MouseEvent("click", { bubbles: true }));

test("decline reaches the backend before leaving the authorization page, even on double click", async () => {
  let finish;
  AccountService.declineDesktopDevice.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => root.render(<LoginPage />));
  const decline = button("LoginPage.deviceConsentDecline");
  await act(async () => { click(decline); click(decline); });
  expect(AccountService.declineDesktopDevice).toHaveBeenCalledExactlyOnceWith("test-device");
  expect(AccountService.approveDesktopDevice).not.toHaveBeenCalled();
  expect(redirect).not.toHaveBeenCalled();
  await act(async () => finish({ status: "denied" }));
  expect(redirect).toHaveBeenCalledExactlyOnceWith("/");
  expect(window.location.search).toBe("");
});

test("failed cancellation keeps the code and shows the error without redirecting", async () => {
  AccountService.declineDesktopDevice.mockRejectedValueOnce(new Error("backend unavailable"));
  await act(async () => root.render(<LoginPage />));
  await act(async () => click(button("LoginPage.deviceConsentDecline")));
  expect(host.textContent).toContain("backend unavailable");
  expect(redirect).not.toHaveBeenCalled();
  expect(window.location.search).toBe("?device_code=test-device");
});

test.each([true, false])("approval and cancellation cannot both be submitted (approve first: %s)", async (approveFirst) => {
  AccountService.approveDesktopDevice.mockImplementation(() => new Promise(() => {}));
  AccountService.declineDesktopDevice.mockImplementation(() => new Promise(() => {}));
  await act(async () => root.render(<LoginPage />));
  const approve = button("LoginPage.deviceConsentApprove");
  const decline = button("LoginPage.deviceConsentDecline");
  await act(async () => {
    click(approveFirst ? approve : decline);
    click(approveFirst ? decline : approve);
  });
  expect(AccountService.approveDesktopDevice).toHaveBeenCalledTimes(approveFirst ? 1 : 0);
  expect(AccountService.declineDesktopDevice).toHaveBeenCalledTimes(approveFirst ? 0 : 1);
});
