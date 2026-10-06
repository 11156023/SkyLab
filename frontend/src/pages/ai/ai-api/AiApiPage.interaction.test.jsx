// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AiApiPage, { getCredentialState } from "./AiApiPage";

const mocks = vi.hoisted(() => ({
  user: { role: "student" },
  listMyCredentials: vi.fn(),
  listMyRequests: vi.fn(),
  getCredential: vi.fn(),
  getMyUsage: vi.fn(),
  getMyUsageRecords: vi.fn(),
  rotateCredential: vi.fn(),
  deleteCredential: vi.fn(),
  createRequest: vi.fn(),
  confirm: vi.fn(),
  copy: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/aiApi", () => ({
  AiApiService: {
    listMyCredentials: mocks.listMyCredentials,
    listMyRequests: mocks.listMyRequests,
    getCredential: mocks.getCredential,
    getMyUsage: mocks.getMyUsage,
    getMyUsageRecords: mocks.getMyUsageRecords,
    rotateCredential: mocks.rotateCredential,
    deleteCredential: mocks.deleteCredential,
    createRequest: mocks.createRequest,
  },
}));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: mocks.user }) }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({
  default: ({ options = [], value, onChange, ariaLabel }) => (
    <div aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  ),
}));

const oldCredential = {
  id: "old-id",
  api_key_name: "專案金鑰",
  api_key_prefix: "ccai_old",
  base_url: "https://api.example.edu",
  rate_limit: 20,
  created_at: "2026-09-01T00:00:00Z",
  expires_at: null,
  revoked_at: null,
};
const newCredential = {
  ...oldCredential,
  id: "new-id",
  api_key_prefix: "ccai_new",
  api_key: "ccai_new_example_secret",
};

let root;
let host;

beforeEach(() => {
  vi.resetAllMocks();
  mocks.user = { role: "student" };
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  mocks.listMyCredentials.mockResolvedValue({ data: [oldCredential] });
  mocks.listMyRequests.mockResolvedValue({ data: [] });
  mocks.getMyUsage.mockResolvedValue({
    total_requests: 1,
    total_input_tokens: 12,
    total_output_tokens: 6,
    by_model: {},
    daily: [],
  });
  mocks.getMyUsageRecords.mockResolvedValue({ data: [], count: 0 });
  mocks.getCredential.mockImplementation(async (id) => id === "new-id"
    ? newCredential
    : { ...oldCredential, api_key: "ccai_old_example_secret" });
  mocks.rotateCredential.mockResolvedValue(newCredential);
  mocks.deleteCredential.mockResolvedValue({ message: "deleted" });
  mocks.confirm.mockResolvedValue(true);
  mocks.copy.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: mocks.copy },
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.clearAllMocks();
});

async function renderPage() {
  await act(async () => root.render(<AiApiPage />));
}

/* 對話框關閉有離場動畫（useDialogPresence 150ms），等它卸載 */
async function waitForExit() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 200)); });
}

