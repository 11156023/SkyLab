// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import AiJudgePanel, { JUDGE_COLUMN_WIDTHS, readStoredColumnWidths } from "./AiJudgePanel";
import { AiJudgeService } from "../../../services/aiJudge";
import { ConfirmProvider } from "../../../components/ConfirmDialog/ConfirmProvider";
import { UnsavedChangesProvider } from "../../../contexts/UnsavedChangesContext";
import i18n from "../../../i18n";

const originalScrollIntoView = Element.prototype.scrollIntoView;
let cleanup = null;

afterEach(async () => {
  await cleanup?.();
  cleanup = null;
  Element.prototype.scrollIntoView = originalScrollIntoView;
  vi.restoreAllMocks();
});

const WEEKS = [
  { id: "week-2", week_number: 2, session_date: "2026-09-08", title: "" },
  { id: "week-1", week_number: 1, session_date: "2026-09-01", title: "Linux 權限" },
];

async function mountPanel() {
  Element.prototype.scrollIntoView = vi.fn();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/?week=week-2"]}>
        <ConfirmProvider>
          <UnsavedChangesProvider>
            <AiJudgePanel classId="class-1" members={[]} weeks={WEEKS} />
          </UnsavedChangesProvider>
        </ConfirmProvider>
      </MemoryRouter>,
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  cleanup = async () => {
    await act(async () => root.unmount());
    container.remove();
  };
  return container;
}

const tr = (key, options) => i18n.t(`AiJudgePanel.${key}`, { ns: "teaching", ...options });

test("側邊欄依序列出每一週，標出哪幾週已建立檢查", async () => {
  vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([
    { id: "session-1", title: "Linux 權限", status: "active", teaching_class_week_id: "week-1" },
  ]);
  vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);

  const container = await mountPanel();
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

  const container = await mountPanel();
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

  const container = await mountPanel();
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

  const container = await mountPanel();
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
});
