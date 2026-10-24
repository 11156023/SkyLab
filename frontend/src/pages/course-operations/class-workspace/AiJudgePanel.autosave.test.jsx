// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const toastApi = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => toastApi }));

import { RubricsTab } from "./AiJudgePanel";
import { AiJudgeService } from "../../../services/aiJudge";
import { ConfirmProvider } from "../../../components/ConfirmDialog/ConfirmProvider";
import { aiJudgeKeys } from "../../../queries/aiJudge";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import i18n from "../../../i18n";

const tt = (key) => i18n.t(`AiJudgePanel.${key}`, { ns: "teaching" });
const originalScrollIntoView = Element.prototype.scrollIntoView;
const wait = (ms) => act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); });
let queryClient;

beforeEach(() => {
  /* TanStack Query 預設用 setTimeout(0) 通知元件；改成同步通知，讀取結果在同一個 act 裡就上畫面 */
  notifyManager.setScheduler((callback) => callback());
  queryClient = createTestQueryClient();
});

afterEach(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
  notifyManager.setScheduler((callback) => setTimeout(callback, 0));
  queryClient.clear();
  vi.restoreAllMocks();
  Object.values(toastApi).forEach((fn) => fn.mockReset());
});

function file(title, revision) {
  return {
    id: "file-1",
    template_key: "linux",
    environment_keys: ["linux"],
    analysis_revision: revision,
    source_type: "created",
    display_name: "測試檢查表",
    updated_at: "2026-10-01T00:00:00Z",
    analysis_json: {
      items: [{ id: "item-1", title, checked: false, detectable: "manual", judgement_mode: "ai", check_steps: [] }],
      total_items: 1,
    },
  };
}

async function setup() {
  const listFiles = vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([file("舊標題", 3)]);
  vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
  const sendMessage = vi.spyOn(AiJudgeService, "sendSessionMessage").mockResolvedValue({});
  const save = vi.spyOn(AiJudgeService, "updateFileAnalysis")
    .mockRejectedValue(Object.assign(new Error("伺服器暫時無法使用"), { status: 503 }));

  Element.prototype.scrollIntoView = vi.fn();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(withQueryClient(
      <ConfirmProvider>
        <RubricsTab classId="class-1" judgeSession={{ id: "session-1", selected_file_id: "file-1" }} />
      </ConfirmProvider>,
      queryClient,
    ));
  });
  await wait(20);

  const titleInput = () => container.querySelector("td textarea");
  const setValue = (element, value) => act(() => {
    const proto = Object.getPrototypeOf(element);
    Object.getOwnPropertyDescriptor(proto, "value").set.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });

  // 編輯標題 → debounce 後自動儲存失敗（503）
  setValue(titleInput(), "新標題");
  await wait(750);

  const cleanup = () => {
    act(() => root.unmount());
    container.remove();
  };
  return { container, titleInput, setValue, listFiles, sendMessage, save, cleanup };
}

const unsavedNotice = (container) => container.querySelector('[data-autosave-status="unsaved"]');
const buttonWith = (container, text) => [...container.querySelectorAll("button")].find((b) => b.textContent.includes(text));