describe("AI API 金鑰詳細資料", () => {
  test("點擊名稱直接顯示完整金鑰並可複製，清單仍只有前綴", async () => {
    await renderPage();
    await act(async () => document.querySelector('[aria-label="AiApiPage.openKeyDetails"]').click());

    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog.textContent).toContain("專案金鑰");
    expect(dialog.textContent).toContain("ccai_old_example_secret");
    expect(host.querySelector("table").textContent).not.toContain("ccai_old_example_secret");
    expect(dialog.textContent).toContain("https://api.example.edu/api/v1/ai-proxy");
    await act(async () => dialog.querySelector('[aria-label="AiApiPage.actionCopyKey"]').click());
    expect(mocks.copy).toHaveBeenCalledWith("ccai_old_example_secret");
    expect(mocks.getCredential).toHaveBeenCalledWith("old-id", { signal: expect.any(AbortSignal) });
  });

  test("載入失敗時顯示錯誤且不能複製前綴作為金鑰", async () => {
    mocks.getCredential.mockRejectedValue(new Error("failed"));
    await renderPage();
    await act(async () => document.querySelector('[aria-label="AiApiPage.openKeyDetails"]').click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.querySelector('[role="alert"]').textContent).toBe("AiApiPage.keyDetailLoadError");
    expect(dialog.querySelector('[aria-label="AiApiPage.actionCopyKey"]').disabled).toBe(true);
    expect(dialog.textContent).not.toContain("ccai_old••••••");
  });

  test("載入途中關閉視窗會取消請求，延遲回應不會重新開啟視窗", async () => {
    let resolveKey;
    mocks.getCredential.mockImplementationOnce(() => new Promise((resolve) => { resolveKey = resolve; }));
    await renderPage();
    await act(async () => document.querySelector('[aria-label="AiApiPage.openKeyDetails"]').click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.querySelector('[aria-label="AiApiPage.actionCopyKey"]').disabled).toBe(true);
    const { signal } = mocks.getCredential.mock.calls[0][1];
    await act(async () => dialog.querySelector('[aria-label="Modal.close"]').click());
    // 一按關閉就取消，不等離場動畫結束
    expect(signal.aborted).toBe(true);
    await act(async () => resolveKey({ api_key: "ccai_old_example_secret" }));
    expect(document.body.textContent).not.toContain("ccai_old_example_secret");
    await waitForExit();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.body.textContent).not.toContain("ccai_old_example_secret");
  });

  test("鍵盤焦點留在詳細視窗，Escape 關閉後回到名稱入口", async () => {
    await renderPage();
    const entry = document.querySelector('[aria-label="AiApiPage.openKeyDetails"]');
    entry.focus();
    await act(async () => entry.click());
    /* 外框是共用 Modal：開啟時焦點移進對話框，Tab 在對話框內循環 */
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.contains(document.activeElement)).toBe(true);
    const buttons = [...dialog.querySelectorAll("button:not(:disabled)")];
    /* happy-dom 沒有版面，getClientRects 一律為空；測試裡當成都看得到 */
    for (const button of buttons) button.getClientRects = () => [{}];
    const press = (options) => document.activeElement.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true, ...options }),
    );

    buttons[0].focus();
    press({ shiftKey: true });
    expect(document.activeElement).toBe(buttons[buttons.length - 1]);
    press();
    expect(document.activeElement).toBe(buttons[0]);

    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    await waitForExit();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(entry);
  });

  test("重新產生後在列表重載期間保留新金鑰，重新開啟會載入完整新金鑰", async () => {
    let resolveRefresh;
    mocks.listMyCredentials.mockImplementationOnce(async () => ({ data: [oldCredential] }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRefresh = resolve; }));
    await renderPage();

    await act(async () => document.querySelector('[aria-label="AiApiPage.moreActions"]').click());
    await act(async () => {
      [...document.querySelectorAll('[role="menuitem"]')]
        .find((button) => button.textContent.includes("AiApiPage.actionRotate")).click();
    });

    let dialog = document.querySelector('[role="dialog"]');
    expect(dialog.textContent).toContain("ccai_new_example_secret");
    // 背景重新載入：表格留在畫面上（不閃成載入動畫），也不會出現明文金鑰
    expect(host.querySelector("table").textContent).not.toContain("ccai_new_example_secret");

    await act(async () => resolveRefresh({ data: [{ ...newCredential, api_key: undefined }, { ...oldCredential, revoked_at: "2026-09-26T00:00:00Z" }] }));
    dialog = document.querySelector('[role="dialog"]');
    expect(dialog.textContent).toContain("ccai_new_example_secret");
    await act(async () => dialog.querySelector('[aria-label="AiApiPage.actionCopyKey"]').click());
    expect(mocks.copy).toHaveBeenCalledWith("ccai_new_example_secret");
    await act(async () => [...dialog.querySelectorAll("button")]
      .find((button) => button.textContent === "AiApiPage.copyCurlQuickstart").click());
    expect(mocks.copy).toHaveBeenCalledWith(
      'curl "https://api.example.edu/api/v1/ai-proxy/models" -H "Authorization: Bearer ccai_new_example_secret"',
    );

    await act(async () => dialog.querySelector('[aria-label="Modal.close"]').click());
    await waitForExit();
    await act(async () => [...host.querySelectorAll('[aria-label="AiApiPage.openKeyDetails"]')]
      .find((button) => button.textContent === "專案金鑰").click());
    dialog = document.querySelector('[role="dialog"]');
    expect(dialog.textContent).toContain("ccai_new_example_secret");
    expect(mocks.getCredential).toHaveBeenCalledWith("new-id", { signal: expect.any(AbortSignal) });
  });
});

