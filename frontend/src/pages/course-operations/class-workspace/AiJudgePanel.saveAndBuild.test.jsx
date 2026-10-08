// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";
import AiJudgePanel, { RubricsTab } from "./AiJudgePanel";
import { AiJudgeService, RUBRIC_POLISH_PROMPT } from "../../../services/aiJudge";
import { ConfirmProvider } from "../../../components/ConfirmDialog/ConfirmProvider";
import { UnsavedChangesProvider } from "../../../contexts/UnsavedChangesContext";
import i18n from "../../../i18n";

const originalScrollIntoView = Element.prototype.scrollIntoView;
const mounted = new Map();

afterEach(async () => {
  for (const [root, container] of mounted) {
    await act(async () => root.unmount());
    container.remove();
  }
  mounted.clear();
  Element.prototype.scrollIntoView = originalScrollIntoView;
  vi.restoreAllMocks();
});

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function fixture({ alreadyTyped = false } = {}) {
  const version = {
    id: "item-version",
    title: "Python 版本檢查",
    checked: false,
    detectable: "auto",
    judgement_mode: "ai",
    detection_method: "取得 Python 版本並確認為 3.12",
    missing_information: [],
    fallback: null,
    check_steps: [{ argv: ["python3", "--version"], timeout_seconds: 10 }],
  };
  const evidence = {
    ...version,
    id: "item-evidence",
    title: "收集報告",
    judgement_mode: "teacher",
    detection_method: "收集報告供導師核查",
    check_steps: [{ argv: ["cat", "/srv/report.txt"], timeout_seconds: 10 }],
  };
  const typedVersion = {
    ...version,
    check_steps: [{
      id: "python.version",
      title: "Python 版本",
      collector: { type: "command", argv: ["python3", "--version"], timeout_seconds: 10 },
      assertion: { type: "text_contains", expected: "3.12" },
    }],
  };
  const typedEvidence = {
    ...evidence,
    check_steps: [{ id: "report.content", title: "報告內容", collector: { type: "file_text", path: "/srv/report.txt", read_mode: "full" } }],
  };
  const untouched = { ...typedVersion, id: "item-keep", title: "另一個既有項目", check_steps: [{ ...typedVersion.check_steps[0], id: "python.other" }] };
  const expectedItems = [typedVersion, typedEvidence, untouched];
  let storedFile = {
    id: "file-1",
    template_key: "linux",
    environment_keys: ["linux"],
    analysis_revision: 3,
    source_type: "created",
    display_name: "測試檢查表",
    updated_at: "2026-10-08T00:00:00Z",
    analysis_json: {
      items: alreadyTyped ? expectedItems : [version, evidence, untouched],
      total_items: 3,
      checked_count: 0,
      auto_count: 3,
      partial_count: 0,
      manual_count: 0,
      detectability_needs_review: true,
      pending_review_item_ids: ["item-version", "item-evidence"],
    },
  };
  const proposals = alreadyTyped ? [] : [typedVersion, typedEvidence].map((item) => ({ ...item, operation: "update" }));
  const response = {
    user_message: { id: "user-1", role: "user", content: RUBRIC_POLISH_PROMPT, metadata_json: { ui_hidden: true } },
    assistant_message: {
      id: "assistant-1",
      role: "assistant",
      content: "重新核對已完成，所有檢查項目都具備腳本所需的取證資訊；目前檢查表已保留，可開始製作檢查腳本。",
      metadata_json: {
        status: "resolved",
        script_ready: true,
        item_results: proposals.map((item) => ({ item_id: item.id, status: item.judgement_mode === "teacher" ? "teacher_review" : "ready", operation: { id: item.id } })),
      },
    },
    rubric_proposal: proposals,
    base_revision: 3,
  };
  const artifact = { artifact_set_id: "set-1", status: "approved", source_analysis_revision: 4, children: [{ id: "script-1", status: "approved" }] };
  vi.spyOn(AiJudgeService, "listFiles").mockImplementation(async () => [storedFile]);
  vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
  const sendMessage = vi.spyOn(AiJudgeService, "sendSessionMessage").mockResolvedValue(response);
  const save = vi.spyOn(AiJudgeService, "updateFileAnalysis").mockImplementation(async (classId, fileId, analysis, revision) => {
    storedFile = { ...storedFile, analysis_json: analysis, analysis_revision: revision + 1 };
    return storedFile;
  });
  const createScript = vi.spyOn(AiJudgeService, "createSessionScriptSet").mockResolvedValue(artifact);
  const onScriptCreated = vi.fn();
  return { response, artifact, expectedItems, sendMessage, save, createScript, onScriptCreated, getFile: () => storedFile };
}

async function mount(element) {
  Element.prototype.scrollIntoView = vi.fn();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.set(root, container);
  await act(async () => root.render(element));
  return {
    container,
    unmount: async () => {
      await act(async () => root.unmount());
      mounted.delete(root);
      container.remove();
    },
  };
}

