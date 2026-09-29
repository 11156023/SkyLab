import { afterEach, beforeAll, expect, test, vi } from "vitest";
import i18n from "../i18n";
import { fetchWithTimeout } from "./fetchWithTimeout";

/* 與 locale_changes 提交的日文字串一致；locales/ja/services.json 補上後這段只是覆寫同值 */
const JA_TIMEOUT = "サーバーの応答がありません。しばらくしてから再度お試しください";

beforeAll(() => {
  i18n.addResources("ja", "services", { "api.requestTimeout": JA_TIMEOUT });
});

afterEach(async () => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  await i18n.changeLanguage("zh-TW");
});

test("逾時的錯誤訊息跟著介面語系，不是寫死的英文", async () => {
  await i18n.changeLanguage("ja");
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((url, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  })));

  const pending = fetchWithTimeout("/api/v1/slow", { method: "GET" }, 1000);
  const assertion = expect(pending).rejects.toMatchObject({
    status: 408,
    timeout: true,
    message: JA_TIMEOUT,
  });
  await vi.advanceTimersByTimeAsync(1000);
  await assertion;
});

test("呼叫端取消仍是 cancelled，不當成逾時", async () => {
  vi.stubGlobal("fetch", vi.fn((url, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  })));
  const controller = new AbortController();
  const pending = fetchWithTimeout("/api/v1/slow", { method: "GET", signal: controller.signal }, 1000);
  controller.abort();
  await expect(pending).rejects.toMatchObject({ status: 0, cancelled: true });
});
