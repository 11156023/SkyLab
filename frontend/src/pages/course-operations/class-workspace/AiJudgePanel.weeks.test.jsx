// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AiJudgePanel, { JUDGE_COLUMN_WIDTHS, readStoredColumnWidths } from "./AiJudgePanel";
import { AiJudgeService } from "../../../services/aiJudge";
import { ConfirmProvider } from "../../../components/ConfirmDialog/ConfirmProvider";
import { UnsavedChangesProvider } from "../../../contexts/UnsavedChangesContext";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import i18n from "../../../i18n";

const originalScrollIntoView = Element.prototype.scrollIntoView;
let cleanup = null;
let queryClient;

beforeEach(() => {
  /* TanStack Query 預設用 setTimeout(0) 通知元件；改成同步通知，讀取結果在同一個 act 裡就上畫面 */
  notifyManager.setScheduler((callback) => callback());
  queryClient = createTestQueryClient();
});

afterEach(async () => {
  await cleanup?.();
  cleanup = null;
  Element.prototype.scrollIntoView = originalScrollIntoView;
  notifyManager.setScheduler((callback) => setTimeout(callback, 0));
  queryClient.clear();
  vi.restoreAllMocks();
});

const WEEKS = [
  { id: "week-2", week_number: 2, session_date: "2026-09-08", title: "" },
  { id: "week-1", week_number: 1, session_date: "2026-09-01", title: "Linux 權限" },
];

function CurrentLocation() {
  const { search } = useLocation();
  return <output data-test-location="">{search}</output>;
}

