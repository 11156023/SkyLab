// @vitest-environment happy-dom
import { act, forwardRef, useEffect, useImperativeHandle } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const { service, i18n, vnc, toast } = vi.hoisted(() => ({
  service: { getConsole: vi.fn() },
  i18n: { t: (key) => key, i18n: { language: "zh-TW" } },
  vnc: { clipboardPaste: vi.fn(), props: null },
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => i18n }));
vi.mock("react-vnc", () => ({
  VncScreen: forwardRef(function FakeVnc(props, ref) {
    vnc.props = props;
    useImperativeHandle(ref, () => ({ clipboardPaste: vnc.clipboardPaste, sendCtrlAltDel: () => {} }));
    useEffect(() => {
      props.onConnect?.();
    }, [props.onConnect]);
    return <div data-testid="vnc-screen" />;
  }),
}));
vi.mock("../../../services/resources", () => ({ ResourcesService: service }));
vi.mock("../../../services/auth", () => ({ AuthStorage: { getAccessToken: () => "token" } }));
vi.mock("../../../services/recentMachines", () => ({ recordMachineUse: vi.fn() }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => toast }));
vi.mock("../../../components/Classroom/ClassroomStudentLayer", () => ({ useClassroomTakeover: () => false }));
vi.mock("../../../components/Classroom/TakeoverOverlay", () => ({ default: () => null }));
vi.mock("../../../components/Modal/Modal", () => ({ default: ({ children }) => <div>{children}</div> }));

import VncDialog from "./VncDialog";

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vnc.clipboardPaste.mockReset();
  vnc.props = null;
  Object.values(toast).forEach((fn) => fn.mockReset());
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
});

async function renderConnected(consoleInfo = { ticket: "PVEVNC:abc", port: "5900", clipboard: true }) {
  service.getConsole.mockResolvedValue(consoleInfo);
  await act(async () => root.render(<VncDialog resource={{ vmid: 105, name: "lab" }} onClose={() => {}} />));
}

function click(el) {
  el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

function typeInto(textarea, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
  setter.call(textarea, value);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

function clipboardToggle() {
  return host.querySelector('[data-testid="vnc-clipboard-toggle"]');
}

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

it("sends the panel text to the guest clipboard", async () => {
  await renderConnected();
  expect(host.querySelector("textarea")).toBeNull();

  await act(async () => click(clipboardToggle()));
  const textarea = host.querySelector("textarea");
  expect(textarea).not.toBeNull();

  await act(async () => typeInto(textarea, "echo hello"));
  await act(async () => click(host.querySelector('[data-testid="vnc-clipboard-send"]')));

  expect(vnc.clipboardPaste).toHaveBeenCalledWith("echo hello");
  expect(host.textContent).toContain("VncDialog.clipboardSent");
});

it("does not send an empty clipboard", async () => {
  await renderConnected();
  await act(async () => click(clipboardToggle()));
  const send = host.querySelector('[data-testid="vnc-clipboard-send"]');
  expect(send.disabled).toBe(true);
  await act(async () => click(send));
  expect(vnc.clipboardPaste).not.toHaveBeenCalled();
});

it("shows text copied inside the guest in the panel", async () => {
  await renderConnected();
  await act(async () => vnc.props.onClipboard({ detail: { text: "from guest" } }));
  await act(async () => click(clipboardToggle()));
  expect(host.querySelector("textarea").value).toBe("from guest");
});

it("warns when the machine has no VNC clipboard", async () => {
  await renderConnected({ ticket: "PVEVNC:abc", port: "5900", clipboard: false });
  await act(async () => click(clipboardToggle()));
  expect(host.textContent).toContain("VncDialog.clipboardDisabled");
});

it("does not warn when the clipboard is enabled", async () => {
  await renderConnected();
  await act(async () => click(clipboardToggle()));
  expect(host.textContent).not.toContain("VncDialog.clipboardDisabled");
});

it("reports a failed browser clipboard read instead of swallowing it", async () => {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { readText: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")) },
  });
  try {
    await renderConnected();
    await act(async () => click(clipboardToggle()));
    await act(async () => click(host.querySelector('[data-testid="vnc-clipboard-read"]')));
    expect(toast.error).toHaveBeenCalledWith("VncDialog.clipboardReadFailed");
    expect(vnc.clipboardPaste).not.toHaveBeenCalled();
  } finally {
    delete navigator.clipboard;
  }
});

it("hides the browser clipboard button when the browser cannot read the clipboard", async () => {
  /* http 來源（非 localhost）的頁面沒有 navigator.clipboard */
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  try {
    await renderConnected();
    await act(async () => click(clipboardToggle()));
    expect(host.querySelector('[data-testid="vnc-clipboard-read"]')).toBeNull();
    expect(host.querySelector('[data-testid="vnc-clipboard-send"]')).not.toBeNull();
  } finally {
    delete navigator.clipboard;
  }
});
