import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { connectReconnectingWebSocket } from "./reconnectingWebSocket";

let sockets;
let stops;
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  sockets = [];
  stops = [];
  vi.stubGlobal("WebSocket", class {
    constructor(url) { this.url = url; sockets.push(this); }
    close() { this.onclose?.({ code: 1000 }); }
  });
});
afterEach(() => {
  stops.forEach((stop) => stop());
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function connect(url = () => "ws://test", handlers = {}) {
  const stop = connectReconnectingWebSocket(url, handlers);
  stops.push(stop);
  return stop;
}

test("reconnect uses jitter, exponential backoff and a fixed ceiling", () => {
  connect();
  for (const delay of [3750, 7500, 15000, 30000, 45000, 45000]) {
    const count = sockets.length;
    sockets.at(-1).onclose({ code: 1006 });
    vi.advanceTimersByTime(delay - 1);
    expect(sockets).toHaveLength(count);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(count + 1);
  }
});

test("opening resets backoff and reconnect obtains the current token", () => {
  let token = "old";
  connect(() => `ws://test?token=${token}`);
  sockets[0].onclose({ code: 1006 });
  token = "new";
  vi.advanceTimersByTime(3750);
  expect(sockets[1].url).toContain("new");
  sockets[1].onopen();
  sockets[1].onclose({ code: 1006 });
  vi.advanceTimersByTime(3750);
  expect(sockets).toHaveLength(3);
});

test("policy rejection stops reconnecting", () => {
  connect();
  sockets[0].onclose({ code: 1008 });
  vi.runAllTimers();
  expect(sockets).toHaveLength(1);
});

test("cleanup cancels timers and suppresses stale socket events", () => {
  const onOpen = vi.fn();
  const onMessage = vi.fn();
  const onClose = vi.fn();
  const stop = connect(undefined, { onOpen, onMessage, onClose });
  const stale = sockets[0];
  stale.onclose({ code: 1006 });
  stop();
  stale.onopen();
  stale.onmessage({ data: "old" });
  stale.onclose({ code: 1006 });
  vi.runAllTimers();
  expect(sockets).toHaveLength(1);
  expect(onOpen).not.toHaveBeenCalled();
  expect(onMessage).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("duplicate close schedules one reconnect and old messages are ignored", () => {
  const onMessage = vi.fn();
  connect(undefined, { onMessage });
  const stale = sockets[0];
  stale.onclose({ code: 1006 });
  stale.onclose({ code: 1006 });
  vi.advanceTimersByTime(3750);
  stale.onmessage({ data: "old" });
  sockets[1].onmessage({ data: "new" });
  expect(sockets).toHaveLength(2);
  expect(onMessage).toHaveBeenCalledOnce();
});

test("missing token retries without creating an unauthenticated socket", () => {
  let url = null;
  const stop = connect(() => url);
  expect(sockets).toHaveLength(0);
  url = "ws://test";
  vi.advanceTimersByTime(3750);
  expect(sockets).toHaveLength(1);
  stop();
  vi.runAllTimers();
  expect(sockets).toHaveLength(1);
});
