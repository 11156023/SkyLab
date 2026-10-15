// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, test, vi } from "vitest";
import UserGuide from "./UserGuide";

const state = vi.hoisted(() => ({ user: null, updateUser: vi.fn(), markSeen: vi.fn() }));
vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => state }));
vi.mock("../../services/account", () => ({ AccountService: { markUserGuideSeen: state.markSeen } }));
vi.mock("react-router-dom", () => ({ useLocation: () => ({ pathname: "/dashboard" }) }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
let root, host;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  localStorage.clear(); sessionStorage.clear();
  state.user = { id: "u1", role: "student", seen_user_guides: [] };
  state.markSeen.mockReset().mockResolvedValue({ seen_user_guides: ["student-home"] });
  state.updateUser.mockReset();
  document.body.innerHTML = '<h1>Home</h1><span data-user-guide-slot></span><div data-guide="home-schedule"></div>';
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.useRealTimers(); document.body.innerHTML = "";
});
async function render() {
  await act(async () => root.render(<UserGuide />));
  await act(async () => vi.advanceTimersByTimeAsync(600));
}
test("first display persists viewing even without finishing the tour", async () => {
  await render();
  expect(state.markSeen).toHaveBeenCalledWith("student-home");
  expect(state.updateUser).toHaveBeenCalledWith({ seen_user_guides: ["student-home"] });
  expect(localStorage.getItem("skylab:user-guide:seen:u1:student-home")).toBe("completed");
});
test("account history suppresses automatic display with empty browser storage, but permits manual replay", async () => {
  state.user.seen_user_guides = ["student-home"];
  await render();
  expect(localStorage.length).toBe(0);
  await act(async () => document.querySelector("[data-user-guide-trigger]").click());
  await act(async () => vi.advanceTimersByTimeAsync(100));
  expect(localStorage.getItem("skylab:user-guide:seen:u1:student-home")).toBe("completed");
  expect(state.markSeen).not.toHaveBeenCalled();
});
test("a browser marker retries account sync after an earlier failed save", async () => {
  localStorage.setItem("skylab:user-guide:seen:u1:student-home", "completed");
  await render();
  expect(state.markSeen).toHaveBeenCalledWith("student-home");
});
