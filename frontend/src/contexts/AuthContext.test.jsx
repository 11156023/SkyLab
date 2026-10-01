// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const storage = vi.hoisted(() => ({ sessionId: "s1" }));

vi.mock("sonner", () => ({
  toast: { dismiss: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));
// t 必須是穩定參照（真實的 react-i18next 也是），否則 callback 依賴一直變會重跑 effect。
const i18nStub = vi.hoisted(() => ({ t: (key) => key }));
vi.mock("react-i18next", () => ({ useTranslation: () => i18nStub }));
vi.mock("../services/auth", () => ({
  AuthStorage: {
    getSnapshot: () => ({ sessionId: storage.sessionId, refreshToken: null }),
    isSameSession: ({ sessionId }) => sessionId === storage.sessionId,
    isLoggedIn: () => storage.sessionId !== null,
    getTokenExpiry: () => null,
    clearTokens: () => { storage.sessionId = null; },
    setTokens: () => { storage.sessionId = "s2"; },
    isRelevantStorageKey: () => false,
    clearTokensIfCurrent: () => true,
  },
  loginLdap: vi.fn(),
  loginTotp: vi.fn(),
  turnstileHeaders: (token) => (token ? { "X-Turnstile-Token": token } : {}),
}));
vi.mock("../services/api", () => ({
  apiPost: vi.fn(() => Promise.resolve({})),
  apiPostForm: vi.fn(() => Promise.resolve({ access_token: "a", refresh_token: "r" })),
  refreshTokens: vi.fn(),
}));
vi.mock("../services/authSession", () => ({
  AuthSessionStatus: {
    CHECKING: "checking",
    AUTHENTICATED: "authenticated",
    ANONYMOUS: "anonymous",
    UNAVAILABLE: "unavailable",
  },
  restoreStoredSession: vi.fn(() => Promise.resolve({
    status: "authenticated",
    user: { id: "u" },
    error: null,
  })),
}));
vi.mock("../services/webPush", () => ({ unsubscribePush: vi.fn(() => Promise.resolve()) }));
vi.mock("../services/aiContextualHelp", () => ({
  AiContextualHelpService: { resetSurfaces: vi.fn() },
}));

import { AiContextualHelpService } from "../services/aiContextualHelp";
import { AuthProvider, useAuth } from "./AuthContext";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
let auth;

function Grab() {
  auth = useAuth();
  return null;
}

beforeEach(async () => {
  storage.sessionId = "s1";
  AiContextualHelpService.resetSurfaces.mockClear();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => root.render(<AuthProvider><Grab /></AuthProvider>));
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

test("身分依附的 AI 畫面清單快取在登出、換帳號登入時清掉，同一個 session 重驗不清", async () => {
  // 初次還原 s1：快取歸屬從「無」變成 s1
  expect(AiContextualHelpService.resetSurfaces).toHaveBeenCalledTimes(1);

  // 同一個 session 重驗不必清
  await act(async () => { await auth.retrySession(); });
  expect(AiContextualHelpService.resetSurfaces).toHaveBeenCalledTimes(1);

  await act(async () => { auth.logout(); });
  expect(AiContextualHelpService.resetSurfaces).toHaveBeenCalledTimes(2);

  // 同一分頁用另一個帳號登入，新的 sessionId 也要清
  await act(async () => { await auth.login("student", "pw"); });
  expect(AiContextualHelpService.resetSurfaces).toHaveBeenCalledTimes(3);
});

test("token 失效被踢出時也清快取", async () => {
  expect(AiContextualHelpService.resetSurfaces).toHaveBeenCalledTimes(1);
  await act(async () => { window.dispatchEvent(new Event("auth:unauthorized")); });
  expect(AiContextualHelpService.resetSurfaces).toHaveBeenCalledTimes(2);
});
