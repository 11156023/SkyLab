// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import JobDetailDialog from "./JobDetailDialog";
import { JobsService } from "../../services/jobs";
import { createTestQueryClient, withQueryClient } from "../../queries/testing";

vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "student" } }) }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: (key) => key }),
}));
vi.mock("../../services/jobs", () => ({ JobsService: { detail: vi.fn() } }));

let host;
let root;
let client;

const job = (id, title, status) => ({
  item: { id, title, status, created_at: "2026-09-27T00:00:00Z" },
  extra: {},
});

/* 假時鐘下 query 的通知走 setTimeout 0（query 更新 → 元件訂閱兩層）；
   fake timers 在 tick 期間把 0ms 計時器排到 +1ms，所以推進幾毫秒才會全部送出 */
async function settle() {
  await act(async () => { await vi.advanceTimersByTimeAsync(5); });
}

/* 推進到輪詢時間點後再送出這一輪的結果通知 */
async function advance(ms) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
  await settle();
}

async function render(jobId) {
  await act(async () => {
    root.render(withQueryClient(<JobDetailDialog jobId={jobId} onClose={() => {}} />, client));
  });
  await settle();
}

beforeEach(() => {
  /* reset 而不是 clear：連 mockResolvedValueOnce 排好的回應一起清掉，前一個測試沒用完的不會漏到下一個 */
  vi.resetAllMocks();
  vi.useFakeTimers();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
  vi.useRealTimers();
});

test("keeps polling after a failed background refresh and shows the final status", async () => {
  JobsService.detail
    .mockResolvedValueOnce(job("a", "任務甲", "running"))
    .mockRejectedValueOnce(new Error("502 Bad Gateway"))
    .mockResolvedValueOnce(job("a", "任務甲", "completed"));

  await render("a");
  expect(document.body.textContent).toContain("JobRow.statusRunning");

  await advance(3000);
  expect(document.body.textContent).toContain("502 Bad Gateway");
  /* 背景刷新失敗時照舊顯示上一份內容 */
  expect(document.body.textContent).toContain("JobRow.statusRunning");

  await advance(6000);
  expect(JobsService.detail).toHaveBeenCalledTimes(3);
  expect(document.body.textContent).not.toContain("502 Bad Gateway");
  expect(document.body.textContent).not.toContain("JobRow.statusRunning");
  expect(document.body.textContent).toContain("JobRow.statusCompleted");

  /* 到終態就不再輪詢 */
  await advance(30_000);
  expect(JobsService.detail).toHaveBeenCalledTimes(3);
});

test("a failed first load shows the error and does not poll", async () => {
  JobsService.detail.mockRejectedValueOnce(new Error("404 Not Found"));

  await render("a");
  expect(document.body.textContent).toContain("404 Not Found");

  await advance(30_000);
  expect(JobsService.detail).toHaveBeenCalledTimes(1);
});

test("switching to another job id never shows the previous job's details", async () => {
  JobsService.detail
    .mockResolvedValueOnce(job("a", "任務甲", "completed"))
    .mockReturnValueOnce(new Promise(() => {}));

  await render("a");
  expect(document.body.textContent).toContain("任務甲");

  await render("b");
  expect(document.body.textContent).not.toContain("任務甲");
  expect(document.body.textContent).toContain("JobDetailDialog.loading");
});

test("closing stops polling while the exit animation keeps the content", async () => {
  JobsService.detail.mockResolvedValue(job("a", "任務甲", "running"));

  await render("a");
  await act(async () => {
    root.render(withQueryClient(<JobDetailDialog jobId={null} onClose={() => {}} />, client));
  });
  /* 離場動畫（150ms）期間內容還在 */
  expect(document.body.textContent).toContain("任務甲");

  await advance(30_000);
  expect(JobsService.detail).toHaveBeenCalledTimes(1);
  expect(document.body.textContent).not.toContain("任務甲");
});

test("a machine expiry date shows its calendar day plus the local cutoff time", async () => {
  const previousTz = process.env.TZ;
  process.env.TZ = "Asia/Taipei";
  try {
    JobsService.detail.mockResolvedValueOnce({
      ...job("a", "任務甲", "completed"),
      extra: { expiry_date: "2026-10-15" },
    });
    await render("a");
    // 到期界線是 00:00 UTC，台灣是當天 08:00
    /* 日期與時間間的空白依 ICU 版本可能是 U+202F，比對前統一成一般空白 */
    expect(document.body.textContent.replace(/\s/g, " ")).toContain("2026/10/15（08:00 截止）");
  } finally {
    if (previousTz === undefined) delete process.env.TZ;
    else process.env.TZ = previousTz;
  }
});
