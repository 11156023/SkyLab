// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AiApiPage from "./AiApiPage";

const mocks = vi.hoisted(() => ({
  listMyCredentials: vi.fn(),
  listMyRequests: vi.fn(),
  getCredential: vi.fn(),
  getMyUsage: vi.fn(),
  getMyUsageRecords: vi.fn(),
  rotateCredential: vi.fn(),
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
  },
}));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
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
    await act(async () => dialog.querySelector('[aria-label="AiApiPage.close"]').click());
    expect(signal.aborted).toBe(true);
    await act(async () => resolveKey({ api_key: "ccai_old_example_secret" }));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.body.textContent).not.toContain("ccai_old_example_secret");
  });

  test("鍵盤焦點留在詳細視窗，Escape 關閉後回到名稱入口", async () => {
    await renderPage();
    const entry = document.querySelector('[aria-label="AiApiPage.openKeyDetails"]');
    entry.focus();
    await act(async () => entry.click());
    const buttons = document.querySelector('[role="dialog"]').querySelectorAll("button");
    expect(document.activeElement).toBe(buttons[0]);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(buttons[buttons.length - 1]);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(buttons[0]);

    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
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
    expect(host.querySelector("table")).toBeNull();

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

    await act(async () => dialog.querySelector('[aria-label="AiApiPage.close"]').click());
    await act(async () => [...host.querySelectorAll('[aria-label="AiApiPage.openKeyDetails"]')]
      .find((button) => button.textContent === "專案金鑰").click());
    dialog = document.querySelector('[role="dialog"]');
    expect(dialog.textContent).toContain("ccai_new_example_secret");
    expect(mocks.getCredential).toHaveBeenCalledWith("new-id", { signal: expect.any(AbortSignal) });
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