describe("AI API 金鑰狀態", () => {
  const base = { request_id: "req-1", expires_at: null, revoked_at: null };

  test("重新產生的舊金鑰算已替換，其他撤銷紀錄算已停用", () => {
    const old = { ...base, id: "old", created_at: "2026-09-01T00:00:00Z", revoked_at: "2026-09-20T00:00:00Z" };
    const rotated = { ...base, id: "new", created_at: "2026-09-20T00:00:00Z" };
    const deleted = { ...base, id: "gone", request_id: "req-2", created_at: "2026-09-01T00:00:00Z", revoked_at: "2026-09-21T00:00:00Z" };
    const all = [rotated, old, deleted];
    expect(getCredentialState(old, all)).toBe("replaced");
    expect(getCredentialState(deleted, all)).toBe("revoked");
    expect(getCredentialState(rotated, all)).toBe("active");
    expect(getCredentialState({ ...base, id: "exp", created_at: "2026-09-01T00:00:00Z", expires_at: "2026-09-02T00:00:00Z" }, all)).toBe("expired");
  });

  test("刪除成功後立即從使用者畫面移除，背景重載失敗也不恢復", async () => {
    mocks.listMyCredentials
      .mockResolvedValueOnce({ data: [oldCredential] })
      .mockRejectedValueOnce(new Error("refresh failed"));
    await renderPage();

    await act(async () => document.querySelector('[aria-label="AiApiPage.moreActions"]').click());
    await act(async () => {
      [...document.querySelectorAll('[role="menuitem"]')]
        .find((button) => button.textContent.includes("AiApiPage.actionDelete")).click();
    });

    expect(mocks.deleteCredential).toHaveBeenCalledWith("old-id");
    expect(host.textContent).not.toContain("專案金鑰");
  });

  test("失效的金鑰預設收起來，按了才顯示", async () => {
    mocks.listMyCredentials.mockResolvedValue({ data: [
      oldCredential,
      { ...oldCredential, id: "revoked-id", request_id: "other", api_key_name: "舊金鑰", revoked_at: "2026-09-20T00:00:00Z" },
    ] });
    await renderPage();
    expect(host.querySelector("table").textContent).not.toContain("舊金鑰");
    const toggle = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("AiApiPage.showInactive"));
    await act(async () => toggle.click());
    expect(host.querySelector("table").textContent).toContain("舊金鑰");
    expect(host.querySelector("table").textContent).toContain("AiApiPage.credStatusRevoked");
  });
});