function mountRubrics(state) {
  return mount(
    <ConfirmProvider>
      <RubricsTab classId="class-1" judgeSession={{ id: "session-1", selected_file_id: "file-1" }} onScriptCreated={state.onScriptCreated} />
    </ConfirmProvider>,
  );
}

function saveButton(container) {
  return [...container.querySelectorAll("button")].find((button) => button.textContent.includes("儲存並製作"));
}

async function clickSave(container) {
  const button = saveButton(container);
  expect(button).toBeTruthy();
  expect(button.disabled).toBe(false);
  await act(async () => button.click());
}

describe("一鍵儲存並製作", () => {
  test("自動保存 AI 與導師取證的 typed 更新，保留未變更項目，以新版本製作並通知跳頁", async () => {
    const state = fixture();
    const { container } = await mountRubrics(state);
    await clickSave(container);

    expect(state.sendMessage).toHaveBeenCalledExactlyOnceWith("class-1", "session-1", RUBRIC_POLISH_PROMPT, 3, { isRefine: true });
    expect(state.save).toHaveBeenCalledExactlyOnceWith("class-1", "file-1", expect.objectContaining({
      items: state.expectedItems,
      detectability_needs_review: false,
      pending_review_item_ids: [],
    }), 3);
    expect(state.createScript).toHaveBeenCalledExactlyOnceWith("class-1", "session-1", 4);
    expect(state.onScriptCreated).toHaveBeenCalledExactlyOnceWith(state.artifact);
    expect(container.querySelector('[aria-label="AI 提案"]')).toBeNull();
    expect(container.textContent).toContain(state.response.assistant_message.content);
  });

  test("無變更時也保存核對狀態並製作，不需再次按按鈕", async () => {
    const state = fixture({ alreadyTyped: true });
    const { container } = await mountRubrics(state);
    await clickSave(container);
    expect(state.save).toHaveBeenCalledTimes(1);
    expect(state.getFile().analysis_json.pending_review_item_ids).toEqual([]);
    expect(state.createScript).toHaveBeenCalledExactlyOnceWith("class-1", "session-1", 4);
    expect(state.onScriptCreated).toHaveBeenCalledTimes(1);
  });

  test("核對結果尚未儲存成功前不製作；等待期間停用按鈕，完成後才跳頁", async () => {
    const state = fixture();
    const saving = deferred();
    state.save.mockReturnValueOnce(saving.promise);
    const { container } = await mountRubrics(state);
    await clickSave(container);
    expect(state.save).toHaveBeenCalledTimes(1);
    expect(state.createScript).not.toHaveBeenCalled();
    expect(state.onScriptCreated).not.toHaveBeenCalled();
    expect(container.querySelector('[data-generation-status="saving"]').disabled).toBe(true);
    expect(container.querySelector('[aria-label="AI 提案"]')).toBeNull();

    await act(async () => saving.resolve({ ...state.getFile(), analysis_json: state.save.mock.calls[0][2], analysis_revision: 8 }));
    expect(state.createScript).toHaveBeenCalledExactlyOnceWith("class-1", "session-1", 8);
    expect(state.onScriptCreated).toHaveBeenCalledTimes(1);
  });

  test.each(["needs_information", "unsupported", "analysis_error"])("核對結果為 %s 時保留回覆與提案，不自動保存或製作", async (status) => {
    const state = fixture();
    state.response.assistant_message.metadata_json.script_ready = false;
    state.response.assistant_message.metadata_json.status = status;
    state.response.assistant_message.content = "本次核對尚未通過，請補充檢查資訊。";
    const { container } = await mountRubrics(state);
    await clickSave(container);
    expect(container.textContent).toContain("本次核對尚未通過");
    expect(container.querySelector('[aria-label="AI 提案"]')).toBeTruthy();
    expect(state.save).not.toHaveBeenCalled();
    expect(state.createScript).not.toHaveBeenCalled();
    expect(state.onScriptCreated).not.toHaveBeenCalled();
  });

  test.each(["missing_readiness", "unvalidated_operation", "stale_revision"])("%s 不得套用或製作", async (invalid) => {
    const state = fixture();
    if (invalid === "missing_readiness") delete state.response.assistant_message.metadata_json.script_ready;
    if (invalid === "unvalidated_operation") state.response.assistant_message.metadata_json.item_results.pop();
    if (invalid === "stale_revision") state.response.base_revision = 2;
    const { container } = await mountRubrics(state);
    await clickSave(container);
    expect(state.save).not.toHaveBeenCalled();
    expect(state.createScript).not.toHaveBeenCalled();
    expect(state.onScriptCreated).not.toHaveBeenCalled();
  });

  test.each([422, 409])("保存失敗（%s）時不製作、不跳頁", async (status) => {
    const state = fixture();
    state.save.mockRejectedValueOnce(Object.assign(new Error("保存未完成"), { status }));
    const { container } = await mountRubrics(state);
    await clickSave(container);
    expect(state.save).toHaveBeenCalledTimes(1);
    expect(state.createScript).not.toHaveBeenCalled();
    expect(state.onScriptCreated).not.toHaveBeenCalled();
    expect(container.textContent).toContain(i18n.t("AiJudgePanel.buildReviewNotSaved", { ns: "teaching" }));
    expect(saveButton(container).getAttribute("aria-disabled")).not.toBe("true");
  });

  test("製作 API 失敗時保留已保存的核對內容，留在檢查表供重試", async () => {
    const state = fixture();
    state.createScript.mockRejectedValueOnce(new Error("製作服務暫時無法使用"));
    const { container } = await mountRubrics(state);
    await clickSave(container);
    expect(state.getFile().analysis_json.items).toEqual(state.expectedItems);
    expect(state.getFile().analysis_revision).toBe(4);
    expect(state.onScriptCreated).not.toHaveBeenCalled();
    expect(container.textContent).toContain("製作服務暫時無法使用");
    expect(saveButton(container).disabled).toBe(false);
  });

  test("腳本審查未通過時不跳到導師核查", async () => {
    const state = fixture();
    state.createScript.mockResolvedValueOnce({ ...state.artifact, status: "review_failed" });
    const { container } = await mountRubrics(state);
    await clickSave(container);
    expect(state.onScriptCreated).not.toHaveBeenCalled();
    expect(container.textContent).toContain(i18n.t("AiJudgePanel.buildReviewFailed", { ns: "teaching" }));
  });

  test("核對中連按不會重複送出或製作", async () => {
    const state = fixture();
    const review = deferred();
    state.sendMessage.mockReturnValueOnce(review.promise);
    const { container } = await mountRubrics(state);
    const button = saveButton(container);
    await clickSave(container);
    expect(button.disabled).toBe(true);
    await act(async () => button.click());
    expect(state.sendMessage).toHaveBeenCalledTimes(1);
    await act(async () => review.resolve(state.response));
    expect(state.save).toHaveBeenCalledTimes(1);
    expect(state.createScript).toHaveBeenCalledTimes(1);
  });

  test.each(["reviewing", "saving", "generating"])("%s 途中切換檢查，舊流程完成後不帶走目前頁面", async (stage) => {
    const state = fixture();
    const pending = deferred();
    if (stage === "reviewing") state.sendMessage.mockReturnValueOnce(pending.promise);
    else if (stage === "saving") state.save.mockReturnValueOnce(pending.promise);
    else state.createScript.mockReturnValueOnce(pending.promise);
    const view = await mountRubrics(state);
    await clickSave(view.container);
    await view.unmount();
    const result = stage === "reviewing" ? state.response
      : stage === "saving" ? { ...state.getFile(), analysis_json: state.save.mock.calls[0][2], analysis_revision: 4 }
        : state.artifact;
    await act(async () => pending.resolve(result));
    expect(state.onScriptCreated).not.toHaveBeenCalled();
    if (stage === "reviewing") {
      expect(state.save).not.toHaveBeenCalled();
      expect(state.createScript).not.toHaveBeenCalled();
    }
    if (stage === "saving") expect(state.createScript).not.toHaveBeenCalled();
  });

  test("正式工作區一鍵完成後顯示導師核查分頁，並載入新腳本", async () => {
    const state = fixture();
    vi.spyOn(AiJudgeService, "listSessions").mockResolvedValue([{ id: "session-1", title: "測試檢查", selected_file_id: "file-1", status: "active" }]);
    const listSets = vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([state.artifact]);
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
    const { container } = await mount(
      <MemoryRouter>
        <ConfirmProvider>
          <UnsavedChangesProvider>
            <AiJudgePanel classId="class-1" members={[]} />
          </UnsavedChangesProvider>
        </ConfirmProvider>
      </MemoryRouter>,
    );
    await clickSave(container);
    const reviewLabel = i18n.t("AiJudgePanel.tabReview", { ns: "teaching" });
    const reviewPanel = container.querySelector(`section[aria-label="${reviewLabel}"]`);
    expect(reviewPanel).toBeTruthy();
    expect(reviewPanel.hidden).toBe(false);
    const rubricsLabel = i18n.t("AiJudgePanel.tabRubrics", { ns: "teaching" });
    expect(container.querySelector(`section[aria-label="${rubricsLabel}"]`).hidden).toBe(true);
    expect(state.save).toHaveBeenCalledTimes(1);
    expect(state.createScript).toHaveBeenCalledExactlyOnceWith("class-1", "session-1", 4);
    expect(listSets).toHaveBeenCalledWith("class-1", "session-1");
  });
});