async function mountPanel({ initialEntry = "/?week=week-2", weeks = WEEKS } = {}) {
  Element.prototype.scrollIntoView = vi.fn();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  cleanup = async () => {
    await act(async () => root.unmount());
    container.remove();
  };
  async function renderWeeks(nextWeeks) {
    await act(async () => {
      root.render(withQueryClient(
        <MemoryRouter initialEntries={[initialEntry]}>
          <CurrentLocation />
          <ConfirmProvider>
            <UnsavedChangesProvider>
              <AiJudgePanel classId="class-1" members={[]} weeks={nextWeeks} />
            </UnsavedChangesProvider>
          </ConfirmProvider>
        </MemoryRouter>,
        queryClient,
      ));
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
  await renderWeeks(weeks);
  return { container, renderWeeks };
}

const tr = (key, options) => i18n.t(`AiJudgePanel.${key}`, { ns: "teaching", ...options });

const THREE_WEEKS = [
  { id: "week-3", week_number: 3, session_date: "2020-09-15", title: "網站部署" },
  ...WEEKS.map((week) => ({ ...week, session_date: week.session_date.replace("2026", "2020") })),
];

const selectedWeek = (container) => container.querySelector('[data-guide="ai-judge-sessions"] button[aria-current="true"]');
const locationParams = (container) => new URLSearchParams(container.querySelector("[data-test-location]").textContent);

test.each(["/?source=dashboard&check=legacy", "/?week=missing&source=dashboard&check=legacy"])("首次進入 %s 時預設第一週，只有第三週有檢查也不跳週", async (initialEntry) => {
  vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([
    { id: "session-3", title: "第 3 週", status: "active", teaching_class_week_id: "week-3" },
  ]);
  vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
  const ensure = vi.spyOn(AiJudgeService, "ensureWeekSession");

  const { container } = await mountPanel({ initialEntry, weeks: THREE_WEEKS });

  expect(selectedWeek(container)?.textContent).toContain(tr("weekRowTitle", { week: 1 }));
  expect(container.textContent).toContain(tr("emptyWeekTitle", { week: 1 }));
  expect(locationParams(container).get("week")).toBe("week-1");
  expect(locationParams(container).get("source")).toBe("dashboard");
  expect(locationParams(container).has("check")).toBe(false);
  expect(ensure).not.toHaveBeenCalled();
});

test("網址指定第三週時沿用，重新整理也保留手動選取的週次", async () => {
  vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([]);
  const { container } = await mountPanel({ initialEntry: "/?week=week-3", weeks: THREE_WEEKS });
  expect(selectedWeek(container)?.textContent).toContain(tr("weekRowTitle", { week: 3 }));

  await act(async () => {
    container.querySelectorAll('[data-guide="ai-judge-sessions"] [role="listitem"] button')[1].click();
  });
  expect(locationParams(container).get("week")).toBe("week-2");
  const initialEntry = `/${container.querySelector("[data-test-location]").textContent}`;
  await cleanup();
  const reloaded = await mountPanel({ initialEntry, weeks: THREE_WEEKS });
  expect(selectedWeek(reloaded.container)?.textContent).toContain(tr("weekRowTitle", { week: 2 }));
});

test("週次延遲載入仍預設第一週；資料刷新保留選取，選取週次移除才回第一週", async () => {
  vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([]);
  const { container, renderWeeks } = await mountPanel({ initialEntry: "/", weeks: [] });
  expect(selectedWeek(container)).toBeNull();

  await renderWeeks(THREE_WEEKS);
  expect(selectedWeek(container)?.textContent).toContain(tr("weekRowTitle", { week: 1 }));
  await act(async () => {
    container.querySelectorAll('[data-guide="ai-judge-sessions"] [role="listitem"] button')[2].click();
  });
  await renderWeeks(THREE_WEEKS.map((week) => ({ ...week })));
  expect(selectedWeek(container)?.textContent).toContain(tr("weekRowTitle", { week: 3 }));
  expect(locationParams(container).get("week")).toBe("week-3");

  await renderWeeks(THREE_WEEKS.filter((week) => week.id !== "week-3"));
  expect(selectedWeek(container)?.textContent).toContain(tr("weekRowTitle", { week: 1 }));
  expect(locationParams(container).get("week")).toBe("week-1");
});

test("側邊欄依序列出每一週，標出哪幾週已建立檢查", async () => {
  vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([
    { id: "session-1", title: "Linux 權限", status: "active", teaching_class_week_id: "week-1" },
  ]);
  vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);

  const { container } = await mountPanel();
  const rows = [...container.querySelectorAll('[data-guide="ai-judge-sessions"] [role="listitem"]')];

  expect(rows).toHaveLength(2);
  expect(rows[0].textContent).toContain(tr("weekRowTitle", { week: 1 }));
  expect(rows[0].textContent).toContain(tr("weekHasCheck"));
  expect(rows[1].textContent).toContain(tr("weekNoTopic"));
  expect(rows[1].textContent).toContain(tr("weekNoCheck"));
  // 沒有檢查的週次沒有「⋯」選單
  expect(rows[1].querySelector('[aria-haspopup="menu"]')).toBeNull();
  // 網址指定第 2 週，而第 2 週還沒有檢查：顯示建立按鈕，不會自動建立
  expect(container.textContent).toContain(tr("emptyWeekTitle", { week: 2 }));
});

test("按「建立這週的檢查」才建立，之後直接進入那週的任務設定", async () => {
  vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
  const ensure = vi.spyOn(AiJudgeService, "ensureWeekSession").mockResolvedValue({
    id: "session-2", title: "第 2 週檢查", status: "active", teaching_class_week_id: "week-2",
  });

  const { container } = await mountPanel();
  expect(ensure).not.toHaveBeenCalled();
  const createButton = [...container.querySelectorAll("button")]
    .find((button) => button.textContent.includes(tr("createWeekCheckBtn")));
  await act(async () => {
    createButton.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });

  expect(ensure).toHaveBeenCalledExactlyOnceWith("class-1", "week-2");
  expect(container.querySelector('[data-guide="ai-judge-rubric-items"]')).toBeTruthy();
  const rows = [...container.querySelectorAll('[data-guide="ai-judge-sessions"] [role="listitem"]')];
  expect(rows[1].textContent).toContain(tr("weekHasCheck"));
});

test("週次欄與助理欄之間各有一個可拖拉的分隔線，寬度沿用上次的設定", async () => {
  window.localStorage.setItem(JUDGE_COLUMN_WIDTHS.week.storageKey, "260");
  vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([
    { id: "session-1", title: "Linux 權限", status: "active", teaching_class_week_id: "week-2" },
  ]);
  vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);

  const { container } = await mountPanel();
  const separators = container.querySelectorAll('[role="separator"][aria-orientation="vertical"]');
  expect(separators).toHaveLength(2);
  const workspace = container.querySelector("[data-judge-workspace]");
  expect(workspace.style.getPropertyValue("--judge-week-w")).toBe("260px");
  window.localStorage.removeItem(JUDGE_COLUMN_WIDTHS.week.storageKey);
});

test("存下來的欄寬超出範圍或不是數字時不採用", () => {
  const { week, chat } = JUDGE_COLUMN_WIDTHS;
  window.localStorage.setItem(week.storageKey, String(week.max + 100));
  window.localStorage.setItem(chat.storageKey, "abc");
  expect(readStoredColumnWidths()).toEqual({});
  window.localStorage.setItem(chat.storageKey, "420");
  expect(readStoredColumnWidths()).toEqual({ [chat.cssVar]: "420px" });
  window.localStorage.removeItem(week.storageKey);
  window.localStorage.removeItem(chat.storageKey);
});

test("在導師核查換週次時停在導師核查，不會跳回任務設定", async () => {
  vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([
    { id: "session-1", title: "第 1 週", status: "active", teaching_class_week_id: "week-1" },
    { id: "session-2", title: "第 2 週", status: "active", teaching_class_week_id: "week-2" },
  ]);
  vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "getSessionProgress").mockResolvedValue({ items: [], students: [] });
  vi.spyOn(AiJudgeService, "listSessionRunBatches").mockResolvedValue([]);

  const { container } = await mountPanel();
  const reviewTab = () => container.querySelector('[data-guide-tab="ai-judge-tab-review"]');
  await act(async () => {
    reviewTab().click();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(reviewTab().getAttribute("aria-pressed")).toBe("true");

  const weekOne = container.querySelectorAll('[data-guide="ai-judge-sessions"] [role="listitem"] button')[0];
  await act(async () => {
    weekOne.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(reviewTab().getAttribute("aria-pressed")).toBe("true");
  expect(container.querySelector('[data-guide="ai-judge-review"]')).toBeTruthy();
  expect(selectedWeek(container)?.textContent).toContain(tr("weekRowTitle", { week: 1 }));
  expect(locationParams(container).get("week")).toBe("week-1");
});
