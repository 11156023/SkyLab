// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { connectJobsWebSocket } from "./jobs";

vi.mock("./api", () => ({ apiGet: vi.fn() }));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("jobs delivers valid snapshots and status changes through the shared socket lifecycle", () => {
  let socket;
  vi.stubGlobal("WebSocket", class {
    constructor() { socket = this; }
    close() { this.onclose?.({ code: 1000 }); }
  });
  const snapshot = vi.fn();
  const status = vi.fn();
  const stop = connectJobsWebSocket("token", snapshot, { onStatusChange: status });
  socket.onopen();
  socket.onmessage({ data: '{"jobs":[]}' });
  socket.onmessage({ data: "invalid" });
  socket.onclose({ code: 1008 });
  expect(snapshot).toHaveBeenCalledExactlyOnceWith({ jobs: [] });
  expect(status.mock.calls).toEqual([[true], [false]]);
  stop();
});

test("jobs retries policy closure with the latest token", () => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0);
  const sockets = [];
  vi.stubGlobal("WebSocket", class {
    constructor(url) { this.url = url; sockets.push(this); }
    close() { this.onclose?.({ code: 1000 }); }
  });
  let token = "expired";
  const stop = connectJobsWebSocket(() => token, vi.fn());
  sockets[0].onclose({ code: 1008 });
  token = "renewed";
  vi.advanceTimersByTime(2500);
  expect(sockets).toHaveLength(2);
  expect(sockets[1].url).toContain("token=renewed");
  stop();
  vi.advanceTimersByTime(60000);
  expect(sockets).toHaveLength(2);
});
