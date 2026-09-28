// @vitest-environment happy-dom
import { act, forwardRef, useEffect, useImperativeHandle } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { setControl } = vi.hoisted(() => ({ setControl: vi.fn() }));

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../services/classroom", () => ({ ClassroomService: { setControl } }));
vi.mock("../../services/auth", () => ({ AuthStorage: { getAccessToken: () => "tok" } }));
vi.mock("../../utils/wsUrl", () => ({ wsBaseUrl: () => "ws://test" }));
vi.mock("../../hooks/useToast", () => ({ useToast: () => ({ error: vi.fn(), success: vi.fn() }) }));
vi.mock("react-vnc", () => ({
  VncScreen: forwardRef(function FakeVnc({ onConnect }, ref) {
    useImperativeHandle(ref, () => ({ disconnect: () => {} }));
    useEffect(() => {
      onConnect?.();
    }, [onConnect]);
    return <div data-testid="vnc" />;
  }),
}));

import ClassroomWatchDialog from "./ClassroomWatchDialog";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  setControl.mockReset();
  setControl.mockResolvedValue({});
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

function findButton(text) {
  return [...document.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

test("unmounting while holding control releases it so the student's console is unlocked", async () => {
  await act(async () =>
    root.render(<ClassroomWatchDialog sessionId="s1" canControl onClose={() => {}} />),
  );
  await act(async () => findButton("ClassroomWatchDialog.takeControl").click());
  expect(setControl).toHaveBeenCalledWith("s1", "take");

  await act(async () => root.unmount());
  expect(setControl).toHaveBeenLastCalledWith("s1", "release");
  root = createRoot(host);
});

test("closing through the dialog releases once, and the later unmount does not release again", async () => {
  vi.useFakeTimers();
  try {
    const onClose = vi.fn();
    await act(async () =>
      root.render(<ClassroomWatchDialog sessionId="s1" canControl onClose={onClose} />),
    );
    await act(async () => findButton("ClassroomWatchDialog.takeControl").click());
    await act(async () =>
      document.querySelector("[aria-label='ClassroomWatchDialog.closeTitle']").click(),
    );
    await act(async () => vi.advanceTimersByTime(200));
    expect(onClose).toHaveBeenCalled();

    await act(async () => root.unmount());
    root = createRoot(host);
    const releases = setControl.mock.calls.filter(([, action]) => action === "release");
    expect(releases).toHaveLength(1);
  } finally {
    vi.useRealTimers();
  }
});

test("unmounting without having taken control sends no release", async () => {
  await act(async () =>
    root.render(<ClassroomWatchDialog sessionId="s1" canControl onClose={() => {}} />),
  );
  await act(async () => root.unmount());
  root = createRoot(host);
  expect(setControl).not.toHaveBeenCalled();
});