describe("AI API 申請金鑰", () => {
  test.each([
    ["student", ["1d", "7d", "30d", "90d"], "30d"],
    ["teacher", ["1d", "7d", "30d", "never"], "never"],
    ["admin", ["1d", "7d", "30d", "never"], "never"],
  ])("%s 只顯示可申請期限並使用正確預設", async (role, values, defaultValue) => {
    mocks.user = { role };
    await renderPage();
    await act(async () => host.querySelector('[data-guide="ai-add-key"]').click());
    const select = document.querySelector("#ai-duration");
    expect([...select.options].map((option) => option.value)).toEqual(values);
    expect(select.value).toBe(defaultValue);
  });

  test("切換成學生時不沿用教師的永久期限", async () => {
    mocks.user = { role: "teacher" };
    await renderPage();
    await act(async () => host.querySelector('[data-guide="ai-add-key"]').click());
    expect(document.querySelector("#ai-duration").value).toBe("never");
    mocks.user = { role: "student" };
    await renderPage();
    expect(document.querySelector("#ai-duration").value).toBe("30d");
    expect([...document.querySelector("#ai-duration").options].map((option) => option.value))
      .toEqual(["1d", "7d", "30d", "90d"]);
  });

  test.each([
    ["student", "90d", "30d"],
    ["teacher", "never", "never"],
    ["admin", "never", "never"],
  ])("%s 送出期限後重設為身分預設值", async (role, duration, defaultValue) => {
    mocks.user = { role };
    mocks.createRequest.mockResolvedValue({ id: "req-duration" });
    await renderPage();
    await act(async () => host.querySelector('[data-guide="ai-add-key"]').click());
    const setInput = async (selector, value, proto) => act(async () => {
      const element = document.querySelector(selector);
      Object.getOwnPropertyDescriptor(proto, "value").set.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await setInput("#ai-key-name", "期限測試", HTMLInputElement.prototype);
    await setInput("#ai-purpose", "使用 AI API 進行課程專題問答", HTMLTextAreaElement.prototype);
    await act(async () => {
      const select = document.querySelector("#ai-duration");
      select.value = duration;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => document.querySelector('[data-guide="ai-submit"]').click());
    expect(mocks.createRequest).toHaveBeenCalledWith({
      api_key_name: "期限測試", purpose: "使用 AI API 進行課程專題問答", duration,
    });
    await act(async () => host.querySelector('[data-guide="ai-add-key"]').click());
    expect(document.querySelector("#ai-duration").value).toBe(defaultValue);
  });

  test("名稱與用途不足時標出欄位；送出後帶到申請紀錄", async () => {
    mocks.createRequest.mockResolvedValue({ id: "req-new" });
    await renderPage();
    await act(async () => host.querySelector('[data-guide="ai-add-key"]').click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.querySelector("#ai-key-name").value).toBe("");
    await act(async () => dialog.querySelector('[data-guide="ai-submit"]').click());
    expect(dialog.querySelector("#ai-key-name").getAttribute("aria-invalid")).toBe("true");
    expect(dialog.querySelector("#ai-purpose").getAttribute("aria-invalid")).toBe("true");
    expect(mocks.createRequest).not.toHaveBeenCalled();

    const setValue = async (element, value) => act(async () => {
      const proto = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(element, value);
      element.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await setValue(dialog.querySelector("#ai-key-name"), "專題");
    await setValue(dialog.querySelector("#ai-purpose"), "畢業專題串接聊天模型做問答");
    await act(async () => dialog.querySelector('[data-guide="ai-submit"]').click());
    expect(mocks.createRequest).toHaveBeenCalledWith({ purpose: "畢業專題串接聊天模型做問答", api_key_name: "專題", duration: "30d" });
    const recordsTab = [...host.querySelectorAll("button")].find((button) => button.textContent === "AiApiPage.tabRecords");
    expect(recordsTab.getAttribute("aria-pressed")).toBe("true");
  });
});

describe("AI API 細項紀錄", () => {
  test("摘要以單列呈現模型、金鑰來源與輸入輸出，點擊後展開完整資料", async () => {
    mocks.getMyUsageRecords.mockResolvedValue({
      data: [{
        id: "usage-1",
        route: "model",
        api_key_name: "研究專案",
        api_key_prefix: "ccai_demo",
        model_name: "models--org--Jev-1.13",
        call_type: "chat_completion",
        input_tokens: 12,
        output_tokens: 6,
        total_tokens: 18,
        request_duration_ms: 1350,
        status: "success",
        error_message: null,
        created_at: "2026-09-25T09:09:00Z",
      }],
      count: 1,
    });

    await renderPage();
    const usageTab = [...host.querySelectorAll("button")]
      .find((button) => button.textContent === "AiApiPage.tabUsage");
    await act(async () => usageTab.click());

    const recordsPanel = host.querySelector('[data-guide="ai-usage-records"]');
    const summary = recordsPanel.querySelector('[aria-expanded="false"]');
    expect(summary.textContent).toContain("org/Jev-1.13");
    expect(summary.textContent).toContain("研究專案");
    expect(summary.textContent).toContain("ccai_demo");
    expect(summary.textContent).toContain("12");
    expect(summary.textContent).toContain("6");
    expect(recordsPanel.textContent).not.toContain("usage-1");

    await act(async () => summary.click());
    expect(summary.getAttribute("aria-expanded")).toBe("true");
    expect(recordsPanel.textContent).toContain("usage-1");
    expect(recordsPanel.textContent).toContain("AiApiPage.callTypeChatCompletion");
    expect(recordsPanel.textContent).toContain("18");
    expect(recordsPanel.textContent).toContain("AiApiPage.recordDuration");
  });
});
