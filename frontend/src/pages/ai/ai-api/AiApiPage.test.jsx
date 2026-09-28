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
  confirm: vi.fn(),
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
  },
}));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/RrdChart/RrdChart", () => ({ default: () => null }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({
  default: ({ options = [], value, onChange, ariaLabel }) => (
    <div data-group={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          data-value={option.value}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  ),
}));

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function usage(total) {
  return { total_requests: total, total_input_tokens: 0, total_output_tokens: 0, by_model: {}, daily: [] };
}

let root;
let host;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listMyCredentials.mockResolvedValue({ data: [] });
  mocks.listMyRequests.mockResolvedValue({ data: [] });
  mocks.getMyUsageRecords.mockResolvedValue({ data: [], count: 0 });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

describe("AiApiPage my usage", () => {
  test("a slow response for the previous range does not overwrite the new range", async () => {
    const first = deferred();
    const second = deferred();
    mocks.getMyUsage
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    await act(async () => { root.render(<AiApiPage />); });
    await flush();
    const usageTab = host.querySelector('[data-group="AiApiPage.tabsAriaLabel"] [data-value="usage"]');
    await act(async () => { usageTab.click(); });
    await flush();
    const sevenDays = host.querySelector('[data-group="AiApiPage.usageRangeLabel"] [data-value="7d"]');
    await act(async () => { sevenDays.click(); });
    await flush();
    expect(mocks.getMyUsage).toHaveBeenCalledTimes(2);

    await act(async () => { second.resolve(usage(9090)); });
    await flush();
    await act(async () => { first.resolve(usage(7777)); });
    await flush();

    expect(host.textContent).toContain("9090");
    expect(host.textContent).not.toContain("7777");
  });

  test.each(["response", "completion"])(
    "a %s record shows its raw call type instead of a missing locale key",
    async (callType) => {
      mocks.getMyUsage.mockResolvedValue(usage(1));
      mocks.getMyUsageRecords.mockResolvedValue({
        data: [{
          id: "usage-r1",
          route: "model",
          api_key_name: "課堂金鑰",
          api_key_prefix: "ccai_demo",
          model_name: "demo-model",
          call_type: callType,
          input_tokens: 3,
          output_tokens: 4,
          total_tokens: 7,
          request_duration_ms: 100,
          status: "success",
          error_message: null,
          created_at: "2026-09-25T09:09:00Z",
        }],
        count: 1,
      });

      await act(async () => { root.render(<AiApiPage />); });
      await flush();
      const usageTab = host.querySelector('[data-group="AiApiPage.tabsAriaLabel"] [data-value="usage"]');
      await act(async () => { usageTab.click(); });
      await flush();
      const summary = host.querySelector('[data-guide="ai-usage-records"] [aria-expanded="false"]');
      await act(async () => { summary.click(); });

      const details = host.querySelector('[data-guide="ai-usage-records"]').textContent;
      expect(details).toContain("usage-r1");
      expect(details).toContain(callType);
      expect(details).not.toMatch(/AiApiPage\.callType/);
    },
  );
});
