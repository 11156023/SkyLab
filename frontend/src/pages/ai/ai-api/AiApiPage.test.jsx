// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { aiApiKeys } from "../../../queries/aiApi";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
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
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "student" } }) }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/RrdChart/RrdChart", () => ({
  default: ({ data = [] }) => <div data-testid="daily-chart">{data.map((point) => point.time).join("|")}</div>,
}));
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
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function usage(total) {
  return { total_requests: total, total_input_tokens: 0, total_output_tokens: 0, by_model: {}, daily: [] };
}

let root;
let host;
let client;

/* TanStack Query 的通知排在 setTimeout(0)：先等一個 timer tick 再等 microtask */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function renderPage() {
  await act(async () => { root.render(withQueryClient(<AiApiPage />, client)); });
}

/* 原本由 useAutoRefresh 觸發的背景刷新，改成直接要求那份查詢重抓 */
function refetch(queryKey) {
  return act(async () => { client.refetchQueries({ queryKey }); });
}

beforeEach(() => {
  vi.resetAllMocks();
  client = createTestQueryClient();
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

    await renderPage();
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

      await renderPage();
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

  test("daily usage chart labels a date-only bucket with that calendar day", async () => {
    const previousTz = process.env.TZ;
    process.env.TZ = "America/New_York";
    try {
      mocks.getMyUsage.mockResolvedValue({
        ...usage(3),
        daily: [
          { date: "2026-10-10", input_tokens: 5, output_tokens: 1 },
          { date: "2026-10-11", input_tokens: 2, output_tokens: 3 },
        ],
      });
      await renderPage();
      await flush();
      const usageTab = host.querySelector('[data-group="AiApiPage.tabsAriaLabel"] [data-value="usage"]');
      await act(async () => { usageTab.click(); });
      await flush();

      // 當成 UTC 午夜解析的話，紐約會標成 10/9、10/10
      expect(host.querySelector('[data-testid="daily-chart"]').textContent).toBe("10/10|10/11");
    } finally {
      if (previousTz === undefined) delete process.env.TZ;
      else process.env.TZ = previousTz;
    }
  });
});

describe("AiApiPage keys and requests", () => {
  const credential = (id, name) => ({
    id,
    api_key_name: name,
    api_key_prefix: `ccai_${id}`,
    base_url: "https://api.example.edu",
    rate_limit: 20,
    created_at: "2026-09-01T00:00:00Z",
    expires_at: null,
    revoked_at: null,
  });
  const pendingRequest = { id: "req-1", status: "pending", api_key_name: "待審金鑰", created_at: "2026-09-01T00:00:00Z" };

  test("an older list response arriving after a newer one is ignored", async () => {
    const older = deferred();
    const newer = deferred();
    mocks.listMyRequests.mockResolvedValue({ data: [pendingRequest] });
    mocks.listMyCredentials
      .mockResolvedValueOnce({ data: [credential("a", "金鑰甲"), credential("b", "金鑰乙")] })
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    await renderPage();
    await flush();
    expect(host.textContent).toContain("金鑰乙");

    /* 第二次重抓會取消還在路上的第一次，它晚回來的舊清單不會寫進快取 */
    await refetch(aiApiKeys.myCredentials);
    await refetch(aiApiKeys.myCredentials);
    expect(mocks.listMyCredentials).toHaveBeenCalledTimes(3);
    await act(async () => { newer.resolve({ data: [credential("a", "金鑰甲")] }); });
    await flush();
    await act(async () => { older.resolve({ data: [credential("a", "金鑰甲"), credential("b", "金鑰乙")] }); });
    await flush();

    expect(host.textContent).toContain("金鑰甲");
    expect(host.textContent).not.toContain("金鑰乙");
  });

  test("a stale pending snapshot does not make the approval toast fire twice", async () => {
    const older = deferred();
    const approved = { ...pendingRequest, status: "approved" };
    mocks.listMyCredentials.mockResolvedValue({ data: [] });
    mocks.listMyRequests
      .mockResolvedValueOnce({ data: [pendingRequest] })
      .mockReturnValueOnce(older.promise)
      .mockResolvedValueOnce({ data: [approved] })
      .mockResolvedValue({ data: [approved, { ...pendingRequest, id: "req-2" }] });

    await renderPage();
    await flush();

    await refetch(aiApiKeys.myRequests);
    await refetch(aiApiKeys.myRequests);
    await flush();
    expect(mocks.toast.success).toHaveBeenCalledTimes(1);

    await act(async () => { older.resolve({ data: [pendingRequest] }); });
    await flush();
    await refetch(aiApiKeys.myRequests);
    await flush();
    expect(mocks.listMyRequests).toHaveBeenCalledTimes(4);

    expect(mocks.toast.success).toHaveBeenCalledTimes(1);
  });

  test("an approval refetches my keys so the new key shows without another poll", async () => {
    const approved = { ...pendingRequest, status: "approved" };
    mocks.listMyRequests
      .mockResolvedValueOnce({ data: [pendingRequest] })
      .mockResolvedValue({ data: [approved] });
    mocks.listMyCredentials
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValue({ data: [credential("new", "核准後的金鑰")] });

    await renderPage();
    await flush();

    /* 申請先回來已核准：待審數歸零後金鑰不再輪詢，要靠核准當下補抓 */
    await refetch(aiApiKeys.myRequests);
    await flush();
    await flush(); // 補抓在核准提醒的 effect 裡才送出，再等它回來

    expect(mocks.toast.success).toHaveBeenCalledTimes(1);
    expect(mocks.listMyCredentials).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("核准後的金鑰");
  });
});