describe("RubricsTab 自動儲存失敗（409 以外）", () => {
  test("保留修改並標示未儲存；背景重抓不覆蓋；手動重試存的是同一份修改", async () => {
    const view = await setup();
    expect(view.save).toHaveBeenCalledTimes(1);
    expect(view.titleInput().value).toBe("新標題");
    expect(unsavedNotice(view.container)).not.toBeNull();
    expect(unsavedNotice(view.container).textContent).toContain(tt("unsavedTitle"));
    expect(toastApi.error).toHaveBeenCalledWith("伺服器暫時無法使用");

    /* 背景刷新拿到伺服器上的舊內容，不能把畫面上的修改蓋掉 */
    act(() => { void queryClient.refetchQueries({ queryKey: aiJudgeKeys.files("class-1") }); });
    await wait(10);
    expect(view.listFiles).toHaveBeenCalledTimes(2);
    expect(view.titleInput().value).toBe("新標題");

    view.save.mockImplementation(async (_classId, _fileId, analysis, expected) => ({
      ...file(analysis.items[0].title, expected + 1),
      analysis_json: analysis,
    }));
    await act(async () => buttonWith(view.container, tt("unsavedRetryBtn")).click());
    await wait(10);

    expect(view.save.mock.calls.at(-1)[2].items[0].title).toBe("新標題");
    expect(view.save.mock.calls.at(-1)[3]).toBe(3);
    expect(unsavedNotice(view.container)).toBeNull();
    expect(view.titleInput().value).toBe("新標題");
    expect(toastApi.success).toHaveBeenCalledWith(tt("unsavedRetrySucceeded"));
    view.cleanup();
  });

  test("有未儲存的修改時不傳送給助理，保留輸入草稿並說明原因", async () => {
    const view = await setup();
    const textarea = view.container.querySelector("textarea[aria-label]");
    view.setValue(textarea, "幫我檢查這份檢查表");
    await act(async () => {
      textarea.closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await wait(10);

    expect(view.sendMessage).not.toHaveBeenCalled();
    expect(toastApi.error).toHaveBeenCalledWith(tt("unsavedBlocksChat"));
    expect(view.container.querySelector("textarea[aria-label]").value).toBe("幫我檢查這份檢查表");
    expect(unsavedNotice(view.container)).not.toBeNull();
    view.cleanup();
  });

  test("有未儲存的修改時不製作腳本，顯示明確原因", async () => {
    const view = await setup();
    expect(view.container.textContent).toContain(tt("unsavedBuildHint"));
    await act(async () => buttonWith(view.container, tt("saveAndBuildBtn")).click());
    await wait(10);

    expect(view.sendMessage).not.toHaveBeenCalled();
    expect(view.container.textContent).toContain(tt("unsavedBlocksBuild"));
    expect(toastApi.error).toHaveBeenCalledWith(tt("unsavedBlocksBuild"));
    expect(view.titleInput().value).toBe("新標題");
    view.cleanup();
  });

  test("製作前重試儲存成功就照常往下走", async () => {
    const view = await setup();
    view.save.mockImplementation(async (_classId, _fileId, analysis, expected) => ({
      ...file(analysis.items[0].title, expected + 1),
      analysis_json: analysis,
    }));
    await act(async () => buttonWith(view.container, tt("saveAndBuildBtn")).click());
    await wait(10);

    expect(view.save.mock.calls[1][2].items[0].title).toBe("新標題");
    expect(view.sendMessage).toHaveBeenCalled();
    expect(view.sendMessage.mock.calls[0][3]).toBe(4);
    view.cleanup();
  });
});

describe("RubricsTab 套用 AI 提案時儲存失敗", () => {
  async function mountWithProposal(error) {
    await i18n.changeLanguage("zh-TW");
    vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([{
      ...file("既有項目", 3),
      analysis_json: { items: [], total_items: 0 },
    }]);
    vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([{
      id: "assistant-1", role: "assistant", content: "已完成核對，請確認提案",
      metadata_json: {
        source_file_id: "file-1", analysis_revision: 3,
        rubric_proposal: [{ id: "version", title: "AI 新增的項目", operation: "add", detectable: "manual" }],
        item_results: [{ item_id: "version", status: "ready", operation: { id: "version" } }],
      },
    }]);
    const save = vi.spyOn(AiJudgeService, "updateFileAnalysis").mockRejectedValue(error);
    Element.prototype.scrollIntoView = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(withQueryClient(
      <ConfirmProvider><RubricsTab classId="class-1" judgeSession={{ id: "session-1", selected_file_id: "file-1" }} /></ConfirmProvider>,
      queryClient,
    )));
    await wait(20);
    expect(container.querySelector('[aria-label="AI 提案"]')).toBeTruthy();
    await act(async () => buttonWith(container, tt("applyBtn")).click());
    await wait(10);
    const cleanup = () => {
      act(() => root.unmount());
      container.remove();
    };
    return { container, save, cleanup };
  }

  test("非 409：保留套用後的內容並標成未儲存，提案收起來避免重複套用", async () => {
    const view = await mountWithProposal(Object.assign(new Error("逾時"), { status: 504 }));
    expect(view.save).toHaveBeenCalledTimes(1);
    expect(view.container.querySelector('[aria-label="AI 提案"]')).toBeNull();
    expect(view.container.querySelector("td textarea").value).toBe("AI 新增的項目");
    expect(unsavedNotice(view.container)).not.toBeNull();
    expect(toastApi.success).not.toHaveBeenCalledWith(tt("proposalApplied"));
    view.cleanup();
  });

  test("409：照舊退回套用前的內容", async () => {
    const view = await mountWithProposal(Object.assign(new Error("conflict"), { status: 409 }));
    expect(view.container.querySelector("td textarea")).toBeNull();
    expect(unsavedNotice(view.container)).toBeNull();
    expect(toastApi.error).toHaveBeenCalledWith(tt("rubricConflict"));
    view.cleanup();
  });
});
