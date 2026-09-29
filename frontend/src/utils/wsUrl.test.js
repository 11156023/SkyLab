// @vitest-environment happy-dom
import { afterEach, describe, expect, test, vi } from "vitest";
import { wsBaseUrl } from "./wsUrl";
import { wsBaseUrl as legacyWsBaseUrl } from "../hooks/useClassroomSocket";
import { courseProgressWsUrl } from "../services/courses";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("wsBaseUrl", () => {
  test("https API 轉成 wss，只留 host", () => {
    vi.stubEnv("VITE_API_URL", "https://skylab.example.edu/api");
    expect(wsBaseUrl()).toBe("wss://skylab.example.edu");
  });

  test("http API 轉成 ws（含 port）", () => {
    vi.stubEnv("VITE_API_URL", "http://localhost:8000");
    expect(wsBaseUrl()).toBe("ws://localhost:8000");
  });

  test("沒設定 VITE_API_URL 時用目前頁面位置", () => {
    vi.stubEnv("VITE_API_URL", "");
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    expect(wsBaseUrl()).toBe(`${proto}//${window.location.host}`);
  });

  test("舊的 hooks/useClassroomSocket 匯入路徑仍指向同一個函式", () => {
    expect(legacyWsBaseUrl).toBe(wsBaseUrl);
  });
});

describe("courseProgressWsUrl", () => {
  test("沿用 wsBaseUrl 並把 token 編碼進 query", () => {
    vi.stubEnv("VITE_API_URL", "https://skylab.example.edu");
    expect(courseProgressWsUrl("p1", "a b+c")).toBe(
      "wss://skylab.example.edu/ws/courses/paths/p1/progress?token=a%20b%2Bc",
    );
  });
});
