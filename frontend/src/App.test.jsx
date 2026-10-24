// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AuthSessionStatus } from "./services/authSession";

const auth = vi.hoisted(() => ({ value: null }));

vi.mock("./contexts/AuthContext", () => ({ useAuth: () => auth.value }));
vi.mock("./pages/setup/useSetupStatus", () => ({
  useSetupStatus: () => ({ status: { completed: true }, loading: false, setupRequired: false }),
}));
vi.mock("./pages/login/LoginPage", () => ({ default: () => <p data-testid="login">login</p> }));
vi.mock("./layout/DashboardLayout", () => ({ default: () => null }));

import App from "./App";

function CurrentPath() {
  return <output data-testid="path">{useLocation().pathname}</output>;
}

let host;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  auth.value = {
    user: null,
    loading: false,
    authStatus: AuthSessionStatus.ANONYMOUS,
    authError: null,
    retrySession: () => {},
    loginPreflightPending: false,
  };
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

/* 介紹頁已搬到獨立官網（site/）：平台本身未登入時 / 跟其他路徑一樣直接進登入頁 */
test.each(["/", "/my-resources"])("未登入開 %s 會導向登入頁", async (path) => {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <App />
        <CurrentPath />
      </MemoryRouter>,
    );
  });

  expect(host.querySelector('[data-testid="path"]').textContent).toBe("/login");
  expect(host.querySelector('[data-testid="login"]')).not.toBeNull();
});
