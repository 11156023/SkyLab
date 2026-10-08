// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, test, vi } from "vitest";
import { RubricsTab, recoverSessionProposal } from "./AiJudgePanel";
import { AiJudgeService } from "../../../services/aiJudge";
import { ConfirmProvider } from "../../../components/ConfirmDialog/ConfirmProvider";
import i18n from "../../../i18n";

const mounted = new Map();
const originalScroll = Element.prototype.scrollIntoView;
afterEach(async () => {
  for (const [root, container] of mounted) {
    await act(async () => root.unmount());
    container.remove();
  }
  mounted.clear();
  Element.prototype.scrollIntoView = originalScroll;
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function proposalMessage(changes = {}) {
  return {
    id: "assistant-1", role: "assistant", content: "已完成核對，請確認提案",
    metadata_json: {
      source_file_id: "file-1", analysis_revision: 3,
      rubric_proposal: [{ id: "version", title: "恢復的版本核對", operation: "add", detectable: "auto" }],
      item_results: [{ item_id: "version", status: "ready", operation: { id: "version" } }],
      ...changes,
    },
  };
}

test.each([
  { source_file_id: "other" }, { analysis_revision: 2 },
  { proposal_dismissed: true }, { rubric_proposal: null },
])("來源、版本或忽略狀態不適用時不恢復：%j", (changes) => {
  expect(recoverSessionProposal([proposalMessage(changes)], "file-1", 3)).toBeNull();
});

test.each([
  { id: "next-user", role: "user", content: "下一輪" },
  { role: "user", content: "尚在送出" },
  { id: "next-assistant", role: "assistant", metadata_json: { status: "analysis_error" } },
])("較新的一輪沒有提案時，不拿舊提案補上：%j", (latest) => {
  expect(recoverSessionProposal([proposalMessage(), latest], "file-1", 3)).toBeNull();
});

async function mountWithMessages(rows) {
  await i18n.changeLanguage("zh-TW");
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([{
    id: "file-1", analysis_revision: 3, source_type: "created", template_key: "linux",
    display_name: "恢復測試", environment_keys: ["linux"],
    analysis_json: { items: [], total_items: 0 },
  }]);
  const list = vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue(rows);
  const save = vi.spyOn(AiJudgeService, "updateFileAnalysis");
  const send = vi.spyOn(AiJudgeService, "sendSessionMessage");
  const build = vi.spyOn(AiJudgeService, "createSessionScriptSet");
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mounted.set(root, container);
  await act(async () => root.render(
    <ConfirmProvider><RubricsTab classId="class-1" judgeSession={{ id: "session-1", selected_file_id: "file-1" }} /></ConfirmProvider>,
  ));
  return { container, list, save, send, build };
}

test("重開頁面恢復待確認提案，不自動套用或製作腳本", async () => {
  const { container, save, send, build } = await mountWithMessages([proposalMessage({ is_refine: true })]);
  expect(container.textContent).toContain("恢復的版本核對");
  expect(container.querySelector('[aria-label="AI 提案"]')).toBeTruthy();
  expect(save).not.toHaveBeenCalled();
  expect(send).not.toHaveBeenCalled();
  expect(build).not.toHaveBeenCalled();
});

test("忽略會保存到伺服器，重開頁面仍保持忽略", async () => {
  const message = proposalMessage();
  const dismiss = vi.spyOn(AiJudgeService, "dismissSessionProposal").mockImplementation(async () => {
    message.metadata_json.proposal_dismissed = true;
  });
  const { container } = await mountWithMessages([message]);
  const ignore = [...container.querySelectorAll("button")].find((button) => button.textContent.trim() === "忽略");
  await act(async () => ignore.click());
  const dialog = document.querySelector('[role="alertdialog"]');
  await act(async () => [...dialog.querySelectorAll("button")].find((button) => button.textContent.trim() === "忽略").click());
  expect(dismiss).toHaveBeenCalledWith("class-1", "session-1", "assistant-1");
  expect(container.querySelector('[aria-label="AI 提案"]')).toBeNull();
  const [root] = mounted.keys();
  await act(async () => root.unmount());
  mounted.delete(root);
  container.remove();
  const reopened = await mountWithMessages([message]);
  expect(reopened.container.querySelector('[aria-label="AI 提案"]')).toBeNull();
});

test("重開處理中的分析會停用重送，背景更新後恢復提案", async () => {
  vi.useFakeTimers();
  const { container, list, send } = await mountWithMessages([{
    id: "user-1", role: "user", content: "核對版本",
    metadata_json: { processing: true, processing_deadline: Date.now() / 1000 + 570 },
  }]);
  expect(container.textContent).toContain("仍在處理");
  expect(container.querySelector("textarea").disabled).toBe(true);
  list.mockResolvedValue([proposalMessage()]);
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(container.querySelector('[aria-label="AI 提案"]')).toBeTruthy();
  expect(container.querySelector("textarea").disabled).toBe(false);
  expect(send).not.toHaveBeenCalled();
});

test("忽略未成功保存時，提案仍可查看及套用", async () => {
  const dismiss = vi.spyOn(AiJudgeService, "dismissSessionProposal").mockRejectedValue({ status: 503 });
  const { container } = await mountWithMessages([proposalMessage()]);
  await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent.trim() === "忽略").click());
  const dialog = document.querySelector('[role="alertdialog"]');
  await act(async () => [...dialog.querySelectorAll("button")].find((button) => button.textContent.trim() === "忽略").click());
  expect(dismiss).toHaveBeenCalledTimes(1);
  expect(container.querySelector('[aria-label="AI 提案"]')).toBeTruthy();
  const apply = [...container.querySelectorAll("button")].find((button) => button.textContent === i18n.t("AiJudgePanel.applyBtn", { ns: "teaching" }));
  expect(apply).toBeTruthy();
  expect(apply.disabled).toBe(false);
});

test("切換到另一個檢查後，舊請求的晚到回覆不會插入目前對話", async () => {
  const { container, send, list } = await mountWithMessages([]);
  let finish;
  send.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const textarea = container.querySelector("textarea");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    setter.call(textarea, "舊檢查要求");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  });
  expect(send).toHaveBeenCalledTimes(1);
  list.mockResolvedValue([]);
  const [root] = mounted.keys();
  await act(async () => root.render(
    <ConfirmProvider><RubricsTab classId="class-1" judgeSession={{ id: "session-2", selected_file_id: "file-1" }} /></ConfirmProvider>,
  ));
  await act(async () => finish({
    user_message: { id: "user-old", role: "user", content: "舊檢查要求" },
    assistant_message: proposalMessage(), rubric_proposal: proposalMessage().metadata_json.rubric_proposal, base_revision: 3,
  }));
  expect(container.textContent).not.toContain("恢復的版本核對");
  expect(container.textContent).not.toContain("已完成核對，請確認提案");
});
