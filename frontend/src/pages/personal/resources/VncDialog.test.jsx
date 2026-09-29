// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const { service, i18n } = vi.hoisted(() => ({
  service: { getConsole: vi.fn() },
  i18n: { t: (key) => key, i18n: { language: "zh-TW" } },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => i18n }));
vi.mock("react-vnc", () => ({ VncScreen: () => <div data-testid="vnc-screen" /> }));
vi.mock("../../../services/resources", () => ({ ResourcesService: service }));
vi.mock("../../../services/auth", () => ({ AuthStorage: { getAccessToken: () => "token" } }));
vi.mock("../../../services/recentMachines", () => ({ recordMachineUse: vi.fn() }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1" } }) }));
vi.mock("../../../components/Classroom/ClassroomStudentLayer", () => ({ useClassroomTakeover: () => false }));
vi.mock("../../../components/Classroom/TakeoverOverlay", () => ({ default: () => null }));
vi.mock("../../../components/Modal/Modal", () => ({ default: ({ children }) => <div>{children}</div> }));

import VncDialog from "./VncDialog";

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
});

it("clears the timeout banner when a slow console request finally succeeds", async () => {
  let resolveConsole;
  service.getConsole.mockReturnValue(new Promise((resolve) => { resolveConsole = resolve; }));
  await act(async () => root.render(<VncDialog resource={{ vmid: 105, name: "lab" }} onClose={() => {}} />));

  await act(async () => { vi.advanceTimersByTime(16_000); });
  expect(host.textContent).toContain("VncDialog.timeoutError");

  await act(async () => { resolveConsole({ ticket: "PVEVNC:abc", port: "5900" }); });
  expect(host.querySelector('[data-testid="vnc-screen"]')).not.toBeNull();
  expect(host.textContent).not.toContain("VncDialog.timeoutError");
});
