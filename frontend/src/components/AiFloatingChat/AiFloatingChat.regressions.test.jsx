// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AiFloatingChat from "./AiFloatingChat";
import { LayoutContext } from "../../layout/layoutContext";
import { AiNavigationService } from "../../services/aiNavigation";
import translations from "../../locales/zh-TW/components.json";

const confirmLeave = vi.fn();
const onOpenChange = vi.fn();

vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "student" } }) }));
vi.mock("../../contexts/UnsavedChangesContext", () => ({ useUnsavedChanges: () => ({ confirmLeave }) }));
vi.mock("react-i18next", async (importOriginal) => ({ ...(await importOriginal()), useTranslation: () => ({ t: translate }) }));
vi.mock("../../services/aiNavigation", () => ({ AiNavigationService: { resolve: vi.fn(), intake: vi.fn() } }));
vi.mock("../../services/aiTemplateRecommendation", () => ({ AiTemplateRecommendationApi: { chat: vi.fn(), recommend: vi.fn() } }));
vi.mock("../../services/aiContextualHelp", () => ({
  AiContextualHelpService: { surfaces: vi.fn().mockResolvedValue([]), explain: vi.fn() }, matchSurface: () => null,
}));
vi.mock("../../services/resources", () => ({ ResourcesService: { list: vi.fn() } }));
vi.mock("../../services/vmRequests", () => ({ VmRequestsService: { list: vi.fn(), create: vi.fn() } }));

function translate(key, vars = {}) {
  return Object.entries(vars).reduce((text, [name, value]) => text.replaceAll(`{{${name}}}`, value), translations[key] ?? key);
}

let host, root;

function Harness() {
  const location = useLocation();
  return <LayoutContext.Provider value={{ requestForm: null, surface: null, requestSubmission: null }}>
    <span data-testid="path">{location.pathname}</span>
    <AiFloatingChat open onOpenChange={onOpenChange} />
  </LayoutContext.Provider>;
}

function setOverlay(matches) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
}

async function renderChat() {
  await act(async () => root.render(<MemoryRouter initialEntries={["/dashboard"]}><Harness /></MemoryRouter>));
}

async function askForFirewall() {
  const input = host.querySelector("textarea");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "帶我去防火牆");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => host.querySelector(`button[aria-label="${translate("AiFloatingChat.sendMessageAriaLabel")}"]`).click());
}

async function clickTarget() {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent.includes("防火牆設定"));
  expect(button).toBeTruthy();
  await act(async () => button.click());
}

const path = () => host.querySelector('[data-testid="path"]').textContent;

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  AiNavigationService.resolve.mockResolvedValue({
    action: "navigate", answer: "這裡可以設定", primary: { path: "/firewall", title: "防火牆設定" }, suggestions: [],
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

test("declining the unsaved-changes prompt keeps the current page", async () => {
  setOverlay(false);
  await renderChat();
  await askForFirewall();
  confirmLeave.mockResolvedValue(false);
  await clickTarget();
  expect(confirmLeave).toHaveBeenCalledTimes(1);
  expect(path()).toBe("/dashboard");
});

test("docked mode asks with the panel open and navigates once confirmed", async () => {
  setOverlay(false);
  await renderChat();
  await askForFirewall();
  confirmLeave.mockResolvedValue(true);
  await clickTarget();
  expect(path()).toBe("/firewall");
  expect(onOpenChange).not.toHaveBeenCalledWith(false);
});

test("overlay mode closes the panel before asking so the dialog is not covered", async () => {
  setOverlay(true);
  await renderChat();
  await askForFirewall();
  confirmLeave.mockImplementation(async () => {
    expect(onOpenChange).toHaveBeenCalledWith(false);
    return false;
  });
  await clickTarget();
  expect(confirmLeave).toHaveBeenCalledTimes(1);
  expect(path()).toBe("/dashboard");
});
