// @vitest-environment happy-dom
import { act, createRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

/* 假的 noVNC RFB：像真的一樣把畫布掛進容器，其餘方法只記錄呼叫 */
const { FakeRfb } = vi.hoisted(() => {
  class FakeRfb extends EventTarget {
    static instances = [];

    constructor(target, url, options) {
      super();
      FakeRfb.instances.push(this);
      this.target = target;
      this.url = url;
      this.options = options;
      this.viewOnly = false;
      this.scaleViewport = false;
      this.background = "";
      this.focusOnClick = true;
      this.screen = document.createElement("div");
      this.canvas = document.createElement("canvas");
      this.canvas.tabIndex = -1;
      this.screen.append(this.canvas);
      target.append(this.screen);
      this.focus = vi.fn(() => this.canvas.focus());
      this.blur = vi.fn(() => this.canvas.blur());
      this.disconnect = vi.fn();
      this.sendCtrlAltDel = vi.fn();
      this.clipboardPasteFrom = vi.fn();
      this.sendCredentials = vi.fn();
    }
  }
  return { FakeRfb };
});

vi.mock("@novnc/novnc", () => ({ default: FakeRfb }));

import NoVncScreen from "./NoVncScreen";

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  FakeRfb.instances.length = 0;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function render(props) {
  await act(async () => root.render(<NoVncScreen url="ws://test/vnc" {...props} />));
}

it("opens one connection with the ticket as the VNC password and exposes the controls", async () => {
  const ref = createRef();
  await render({ ref, password: "PVEVNC:abc" });

  expect(FakeRfb.instances).toHaveLength(1);
  const rfb = FakeRfb.instances[0];
  expect(rfb.url).toBe("ws://test/vnc");
  expect(rfb.options.credentials).toEqual({ username: "", password: "PVEVNC:abc", target: "" });
  expect(host.querySelector("canvas")).toBe(rfb.canvas);

  ref.current.focus();
  ref.current.sendCtrlAltDel();
  ref.current.clipboardPaste("echo hi");
  expect(rfb.focus).toHaveBeenCalledWith({ preventScroll: true });
  expect(rfb.sendCtrlAltDel).toHaveBeenCalled();
  expect(rfb.clipboardPasteFrom).toHaveBeenCalledWith("echo hi");
  expect(ref.current.rfb).toBe(rfb);
});

it("keeps the keyboard on the canvas when the pointer leaves the screen", async () => {
  /* react-vnc 會在 mouseleave 時 blur 畫布，點一下或拖曳後鍵盤就死掉；這裡不能有這種事 */
  const ref = createRef();
  await render({ ref });
  const rfb = FakeRfb.instances[0];
  ref.current.focus();
  expect(document.activeElement).toBe(rfb.canvas);

  await act(async () => {
    rfb.canvas.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: document.body }));
    rfb.canvas.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body }));
    document.body.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: rfb.canvas }));
  });

  expect(rfb.blur).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(rfb.canvas);
});

it("applies viewOnly changes to the live connection instead of ignoring them", async () => {
  /* 老師接管：viewOnly true → false 要立刻生效，而且不能重連 */
  await render({ viewOnly: true });
  const rfb = FakeRfb.instances[0];
  expect(rfb.viewOnly).toBe(true);

  await render({ viewOnly: false });
  expect(FakeRfb.instances).toHaveLength(1);
  expect(rfb.viewOnly).toBe(false);
  expect(rfb.disconnect).not.toHaveBeenCalled();
});

it("delivers events to the callbacks from the latest render", async () => {
  const first = vi.fn();
  const second = vi.fn();
  await render({ onConnect: first });
  await render({ onConnect: second });

  const rfb = FakeRfb.instances[0];
  await act(async () => rfb.dispatchEvent(new CustomEvent("connect")));
  expect(first).not.toHaveBeenCalled();
  expect(second).toHaveBeenCalledTimes(1);

  const onClipboard = vi.fn();
  await render({ onConnect: second, onClipboard });
  const event = new CustomEvent("clipboard", { detail: { text: "from guest" } });
  await act(async () => rfb.dispatchEvent(event));
  expect(onClipboard).toHaveBeenCalledWith(event);
});

it("answers a credentials request with the same ticket", async () => {
  await render({ password: "PVEVNC:abc" });
  const rfb = FakeRfb.instances[0];
  await act(async () => rfb.dispatchEvent(new CustomEvent("credentialsrequired")));
  expect(rfb.sendCredentials).toHaveBeenCalledWith({ username: "", password: "PVEVNC:abc", target: "" });
});

it("reconnects when the url changes and disconnects the old connection", async () => {
  await render({});
  await render({ url: "ws://test/other" });

  expect(FakeRfb.instances).toHaveLength(2);
  expect(FakeRfb.instances[0].disconnect).toHaveBeenCalledTimes(1);
  expect(FakeRfb.instances[1].url).toBe("ws://test/other");
});

it("disconnects exactly once when closed through the handle and then unmounted", async () => {
  const ref = createRef();
  await render({ ref });
  const rfb = FakeRfb.instances[0];

  ref.current.disconnect();
  ref.current.disconnect();
  await act(async () => root.unmount());
  root = createRoot(host);

  expect(rfb.disconnect).toHaveBeenCalledTimes(1);
});
