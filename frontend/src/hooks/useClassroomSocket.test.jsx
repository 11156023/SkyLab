// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const tokenState = { value: "token-1" };

vi.mock("../services/auth", () => ({
  AuthStorage: { getAccessToken: () => tokenState.value },
}));
vi.mock("../utils/wsUrl", () => ({ wsBaseUrl: () => "ws://test" }));

import { useClassroomSocket } from "./useClassroomSocket";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

class FakeWebSocket {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.onopen = null;
    this.onclose = null;
    this.onmessage = null;
    this.onerror = null;
    FakeWebSocket.instances.push(this);
  }

  close() {}
}

let host;
let root;
let originalWebSocket;

function Harness({ onEvent }) {
  useClassroomSocket(onEvent);
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
  tokenState.value = "token-1";
  originalWebSocket = globalThis.WebSocket;
  globalThis.WebSocket = FakeWebSocket;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  globalThis.WebSocket = originalWebSocket;
  vi.useRealTimers();
});

function tokenOf(socket) {
  return new URL(socket.url).searchParams.get("token");
}

test("收到 1008 仍依退避重連，並改用最新的 token", () => {
  act(() => root.render(<Harness onEvent={() => {}} />));
  expect(FakeWebSocket.instances).toHaveLength(1);
  expect(tokenOf(FakeWebSocket.instances[0])).toBe("token-1");

  // 舊 token 被後端拒絕；期間 api 層 refresh 換上新 token
  tokenState.value = "token-2";
  act(() => FakeWebSocket.instances[0].onclose({ code: 1008 }));

  act(() => vi.advanceTimersByTime(4_999));
  expect(FakeWebSocket.instances).toHaveLength(1);
  act(() => vi.advanceTimersByTime(1));
  expect(FakeWebSocket.instances).toHaveLength(2);
  expect(tokenOf(FakeWebSocket.instances[1])).toBe("token-2");
});

test("連續 1008 時退避加倍並封頂 60 秒，不會停止重連", () => {
  act(() => root.render(<Harness onEvent={() => {}} />));
  const expectedDelays = [5_000, 10_000, 20_000, 40_000, 60_000, 60_000];
  for (const delay of expectedDelays) {
    const count = FakeWebSocket.instances.length;
    act(() => FakeWebSocket.instances[count - 1].onclose({ code: 1008 }));
    act(() => vi.advanceTimersByTime(delay - 1));
    expect(FakeWebSocket.instances).toHaveLength(count);
    act(() => vi.advanceTimersByTime(1));
    expect(FakeWebSocket.instances).toHaveLength(count + 1);
  }
});

test("連上後退避歸零，事件轉給 handler", () => {
  const onEvent = vi.fn();
  act(() => root.render(<Harness onEvent={onEvent} />));
  const first = FakeWebSocket.instances[0];
  act(() => first.onclose({ code: 1008 }));
  act(() => vi.advanceTimersByTime(5_000));
  const second = FakeWebSocket.instances[1];
  act(() => second.onopen());
  act(() => second.onmessage({ data: JSON.stringify({ type: "live_started" }) }));
  expect(onEvent).toHaveBeenCalledWith({ type: "live_started" });

  act(() => second.onclose({ code: 1006 }));
  act(() => vi.advanceTimersByTime(5_000));
  expect(FakeWebSocket.instances).toHaveLength(3);
});

test("卸載後不再重連", () => {
  act(() => root.render(<Harness onEvent={() => {}} />));
  const first = FakeWebSocket.instances[0];
  act(() => root.unmount());
  act(() => first.onclose({ code: 1008 }));
  act(() => vi.advanceTimersByTime(120_000));
  expect(FakeWebSocket.instances).toHaveLength(1);
  root = createRoot(host);
});
