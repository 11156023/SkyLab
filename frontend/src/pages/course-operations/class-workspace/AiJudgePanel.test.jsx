// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  ChatPanel,
  CommandLog,
  RubricTable,
  ProposalPanel,
  SaveAndCreateAction,
  ScriptGenerationNotice,
  SessionTitle,
  TeacherReviewTab,
  aggregateStudentCheckTotals,
  applyProposalOperations,
  attachStudentReports,
  checkOutcome,
  studentMachineChips,
  machinePowerState,
  checkpointBadge,
  buildMergedBatchReviewRows,
  buildBatchReviewRows,
  buildLegacyReviewRows,
  buildProposalDiff,
  buildStudentOverviewRows,
  getRubricDisplayName,
  getRubricItemsValue,
  getRubricReviewItemIds,
  getPendingRubricItemIds,
  getStudentOverviewStatus,
  resolveDetectabilityNeedsReview,
  sortStudentOverviewRows,
  getScriptCreationBlocker,
  getSessionMenuPosition,
  getScriptCreationDestination,
  getTargetReviewSummary,
  getCheckResultSummary,
  isReviewDraftDirty,
  getSelectableProposalIds,
  mergeNodeTeacherReview,
  mergeSessionMessages,
  resolveActiveWeekId,
  weekOptionLabel,
  getDefaultWeekId,
  proposalToolCallLines,
  RubricsTab,
} from "./AiJudgePanel";
import {
  AiJudgeService,
  RUBRIC_POLISH_PROMPT,
} from "../../../services/aiJudge";
import i18n from "../../../i18n";
import { ConfirmProvider } from "../../../components/ConfirmDialog/ConfirmProvider";
import { notifyManager } from "@tanstack/react-query";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";

const originalScrollIntoView = Element.prototype.scrollIntoView;
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
});

describe("mergeSessionMessages", () => {
  test("依 server id 去重排序，但相同內容的不同訊息仍保留", () => {
    expect(mergeSessionMessages(
      [
        { id: "assistant-1", content: "相同結果", created_at: "2026-09-15T00:00:02Z" },
        { id: "retry-1", content: "相同結果", created_at: "2026-09-15T00:00:03Z" },
      ],
      [
        { id: "assistant-1", content: "更新後結果", created_at: "2026-09-15T00:00:02Z" },
        { id: "user-1", content: "問題", created_at: "2026-09-15T00:00:01Z" },
      ],
    )).toEqual([
      { id: "user-1", content: "問題", created_at: "2026-09-15T00:00:01Z" },
      { id: "assistant-1", content: "更新後結果", created_at: "2026-09-15T00:00:02Z" },
      { id: "retry-1", content: "相同結果", created_at: "2026-09-15T00:00:03Z" },
    ]);
  });
});

describe("getSelectableProposalIds", () => {
  test("混合結果只預選 Ready／導師檢查操作，不會套用 unresolved candidate", () => {
    expect([...getSelectableProposalIds(
      [
        { id: "ready", operation: "update" },
        { id: "gap", operation: "update" },
      ],
      [
        { operation: { id: "ready" }, status: "ready" },
        { operation: { id: "gap" }, status: "needs_information" },
      ],
    )]).toEqual(["ready"]);
  });
});

describe("ChatPanel", () => {
  test("refine 內部提示詞不會出現在聊天室，並提供清除內容按鈕", () => {
    const html = renderToStaticMarkup(
      <ChatPanel
        messages={[
          { role: "user", content: RUBRIC_POLISH_PROMPT },
          { role: "assistant", content: "已完成潤飾，請確認提案。" },
        ]}
        onSendMessage={() => {}}
        onClearMessages={() => {}}
        isLoading={false}
        hasRubric
      />,
    );

    expect(html).not.toContain(RUBRIC_POLISH_PROMPT);
    expect(html).toContain("已完成潤飾，請確認提案。");
    expect(html).toContain("清除內容");
  });

  test("聊天室只保留對話相關操作，不提供整理或製作入口", () => {
    const html = renderToStaticMarkup(
      <ChatPanel
        messages={[]}
        onSendMessage={() => {}}
        onToggleSources={() => {}}
        isLoading={false}
        hasRubric
      />,
    );

    expect(html).not.toContain("AI一鍵整理");
    expect(html).not.toContain("製作腳本");
    expect(html).not.toContain("製作檢查腳本");
    expect(html).toContain("資料來源");
    expect(html).toContain('aria-controls="ai-chat-data-sources"');
    expect(html).toContain("描述想檢查的內容");
    expect(html).toContain("你同意套用後才會寫進檢查表");
    expect(html).not.toContain("檢查表來源");
    expect(html).not.toContain("自動檢測支援");
  });

  test("資料來源在聊天室內展開，不建立額外 dialog", () => {
    const html = renderToStaticMarkup(
      <ChatPanel
        messages={[]}
        onSendMessage={() => {}}
        onToggleSources={() => {}}
        sourcesOpen
        sourcesContent={<div>目前資料來源內容</div>}
        isLoading={false}
        hasRubric
      />,
    );

    expect(html).toContain('id="ai-chat-data-sources"');
    expect(html).toContain("目前資料來源內容");
    expect(html).toContain('aria-expanded="true"');
    expect(html).not.toContain('role="dialog"');
  });

  test("以加號提供聊天室附件入口並顯示待送出附件橫欄", () => {
    const html = renderToStaticMarkup(
      <ChatPanel
        messages={[]}
        onSendMessage={() => {}}
        onUploadFile={() => {}}
        pendingAttachments={[{
          id: "attachment-1",
          original_filename: "requirements.md",
          status: "ready",
        }]}
        isLoading={false}
      />,
    );

    expect(html).toContain('aria-label="新增附件"');
    expect(html).toContain("requirements.md");
    expect(html).toContain("已讀取");
  });

  describe("拖檔進對話區", () => {
    const dropHint = () => i18n.t("FileDropzone.dropToAdd", { ns: "common" });

    function mount(props) {
      Element.prototype.scrollIntoView = vi.fn();
      const container = document.createElement("div");
      document.body.appendChild(container);
      const root = createRoot(container);
      act(() => {
        root.render(<ChatPanel messages={[]} onSendMessage={() => {}} isLoading={false} {...props} />);
      });
      const drag = (type, files = []) => {
        const event = new Event(type, { bubbles: true, cancelable: true });
        Object.defineProperty(event, "dataTransfer", { value: { files, types: ["Files"] } });
        act(() => {
          container.firstElementChild.dispatchEvent(event);
        });
      };
      const cleanup = () => {
        act(() => root.unmount());
        container.remove();
      };
      return { container, drag, cleanup };
    }

    test("拖進來時浮出放置提示，放開後交給上傳，跟＋一樣一次一個", () => {
      const onUploadFile = vi.fn();
      const { container, drag, cleanup } = mount({ onUploadFile });

      drag("dragover");
      expect(container.textContent).toContain(dropHint());

      const first = new File(["a"], "requirements.md");
      drag("drop", [first, new File(["b"], "notes.md")]);
      expect(onUploadFile).toHaveBeenCalledTimes(1);
      expect(onUploadFile).toHaveBeenCalledWith(first);
      expect(container.textContent).not.toContain(dropHint());
      cleanup();
    });

    test("沒有上傳入口或正在上傳時，不浮出提示也不上傳", () => {
      const noUpload = mount({});
      noUpload.drag("dragover");
      expect(noUpload.container.textContent).not.toContain(dropHint());
      noUpload.cleanup();

      const onUploadFile = vi.fn();
      const busy = mount({ onUploadFile, isUploading: true });
      busy.drag("dragover");
      expect(busy.container.textContent).not.toContain(dropHint());
      busy.drag("drop", [new File(["a"], "requirements.md")]);
      expect(onUploadFile).not.toHaveBeenCalled();
      busy.cleanup();
    });
  });

  test("沒有檢查表時仍不提供任何腳本操作入口", () => {
    const html = renderToStaticMarkup(
      <ChatPanel
        messages={[]}
        onSendMessage={() => {}}
        isLoading={false}
      />,
    );

    expect(html).not.toContain("製作檢查腳本");
    expect(html).not.toContain("匯出 Excel");
  });

  test("腳本製作失敗會留下可辨識的頁面狀態，不只依賴 toast", () => {
    const html = renderToStaticMarkup(
      <ScriptGenerationNotice
        notice={{
          status: "error",
          message: "上游服務逾時。可再次按「製作腳本」重試。",
        }}
      />,
    );

    expect(html).toContain('role="alert"');
    expect(html).toContain('data-workflow-status="error"');
    expect(html).toContain("上游服務逾時");
    expect(html).toContain("再次按");
  });

  test("腳本製作中的橙色橫幅沿用旋轉圖示與忙碌狀態", () => {
    const html = renderToStaticMarkup(
      <ScriptGenerationNotice isCreatingScript status="generating" />,
    );

    expect(html).toContain("正在製作檢查腳本");
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('data-workflow-status="generating"');
    // 旋轉圖示走 MIcon 的 spin prop（全站統一的 micon-spin class）
    expect(html).toContain("micon-spin");
  });
  test("附件逐項核查期間顯示階段文案，不偽造進度百分比", () => {
    const html = renderToStaticMarkup(
      <ChatPanel
        messages={[]}
        onSendMessage={() => {}}
        isLoading
        loadingText="正在拆解評分表並逐項核查…"
        hasRubric
      />,
    );

    expect(html).toContain("正在拆解評分表並逐項核查");
    expect(html).not.toContain("%");
  });
});

describe("RubricsTab 製作腳本流程", () => {
  test.each([
    { title: "確認服務 Port", detectable: "partial", missing: "服務 Port", checkSteps: [] },
    {
      title: "收集 main.py 輸出",
      detectable: "auto",
      missing: "main.py 的完整路徑或工作目錄",
      checkSteps: [{ id: "output", title: "收集輸出", collector: { type: "command", argv: ["python3", "main.py"] } }],
    },
  ])("$title：server 核對缺少資訊時顯示路徑／範圍詢問，且不啟動腳本", async ({ title, detectable, missing, checkSteps }) => {
    Element.prototype.scrollIntoView = vi.fn();
    const file = {
      id: "file-1",
      template_key: "linux",
      environment_keys: ["linux"],
      analysis_revision: 3,
      source_type: "created",
      display_name: "測試檢查表",
      original_filename: null,
      updated_at: "2026-09-15T00:00:00Z",
      analysis_json: {
        items: [{
          id: "item-port",
          title,
          checked: false,
          detectable,
          judgement_mode: "ai",
          detection_method: "檢查服務",
          missing_information: detectable === "auto" ? [] : [missing],
          check_steps: checkSteps,
          fallback: null,
        }],
        total_items: 1,
        checked_count: 0,
        auto_count: detectable === "auto" ? 1 : 0,
        partial_count: detectable === "partial" ? 1 : 0,
        manual_count: 0,
      },
    };
    const assistantMessage = {
      id: "assistant-1",
      session_id: "session-1",
      role: "assistant",
      message_type: "chat",
      content: `重新核對後，「${title}」已確認檢查目標，但還缺少：${missing}。請補充${missing}。`,
      metadata_json: {
        status: "needs_information",
        stage: "reanalysis",
        script_ready: false,
        item_results: [{
          item_id: "item-port",
          title,
          status: "needs_information",
          missing_information: [missing],
        }],
      },
      created_at: "2026-09-15T00:00:02Z",
    };
    vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([file]);
    vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
    const sendMessage = vi.spyOn(AiJudgeService, "sendSessionMessage").mockResolvedValue({
      user_message: {
        id: "user-1",
        session_id: "session-1",
        role: "user",
        message_type: "chat",
        content: RUBRIC_POLISH_PROMPT,
        metadata_json: { ui_hidden: true },
        created_at: "2026-09-15T00:00:01Z",
      },
      assistant_message: assistantMessage,
      rubric_proposal: [],
      base_revision: 3,
    });
    const createScript = vi.spyOn(AiJudgeService, "createSessionScriptSet").mockResolvedValue({
      status: "approved",
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(
        <ConfirmProvider>
          <RubricsTab
            classId="class-1"
            judgeSession={{ id: "session-1", selected_file_id: "file-1" }}
          />
        </ConfirmProvider>,
        queryClient,
      ));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const saveButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("製作腳本"));
    expect(saveButton).toBeTruthy();
    expect(saveButton.disabled).toBe(false);
    await act(async () => {
      saveButton.click();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(sendMessage).toHaveBeenCalledWith(
      "class-1",
      "session-1",
      RUBRIC_POLISH_PROMPT,
      3,
      { isRefine: true },
    );
    expect(container.textContent).toContain(title);
    expect(container.textContent).toContain(assistantMessage.content);
    expect(createScript).not.toHaveBeenCalled();
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("重新核對全部不可套用時不顯示提案面板，只保留 Chat 回覆", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const file = {
      id: "file-1",
      template_key: "linux",
      environment_keys: ["linux"],
      analysis_revision: 3,
      source_type: "created",
      display_name: "測試檢查表",
      original_filename: null,
      updated_at: "2026-09-15T00:00:00Z",
      analysis_json: {
        items: [{
          id: "item-port",
          title: "確認服務 Port",
          checked: false,
          detectable: "partial",
          judgement_mode: "ai",
          detection_method: "檢查服務",
          missing_information: ["服務 Port"],
          check_steps: [],
          fallback: null,
        }],
        total_items: 1,
        checked_count: 0,
        auto_count: 0,
        partial_count: 1,
        manual_count: 0,
      },
    };
    const assistantMessage = {
      id: "assistant-1",
      session_id: "session-1",
      role: "assistant",
      message_type: "chat",
      content: "重新核對後，「確認服務 Port」已確認檢查目標，但還缺少：服務 Port。",
      metadata_json: {
        status: "needs_information",
        stage: "reanalysis",
        script_ready: false,
        item_results: [{
          item_id: "item-port",
          title: "確認服務 Port",
          status: "needs_information",
          missing_information: ["服務 Port"],
        }],
      },
      created_at: "2026-09-15T00:00:02Z",
    };
    vi.spyOn(AiJudgeService, "listFiles").mockResolvedValue([file]);
    vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
    vi.spyOn(AiJudgeService, "sendSessionMessage").mockResolvedValue({
      user_message: {
        id: "user-1",
        session_id: "session-1",
        role: "user",
        message_type: "chat",
        content: RUBRIC_POLISH_PROMPT,
        metadata_json: { ui_hidden: true },
        created_at: "2026-09-15T00:00:01Z",
      },
      assistant_message: assistantMessage,
      rubric_proposal: [{
        id: "item-port",
        title: "確認服務 Port",
        checked: false,
        detectable: "partial",
        judgement_mode: "ai",
        detection_method: "檢查服務 Port",
        missing_information: ["服務 Port"],
        check_steps: [],
        fallback: null,
        operation: "update",
      }],
      base_revision: 3,
    });
    const createScript = vi.spyOn(AiJudgeService, "createSessionScriptSet").mockResolvedValue({
      status: "approved",
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(
        <ConfirmProvider>
          <RubricsTab
            classId="class-1"
            judgeSession={{ id: "session-1", selected_file_id: "file-1" }}
          />
        </ConfirmProvider>,
        queryClient,
      ));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const saveButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("製作腳本"));
    expect(saveButton).toBeTruthy();
    await act(async () => {
      saveButton.click();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(container.textContent).not.toContain("AI 核對提案");
    expect(container.querySelector('[aria-label="AI 提案"]')).toBeNull();
    expect(container.textContent).toContain("重新核對後，「確認服務 Port」已確認檢查目標，但還缺少：服務 Port。");
    expect(createScript).not.toHaveBeenCalled();
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

});

describe("ProposalPanel", () => {
  test("以可展開透明預覽呈現可套用操作與正式套用文案", () => {
    const html = renderToStaticMarkup(
      <ProposalPanel
        proposal={[
          { id: "item-add", title: "main.py 輸出檢查", operation: "add" },
          { id: "item-update", title: "資料庫健康檢查", operation: "update" },
          { id: "item-delete", title: "舊版 Port 檢查", operation: "delete" },
        ]}
        selectedIds={new Set(["item-add", "item-update", "item-delete"])}
        onToggle={() => {}}
        onApply={() => {}}
        onSkip={() => {}}
        disabled={false}
      />,
    );

    expect(html).toContain('aria-label="AI 提案"');
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("可套用 3");
    expect(html).toContain("新增");
    expect(html).toContain("修改");
    expect(html).toContain("刪除");
    expect(html).toContain("忽略");
    expect(html).toContain("同意套用");
    expect(html).not.toContain("略過");
    expect(html).not.toContain("套用選取");
  });

  test("附件逐項結果依來源順序顯示，不可套用項目不提供勾選框", () => {
    const itemResults = [
      {
        source_index: 1,
        source_label: "第 1 列",
        title: "確認 Python 版本",
        status: "ready",
        operation: { id: "item-attachment-1", operation: "add", title: "確認 Python 版本" },
        missing_information: [],
        detail: "",
      },
      {
        source_index: 2,
        source_label: "第 2 列",
        title: "檢查 Port 8080",
        status: "needs_information",
        operation: null,
        missing_information: ["要檢查的服務或連接埠範圍"],
        detail: "",
      },
      {
        source_index: 3,
        source_label: "第 3 列",
        title: "程式架構品質",
        status: "teacher_review",
        operation: { id: "item-attachment-3", operation: "add", title: "程式架構品質" },
        missing_information: [],
        detail: "",
      },
    ];

    const html = renderToStaticMarkup(
      <ProposalPanel
        proposal={[
          { id: "item-attachment-1", operation: "add", title: "確認 Python 版本" },
          { id: "item-attachment-3", operation: "add", title: "程式架構品質" },
        ]}
        selectedIds={new Set(["item-attachment-1", "item-attachment-3"])}
        onToggle={() => {}}
        onApply={() => {}}
        onSkip={() => {}}
        disabled={false}
        itemResults={itemResults}
      />,
    );

    expect(html.indexOf("第 1 列")).toBeLessThan(html.indexOf("第 2 列"));
    expect(html.indexOf("第 2 列")).toBeLessThan(html.indexOf("第 3 列"));
    expect(html).toContain("缺少資訊");
    expect(html).toContain("要檢查的服務或連接埠範圍");
    expect(html.match(/type="checkbox"/g)).toHaveLength(2);
    expect(html).toContain("同意套用");
  });
});

describe("SaveAndCreateAction", () => {
  test("顯示於檢查表操作區並說明全綠後才製作", () => {
    const html = renderToStaticMarkup(<SaveAndCreateAction onClick={() => {}} />);

    expect(html).toContain("製作腳本");
    expect(html).toContain("通過後自動保存核對結果、製作腳本，並前往導師核查");
    expect(html).toContain("save");
  });

  test("核對期間停用按鈕並提供可辨識的忙碌狀態", () => {
    const html = renderToStaticMarkup(
      <SaveAndCreateAction onClick={() => {}} isProcessing status="reviewing" />,
    );

    expect(html).toContain("核對項目中…");
    expect(html).toContain("disabled");
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('data-generation-status="reviewing"');
  });

  test("有待處理提案時外觀停用但仍可聚焦，滑過顯示原因", () => {
    const html = renderToStaticMarkup(
      <SaveAndCreateAction onClick={() => {}} blocker="請先套用目前提案" />,
    );

    expect(html).toContain('aria-disabled="true"');
    expect(html).toContain('title="請先套用目前提案"');
    expect(html).not.toMatch(/<button[^>]*\sdisabled=""/);
  });

  test("被擋住時點擊只回報原因、不會開始製作；條件滿足後才真的執行", () => {
    const onClick = vi.fn();
    const onBlocked = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const reason = "請先在聊天室請 AI 產生至少一個檢查項目";

    act(() => {
      root.render(<SaveAndCreateAction onClick={onClick} onBlocked={onBlocked} blocker={reason} />);
    });
    act(() => container.querySelector("button").click());
    expect(onBlocked).toHaveBeenCalledWith(reason);
    expect(onClick).not.toHaveBeenCalled();

    act(() => {
      root.render(<SaveAndCreateAction onClick={onClick} onBlocked={onBlocked} />);
    });
    act(() => container.querySelector("button").click());
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onBlocked).toHaveBeenCalledTimes(1);

    act(() => root.unmount());
    container.remove();
  });
});

describe("RubricTable", () => {
  const items = [
    {
      id: "python-version",
      title: "Python 版本檢查",
      detectable: "auto",
      judgement_mode: "ai",
      detection_method: "執行 python --version",
      fallback: "無法執行時由老師確認",
      check_steps: [{ template_key: "python", command_key: "python_version", command_label: "Python 版本" }],
    },
    {
      id: "response-quality",
      title: "回傳內容品質",
      detectable: "partial",
      detection_method: null,
      fallback: "請補充實際輸出格式",
      missing_information: ["預期輸出格式"],
      check_steps: [],
    },
    {
      id: "teacher-review",
      title: "程式架構品質",
      detectable: "auto",
      judgement_mode: "teacher",
      detection_method: "讀取 main.py 內容",
      check_steps: [{ template_key: "linux", command_key: "system.run_command" }],
    },
    {
      id: "manual-review",
      title: "主觀設計品質",
      detectable: "manual",
      detection_method: null,
      fallback: null,
      check_steps: [],
    },
  ];

  test("以最左側圖示提供檢查設定入口", () => {
    const html = renderToStaticMarkup(
      <RubricTable items={items} onChange={() => {}} onDelete={() => {}} />,
    );

    expect(html).toContain("檢查點");
    expect(html).toContain("檢測方式");
    expect(html).not.toContain("檢查條件");
    expect(html).toContain("自動檢測支援");
    expect(html).toContain('>Python 版本檢查</textarea>');
    expect(html).toContain("可以");
    expect(html).toContain("缺少資訊");
    expect(html).toContain("導師檢查");
    expect(html).toContain("導師核查／無法執行");
    expect(html).toContain("check_circle");
    expect(html).toContain("warning_amber");
    // 導師檢查是正常判定方式：藍色＋人像圖示，不跟「無法執行」共用紅色叉叉
    expect(html.match(/cancel/g)).toHaveLength(1);
    expect(html.match(/detBadge_manual/g)).toHaveLength(1);
    expect(html).toContain("detBadge_teacher");
    expect(html).not.toContain("可執行取證");
    expect(html).not.toContain("導師人工審核");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('aria-label="展開第 1 項檢查設定"');
    expect(html.indexOf('aria-label="展開第 1 項檢查設定"')).toBeLessThan(html.indexOf('>Python 版本檢查</textarea>'));
    // 序號跟展開鈕在同一格，排在按鈕前面
    expect(html.indexOf(">1</span>")).toBeGreaterThan(-1);
    expect(html.indexOf(">1</span>")).toBeLessThan(html.indexOf('aria-label="展開第 1 項檢查設定"'));
    expect(html).not.toContain(">詳細</button>");
    expect(html).toContain("執行 python --version");
    expect(html).not.toContain('placeholder="寫下學生需要符合的條件"');
    expect(html).not.toContain("AI 偵測判斷（僅由 AI 更新）");
  });

  test("只在異動的檢查項目列標示待更新", () => {
    const html = renderToStaticMarkup(
      <RubricTable
        items={items}
        needsReviewIds={new Set(["python-version"])}
        onChange={() => {}}
        onDelete={() => {}}
      />,
    );

    expect(html).toContain("待更新");
    expect(html).toContain('title="缺少資訊（自動檢測支援待更新）"');
    expect(html).toContain("warning_amber");
  });

  test("缺少資訊的導師核查項目仍優先顯示缺少資訊", () => {
    const html = renderToStaticMarkup(
      <RubricTable
        items={[{
          id: "partial-teacher",
          title: "程式風格檢查",
          detectable: "partial",
          judgement_mode: "teacher",
          detection_method: null,
          fallback: null,
          missing_information: ["預期輸出格式"],
          check_steps: [],
        }]}
        onChange={() => {}}
        onDelete={() => {}}
      />,
    );

    expect(html).toContain("缺少資訊");
    expect(html).not.toContain(">導師檢查<");
  });

  test("收合時顯示 typed collector 的檢測目標，不暴露 peer key", () => {
    const html = renderToStaticMarkup(
      <RubricTable
        items={[
          {
            id: "file-text",
            title: "讀取程式碼",
            detectable: "auto",
            detection_method: "讀取 main.py",
            check_steps: [{
              id: "read-main",
              title: "讀取 main.py",
              collector: { type: "file_text", path: "/home/student/main.py", read_mode: "head", lines: 200 },
            }],
          },
          {
            id: "command",
            title: "執行程式",
            detectable: "auto",
            detection_method: "執行入口程式",
            check_steps: [{
              id: "run-main",
              title: "執行 main.py",
              collector: { type: "command", argv: ["python3", "/home/student/project/main.py"], timeout_seconds: 30 },
            }],
          },
          {
            id: "http",
            title: "檢查服務",
            detectable: "auto",
            detection_method: "檢查本機服務",
            check_steps: [{
              id: "health",
              title: "健康檢查",
              collector: { type: "localhost_http", url: "http://localhost:3000/health", method: "GET", timeout_seconds: 10, max_chars: 1200 },
            }],
          },
          {
            id: "peer",
            title: "檢查觀察節點",
            detectable: "auto",
            detection_method: "確認節點連通性",
            peer_node_key: "peer-secret-key",
            check_steps: [{
              id: "ping",
              title: "連通性",
              collector: { type: "peer_ping", timeout_seconds: 10 },
            }],
          },
        ]}
        onChange={() => {}}
        onDelete={() => {}}
      />,
    );

    expect(html.match(/rubricTargetSummary/g)).toHaveLength(4);
    expect(html).toContain("檔案內容");
    expect(html).toContain("/home/student/main.py");
    expect(html).toContain("python3 /home/student/project/main.py");
    expect(html).toContain("http://localhost:3000/health");
    expect(html).toContain("由執行節點觀察");
    expect(html).not.toContain("peer-secret-key");
  });

  test("檔案項目顯示絕對路徑，版本項目不需要檔案位置", () => {
    const renderItem = (collector) => renderToStaticMarkup(
      <RubricTable
        items={[{
          id: "located-item", title: "檢查項目", detectable: "auto",
          judgement_mode: "teacher", detection_method: "收集內容",
          check_steps: [{ id: "collect", title: "收集", collector }],
        }]}
        onChange={() => {}}
        onDelete={() => {}}
      />,
    );
    const fileHtml = renderItem({ type: "file_text", path: "/srv/student project/.env" });
    expect(fileHtml).not.toContain("工作目錄");
    expect(fileHtml).toContain("/srv/student project/.env");
    const versionHtml = renderItem({ type: "command", argv: ["python3", "--version"] });
    expect(versionHtml).toContain("python3 --version");
    expect(versionHtml).not.toContain("工作目錄");
    expect(versionHtml).not.toContain("缺少資訊");
  });

  test("展開後顯示執行契約、判定模式與 assertion", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <RubricTable
          items={[{
            id: "typed-command",
            title: "執行並判定",
            detectable: "auto",
            judgement_mode: "ai",
            detection_method: "執行程式並檢查回傳碼",
            target_node_key: "n8n",
            check_steps: [{
              id: "run",
              title: "執行 main.py",
              collector: { type: "command", argv: ["python3", "/home/student/project/main.py"], timeout_seconds: 30 },
              assertion: { type: "returncode_equals", expected: 0 },
            }],
          }]}
          onChange={() => {}}
          onDelete={() => {}}
          machineNodes={[{
            node_key: "n8n",
            name: "n8n",
            role: "workflow",
            resource_type: "lxc",
            sort_order: 0,
            template_name: "Ubuntu 24.04",
          }]}
        />,
      );
    });

    const toggle = container.querySelector('button[aria-label="展開第 1 項檢查設定"]');
    expect(toggle).toBeTruthy();
    await act(async () => {
      toggle.click();
    });

    expect(container.textContent).toContain("執行契約");
    expect(container.textContent).toContain("執行節點");
    expect(container.textContent).toContain("n8n（P1）");
    expect(container.textContent).toContain("角色：workflow · 類型：LXC · 映像：Ubuntu 24.04");
    expect(container.textContent).toContain("系統判定");
    expect(container.textContent).toContain("預計檢查步驟（尚未執行）");
    expect(container.textContent).toContain("回傳碼 = 0");
    expect(container.textContent).toContain("30 秒");

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});

describe("getScriptCreationBlocker", () => {
  const completeItem = {
    id: "python-run",
    title: "執行 main.py",
    detectable: "auto",
    detection_method: "依 exit code 與 stdout 判定",
    check_steps: [{
      id: "python.output",
      title: "執行 main.py",
      collector: {
        type: "command",
        argv: ["python3", "/home/student/project/main.py"],
        timeout_seconds: 30,
      },
      assertion: { type: "returncode_equals", expected: 0 },
    }],
  };

  test("所有項目都可以執行時允許製作腳本", () => {
    expect(getScriptCreationBlocker({ analysis: { items: [completeItem] } })).toBeNull();
  });

  test("後端導師判定模式不受客觀答案攔截", () => {
    const teacherReviewItem = {
      ...completeItem,
      judgement_mode: "teacher",
      check_steps: [{
        ...completeItem.check_steps[0],
        assertion: undefined,
      }],
    };

    expect(getScriptCreationBlocker({ analysis: { items: [teacherReviewItem] } })).toBeNull();
  });

  test.each([
    { argv: ["python3", "main.py"], cwd: "/home/student/project" },
    { ...completeItem.check_steps[0], collector: { type: "file_text", path: "main.py", cwd: "/home/student/project" } },
  ])("舊步驟提示重新核對，不推算已可執行", (step) => {
    const analysis = { items: [{ ...completeItem, check_steps: [step] }] };
    expect(getScriptCreationBlocker({ analysis })).toContain("待更新");
  });

  test("typed auto + teacher 項目可直接製作腳本", () => {
    const typedTeacherItem = {
      ...completeItem,
      judgement_mode: "teacher",
      check_steps: [{
        id: "read-main",
        title: "讀取 main.py",
        collector: {
          type: "file_text",
          path: "/home/student/main.py",
          encoding: "utf-8",
          read_mode: "head",
          lines: 200,
        },
      }],
    };

    expect(getScriptCreationBlocker({ analysis: { items: [typedTeacherItem] } })).toBeNull();
  });

  test("缺少資訊或需要人工審核時阻擋整份腳本", () => {
    const blocker = getScriptCreationBlocker({
      analysis: {
        items: [
          { ...completeItem, id: "missing", detectable: "partial" },
          { ...completeItem, id: "manual", detectable: "manual" },
        ],
      },
    });

    expect(blocker).toContain("1 項缺少資訊");
    expect(blocker).toContain("1 項需要導師核查或無法執行");
  });

  test("異動後尚未重新確認時阻擋腳本", () => {
    expect(getScriptCreationBlocker({
      analysis: { items: [completeItem], detectability_needs_review: true },
    })).toContain("待更新");
  });

  test("待重新確認項目會從檢查表狀態還原，讓提示與列標籤一致", () => {
    const analysis = {
      items: [completeItem],
      detectability_needs_review: true,
    };
    expect([...getRubricReviewItemIds(analysis)]).toEqual(["python-run"]);

    const html = renderToStaticMarkup(
      <RubricTable
        items={analysis.items}
        needsReviewIds={getRubricReviewItemIds(analysis)}
        onChange={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(html).toContain("待更新");
    expect(html).toContain('title="缺少資訊（自動檢測支援待更新）"');
  });

  test("保存的待更新項目只標示對應列，不讓整張表變成待確認", () => {
    const secondItem = { ...completeItem, id: "second-item", title: "第二項" };
    const analysis = {
      items: [completeItem, secondItem],
      detectability_needs_review: true,
      pending_review_item_ids: ["python-run"],
    };

    const html = renderToStaticMarkup(
      <RubricTable
        items={analysis.items}
        needsReviewIds={getRubricReviewItemIds(analysis)}
        onChange={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(html.match(/自動檢測支援待更新/g)).toHaveLength(1);
  });
});

describe("rubric item change detection", () => {
  test("相同項目內容不視為異動，實際欄位變更才產生不同快照", () => {
    const saved = {
      items: [{ id: "item-1", title: "檢查版本", detection_method: "執行版本檢查", detectable: "auto" }],
      detectability_needs_review: false,
    };
    const same = { ...saved, detectability_needs_review: true };
    const renamed = {
      ...saved,
      items: [{ ...saved.items[0], title: "檢查 Python 版本" }],
    };
    const changed = {
      ...saved,
      items: [{ ...saved.items[0], detection_method: "執行 python3 --version" }],
    };

    expect(getRubricItemsValue(same)).toBe(getRubricItemsValue(saved));
    // 只改檢查點名稱不影響自動檢測支援
    expect(getRubricItemsValue(renamed)).toBe(getRubricItemsValue(saved));
    expect(getRubricItemsValue(changed)).not.toBe(getRubricItemsValue(saved));
  });

  test("只回傳實際變動的項目 ID，不把整張表標成待更新", () => {
    const savedItems = [
      { id: "item-1", title: "檢查版本", detection_method: "執行版本檢查", detectable: "auto" },
      { id: "item-2", title: "檢查輸出", detection_method: "讀取輸出內容", detectable: "partial" },
    ];
    const currentItems = [
      { ...savedItems[0], detection_method: "執行 python3 --version" },
      savedItems[1],
    ];

    expect([...getPendingRubricItemIds(currentItems, savedItems)]).toEqual(["item-1"]);
    expect([...getPendingRubricItemIds([savedItems[1]], savedItems)]).toEqual([]);
  });

  test("只改檢查點名稱不會標成待更新", () => {
    const savedItems = [
      { id: "item-1", title: "檢查版本", detection_method: "執行版本檢查", detectable: "auto" },
    ];
    expect([...getPendingRubricItemIds([{ ...savedItems[0], title: "檢查 Python 版本" }], savedItems)]).toEqual([]);
  });
});

describe("detectability review state", () => {
  test("待確認清單為空時不得保存整表待更新旗標，避免未編輯項目全變待更新", () => {
    expect(resolveDetectabilityNeedsReview({
      requested: true,
      reviewItemIds: new Set(),
      hasActualChange: true,
      lastSavedNeedsReview: false,
    })).toBe(false);

    expect(resolveDetectabilityNeedsReview({
      requested: true,
      reviewItemIds: new Set(["item-2"]),
      hasActualChange: true,
      lastSavedNeedsReview: false,
    })).toBe(true);

    expect(resolveDetectabilityNeedsReview({
      requested: true,
      reviewItemIds: new Set(),
      hasActualChange: false,
      lastSavedNeedsReview: true,
    })).toBe(false);

    expect(resolveDetectabilityNeedsReview({
      requested: false,
      reviewItemIds: new Set(["item-2"]),
      hasActualChange: true,
    })).toBe(false);
  });

  test("未指定意圖時沿用分析結果既有旗標", () => {
    expect(resolveDetectabilityNeedsReview({
      requested: null,
      reviewItemIds: new Set(["item-1"]),
      fallbackNeedsReview: true,
    })).toBe(true);
    expect(resolveDetectabilityNeedsReview({
      requested: null,
      reviewItemIds: new Set(),
      fallbackNeedsReview: false,
    })).toBe(false);
  });

  test("刪除單一項目不會把其他未編輯項目算進待確認清單", () => {
    const savedItems = [
      { id: "item-1", title: "檢查版本", detection_method: "執行版本檢查", detectable: "auto" },
      { id: "item-2", title: "檢查輸出", detection_method: "讀取輸出內容", detectable: "auto" },
    ];
    const nextItems = [savedItems[1]];

    expect([...getPendingRubricItemIds(nextItems, savedItems)]).toEqual([]);
  });

  test("套用提案期間尚未保存完成的內容，會以排程中的分析為基準，不把 AI 套用結果誤判成待更新", () => {
    const savedItems = [
      { id: "item-1", title: "檢查版本", detection_method: "執行版本檢查", detectable: "auto" },
      { id: "item-2", title: "檢查輸出", detection_method: "讀取輸出內容", detectable: "auto" },
    ];
    // AI 提案已套用 item-1（尚未保存完成），使用者此時編輯 item-2
    const pendingSaveAnalysis = {
      items: [
        { ...savedItems[0], detection_method: "執行版本檢查（AI 補充）" },
        savedItems[1],
      ],
      detectability_needs_review: false,
      pending_review_item_ids: [],
    };
    const nextItems = [
      pendingSaveAnalysis.items[0],
      { ...savedItems[1], detection_method: "讀取輸出內容（教師微調）" },
    ];

    expect([...getPendingRubricItemIds(
      nextItems,
      savedItems,
      [],
      false,
      pendingSaveAnalysis,
    )]).toEqual(["item-2"]);
  });
});

describe("buildProposalDiff", () => {
  test("機器執行與 peer 身分變更會形成可套用差異", () => {
    const diff = buildProposalDiff(
      [{
        id: "network-check",
        title: "檢查網路",
        target_node_key: "web",
        peer_node_key: "db",
        detectable: "auto",
      }],
      [{
        id: "network-check",
        title: "檢查網路",
        target_node_key: "db",
        peer_node_key: "web",
        detectable: "auto",
      }],
    );

    expect(diff).toMatchObject([{
      id: "network-check",
      operation: "update",
      target_node_key: "db",
      peer_node_key: "web",
    }]);
  });

  test("將 AI 修改轉成可確認差異，且未回傳項目不會被默認刪除", () => {
    const current = [
      { id: "keep", title: "保留", detection_method: "原檢測方式", detectable: "manual" },
      { id: "remove", title: "移除", detection_method: "舊檢測方式", detectable: "manual" },
    ];
    const diff = buildProposalDiff(current, [
      { id: "keep", title: "保留", detection_method: "新檢測方式", detectable: "manual" },
      { id: "new", title: "新增", detection_method: "新檢測方式", detectable: "auto" },
      { id: "remove", operation: "delete", title: "移除" },
    ]);

    expect(diff.map((item) => [item.id, item.operation])).toEqual([
      ["keep", "update"],
      ["new", "add"],
      ["remove", "delete"],
    ]);
  });

  test("候選檢查表只套用選定差異並保留未提及項目", () => {
    const result = applyProposalOperations(
      [
        { id: "keep", title: "保留", detection_method: "原檢測方式" },
        { id: "remove", title: "移除", detection_method: "舊檢測方式" },
      ],
      [
        { id: "keep", title: "保留", detection_method: "新檢測方式", operation: "update" },
        { id: "remove", operation: "delete" },
      ],
      new Set(["keep"]),
    );

    expect(result.items).toEqual([
      { id: "keep", title: "保留", detection_method: "新檢測方式" },
      { id: "remove", title: "移除", detection_method: "舊檢測方式" },
    ]);
    expect([...result.evaluatedIds]).toEqual(["keep"]);
  });
});

describe("proposalToolCallLines", () => {
  test("以工具實際結果顯示建立／修改提案狀態，read 事件不顯示", () => {
    const message = {
      metadata_json: {
        tool_calls: [
          { tool: "list_checklist", status: "read", item_count: 2 },
          {
            tool: "create_checklist_item",
            status: "staged",
            operation: "add",
            title: "檢查 Python 版本",
          },
          {
            tool: "edit_checklist_item",
            status: "staged",
            operation: "update",
            title: "既有 Port 檢查",
          },
          {
            tool: "edit_checklist_item",
            status: "rejected",
            title: "未讀取項目",
          },
        ],
      },
    };

    expect(proposalToolCallLines(message)).toEqual([
      { icon: "check_circle", text: "已建立提案：檢查 Python 版本" },
      { icon: "check_circle", text: "已送出修改提案：既有 Port 檢查" },
      { icon: "cancel", text: "提案未建立：未讀取項目" },
    ]);
  });

  test("沒有 tool_calls metadata 時回傳空陣列", () => {
    expect(proposalToolCallLines({ metadata_json: {} })).toEqual([]);
    expect(proposalToolCallLines(null)).toEqual([]);
  });

  test("格式錯誤顯示實際失敗，已修正的舊錯誤不再顯示", () => {
    expect(proposalToolCallLines({ metadata_json: { tool_calls: [
      { status: "error", title: "Python 版本", resolved: true },
      { status: "staged", operation: "update", title: "Python 版本" },
      { status: "error", title: "套件檢查" },
      { status: "error", title: "套件檢查" },
    ] } })).toEqual([
      { icon: "check_circle", text: "已送出修改提案：Python 版本" },
      { icon: "cancel", text: "提案未建立：套件檢查" },
    ]);
  });
});

describe("uploaded rubric naming", () => {
  test("匯入檔名移除副檔名", () => {
    expect(getRubricDisplayName({ name: "AI導師檢查表審核系統_Python服務Running狀態檢測_簡短版.docx" }))
      .toBe("AI導師檢查表審核系統_Python服務Running狀態檢測_簡短版");
    expect(getRubricDisplayName({ display_name: "自訂檢查表", original_filename: "保存的檢查表.docx" })).toBe("自訂檢查表");
    expect(getRubricDisplayName({ name: "  " }, "未命名檢查表")).toBe("未命名檢查表");
  });
});

describe("session menu positioning", () => {
  test("浮動選單會貼近觸發按鈕並限制在視窗內", () => {
    expect(getSessionMenuPosition(
      { top: 160, right: 780, bottom: 196 },
      { width: 800, height: 600, menuWidth: 220, menuHeight: 280, margin: 12 },
    )).toEqual({ top: 208, left: 560 });

    expect(getSessionMenuPosition(
      { top: 520, right: 790, bottom: 556 },
      { width: 800, height: 600, menuWidth: 220, menuHeight: 280, margin: 12 },
    )).toEqual({ top: 228, left: 568 });
  });
});

describe("SessionTitle", () => {
  test("保留完整名稱作為 tooltip，並將可視區與文字分開以支援截斷動畫", () => {
    const title = "這是一個很長的 AI導師檢查 session 名稱";
    const html = renderToStaticMarkup(<SessionTitle title={title}>{title}</SessionTitle>);

    expect(html).toContain('title="這是一個很長的 AI導師檢查 session 名稱"');
    expect(html).toContain(title);
  });
});

describe("getDefaultWeekId", () => {
  const weeks = [
    { id: "w3", week_number: 3, session_date: "2026-09-21" },
    { id: "w1", week_number: 1, session_date: "2026-09-07" },
    { id: "w2", week_number: 2, session_date: "2026-09-14" },
  ];
  test("依週次編號預設第一週，不改動來源順序", () => {
    expect(getDefaultWeekId(weeks)).toBe("w1");
    expect(weeks.map((week) => week.id)).toEqual(["w3", "w1", "w2"]);
  });
  test("沒有第一週時選編號最小的現有週次", () => {
    expect(getDefaultWeekId([weeks[0], weeks[2]])).toBe("w2");
  });
  test("沒有週次時不指定", () => {
    expect(getDefaultWeekId([])).toBe("");
  });
});

describe("resolveActiveWeekId", () => {
  const weeks = [
    { id: "w3", week_number: 3, session_date: "2026-09-21" },
    { id: "w1", week_number: 1, session_date: "2026-09-07" },
    { id: "w2", week_number: 2, session_date: "2026-09-14" },
  ];

  test("網址指定的週次優先，不存在的週次不採用", () => {
    expect(resolveActiveWeekId("w3", weeks)).toBe("w3");
    expect(resolveActiveWeekId("missing", weeks)).toBe("w1");
  });

  test.each(["2026-09-01", "2026-09-16", "2026-09-21", "2026-10-16"])("日期為 %s 時，沒有指定週次仍選第一週", (date) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    try {
      vi.setSystemTime(new Date(`${date}T12:00:00`));
      expect(resolveActiveWeekId(null, weeks)).toBe("w1");
    } finally {
      vi.useRealTimers();
    }
  });

  test("週次 ID 統一為字串，網址可以指定數字 ID", () => {
    const numericWeeks = [{ id: 20, week_number: 2 }, { id: 10, week_number: 1 }];
    expect(resolveActiveWeekId("20", numericWeeks)).toBe("20");
    expect(resolveActiveWeekId(null, numericWeeks)).toBe("10");
  });

  test("沒有週次時不選", () => {
    expect(resolveActiveWeekId(null, [])).toBeNull();
  });
});

describe("weekOptionLabel", () => {
  test("週次名稱帶主題，沒填主題時註明", () => {
    expect(weekOptionLabel({ week_number: 3, title: "網站部署" })).toBe("第 3 週 · 網站部署");
    expect(weekOptionLabel({ week_number: 4, title: " " })).toBe("第 4 週 · 未設定主題");
  });
});

describe("script creation workflow", () => {
  test("通過自動檢查後進入導師核查，其餘留在任務設定", () => {
    expect(getScriptCreationDestination({ status: "approved" })).toBe("review");
    expect(getScriptCreationDestination({ status: "review_failed", id: "script-1" })).toBe("rubrics");
  });
});

describe("teacher review summary", () => {
  test("非零 returncode 即使沒有輸出也顯示 argv 與無輸出提示", () => {
    const markup = renderToStaticMarkup(
      <CommandLog
        raw={JSON.stringify({
          argv: ["pgrep", "-f", "n8n"],
          timeout_seconds: 10,
          stdout: "",
          stderr: "",
          returncode: 1,
          error_code: "unexpected_returncode",
        })}
      />,
    );

    expect(markup).toContain("[&quot;pgrep&quot;,&quot;-f&quot;,&quot;n8n&quot;]");
    expect(markup).not.toContain("工作目錄");
    expect(markup).toContain("指令沒有 stdout/stderr 輸出");
    expect(markup).toContain("結束代碼 1");
  });

  test("將舊版 command_exception 與非零 returncode 顯示成老師可讀摘要，並保留 raw log", () => {
    const exceptionRaw = JSON.stringify({
      error_code: "command_exception",
      error: "[Errno 2] No such file or directory: 'null'",
    });
    const nonzeroRaw = JSON.stringify({
      stdout: "",
      stderr: "cat: /home/owo/main.log: No such file or directory\n",
      returncode: 1,
    });

    expect(getCheckResultSummary({ status: "unknown", raw: exceptionRaw })).toBe(
      "指令無法執行：[Errno 2] No such file or directory: 'null'",
    );
    expect(getCheckResultSummary({ status: "unknown", raw: nonzeroRaw })).toBe(
      "指令執行失敗（結束代碼 1）：cat: /home/owo/main.log: No such file or directory",
    );
    expect(JSON.parse(nonzeroRaw)).toMatchObject({
      stderr: "cat: /home/owo/main.log: No such file or directory\n",
      returncode: 1,
    });
  });

  test("把 warning、unknown 與 collected 視為待導師核查", () => {
    const target = {
      status: "completed",
      validation: { valid: true },
      parsed_result: {
        checks: [
          { id: "auto-pass", status: "pass" },
          { id: "manual", status: "unknown" },
          { id: "risk", status: "warning" },
          { id: "evidence", status: "collected" },
        ],
      },
    };

    expect(getTargetReviewSummary(target)).toMatchObject({
      kind: "pending",
      pending: 3,
      reviewable: 3,
    });
    target.teacher_review = { decisions: { manual: "pass", risk: "fail", evidence: "pass" } };
    expect(getTargetReviewSummary(target)).toMatchObject({
      kind: "reviewed",
      pending: 0,
      reviewable: 3,
    });
  });

  test("沒有結果與執行失敗會清楚分開", () => {
    expect(getTargetReviewSummary(null).kind).toBe("missing");
    expect(getTargetReviewSummary({ status: "failed" }).kind).toBe("failed");
  });

  test("只有明確的關機原因顯示關機，舊紀錄與 SSH 錯誤維持一般失敗", () => {
    expect(getTargetReviewSummary({ status: "failed", reason_code: "vm_powered_off" }).label).toBe("錯誤：關機");
    expect(getTargetReviewSummary({ status: "failed", reason_code: "vm_status_unavailable" }).label).toBe("錯誤：狀態不明");
    for (const reason_code of ["not_running", "executor_error", "execution_timeout", "target_not_ready"]) {
      expect(getTargetReviewSummary({ status: "failed", reason_code }).label).toBe("執行失敗");
    }
  });
});

describe("teacher review run-once（整組檢查點）", () => {
  // 全班回報與較舊批次預設都沒有；需要的測試再自己覆寫
  beforeEach(() => {
    vi.spyOn(AiJudgeService, "getSessionProgress").mockResolvedValue({ items: [], students: [] });
    vi.spyOn(AiJudgeService, "listSessionRunBatches").mockResolvedValue([]);
  });

  const batchPayload = {
    run_batch_id: "batch-1",
    status: "completed",
    summary: { nodes: 1, students: 1, targets: 1, completed: 1, failed: 0 },
    nodes: [
      {
        target_node_key: "db",
        display_label: "P2",
        artifact_id: "artifact-1",
        run_id: "run-1",
        status: "completed",
        progress_json: { total: 1, done: 1 },
        result_summary_json: {},
      },
    ],
    students: [
      {
        student_id: "enrollment-1",
        nodes: [
          {
            node_key: "db",
            display_label: "P2",
            run_id: "run-1",
            execution_status: "completed",
            vmid: 101,
            teacher_review: { feedback: "舊留言", decisions: {} },
            items: [
              {
                rubric_item_id: "item-db",
                title: "確認 PostgreSQL",
                status: "warning",
                checks: [
                  { id: "check-db", title: "pg_isready", status: "warning", evidence: "延遲偏高" },
                ],
              },
            ],
            unmapped_checks: [],
          },
        ],
      },
    ],
  };
  const members = [
    { user_id: "user-1", full_name: "王小明", email: "s1@example.edu", vmid: 101, node_key: "db" },
  ];

  test("批次投影以 vmid 對應成員，並把檢查點攤平成核查列", () => {
    const rows = buildBatchReviewRows(batchPayload, members);
    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.key).toBe("enrollment-1|db|101");
    expect(row.runId).toBe("run-1");
    expect(row.vmid).toBe(101);
    expect(row.member.full_name).toBe("王小明");
    expect(row.target.status).toBe("completed");
    expect(row.target.parsed_result.checks).toHaveLength(1);
    expect(row.target.teacher_review).toMatchObject({ feedback: "舊留言" });
    expect(getTargetReviewSummary(row.target)).toMatchObject({ kind: "pending", pending: 1 });
  });

  test("學生節點缺少 run_id 時會從批次節點摘要回補", () => {
    const batchWithoutStudentRunId = {
      ...batchPayload,
      students: batchPayload.students.map((student) => ({
        ...student,
        nodes: student.nodes.map(({ run_id: _runId, ...node }) => node),
      })),
    };

    const rows = buildBatchReviewRows(batchWithoutStudentRunId, members);

    expect(rows[0].runId).toBe("run-1");
  });

  test("成員對映不依賴 enrollment id 等於 user id 的巧合", () => {
    const rows = buildBatchReviewRows(batchPayload, [
      { user_id: "user-other", full_name: "王小明", email: "s1@example.edu", vmid: 101, node_key: "db" },
    ]);
    expect(rows[0].member.email).toBe("s1@example.edu");
  });

  test("執行失敗的機器仍會產生核查列並標記執行失敗", () => {
    const failed = {
      ...batchPayload,
      students: [{
        student_id: "enrollment-1",
        nodes: [{
          node_key: "db", run_id: "run-1", execution_status: "failed",
          reason_code: "not_running", vmid: 101, items: [],
        }],
      }],
    };
    const rows = buildBatchReviewRows(failed, []);
    expect(getTargetReviewSummary(rows[0].target).kind).toBe("failed");
  });

  test("mergeNodeTeacherReview 只更新對應節點的導師核查", () => {
    const merged = mergeNodeTeacherReview(
      batchPayload,
      { studentId: "enrollment-1", nodeKey: "db" },
      { feedback: "新留言", decisions: { "check-db": "pass" }, updated_at: "2026-09-20T00:00:00Z" },
    );
    const node = merged.students[0].nodes[0];
    expect(node.teacher_review).toMatchObject({
      feedback: "新留言",
      decisions: { "check-db": "pass" },
    });
    expect(node.items).toHaveLength(1);
  });

  test("legacy 單一 run 資料仍以既有列為準", () => {
    const run = {
      id: "run-legacy",
      target_results_json: {
        targets: [
          {
            vmid: 101,
            user: { email: "s1@example.edu", full_name: "王小明" },
            status: "completed",
            teacher_review: { feedback: "", decisions: {} },
            parsed_result: { checks: [{ id: "check-1", status: "pass" }] },
          },
        ],
      },
    };
    const rows = buildLegacyReviewRows(run, members);
    expect(rows[0].runId).toBe("run-legacy");
    expect(getTargetReviewSummary(rows[0].target).kind).toBe("automatic");
  });

  test("legacy 公開結果沒有 vmid 時仍保留 student_id 供核查儲存", () => {
    const run = {
      id: "run-legacy",
      target_results_json: {
        targets: [{
          student_id: "enrollment-1",
          user: { id: "user-1", email: "s1@example.edu", full_name: "王小明" },
          status: "failed",
          reason_code: "not_running",
          parsed_result: { checks: [] },
        }],
      },
    };

    const rows = buildLegacyReviewRows(run, []);

    expect(rows).toHaveLength(1);
    expect(rows[0].runId).toBe("run-legacy");
    expect(rows[0].vmid).toBeNull();
    expect(rows[0].studentId).toBe("enrollment-1");
    expect(rows[0].member.full_name).toBe("王小明");
  });

  test("核查頁改用整批資料顯示逐機器檢查點並提供一次執行", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([
      { id: "run-1", artifact_id: "artifact-1", run_batch_id: "batch-1", status: "completed" },
    ]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([
      {
        artifact_set_id: "set-1", is_active: true,
        status: "approved",
        source_analysis_revision: 3,
        children: [{ id: "artifact-1", target_node_key: "db", name: "db 腳本", status: "approved" }],
      },
    ]);
    const getBatch = vi.spyOn(AiJudgeService, "getSessionRunBatch").mockResolvedValue(batchPayload);

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(getBatch).toHaveBeenCalledWith("class-1", "session-1", "batch-1");
    expect(container.textContent).toContain("一次執行");
    expect(container.textContent).toContain("學生打勾完成");
    expect(container.textContent).toContain("待確認 1 項");

    const toggle = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("王小明"));
    expect(toggle).toBeTruthy();
    await act(async () => {
      toggle.click();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(container.textContent).toContain("確認 PostgreSQL");
    expect(container.textContent).toContain("pg_isready");
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test.each([
    ["vm_powered_off", "錯誤：關機", "機器已關機，已跳過檢查。開機後可重新執行。"],
    ["vm_status_unavailable", "錯誤：狀態不明", "無法確認機器狀態，已跳過檢查。請稍後重新執行。"],
  ])("%s 在收合與展開時皆顯示原因，不誤報腳本沒有輸出", async (reason, label, message) => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([
      { id: "run-1", artifact_id: "artifact-1", run_batch_id: "batch-1", status: "completed" },
    ]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([
      { artifact_set_id: "set-1", is_active: true, status: "approved", children: [] },
    ]);
    const failed = structuredClone(batchPayload);
    const node = failed.students[0].nodes[0];
    node.execution_status = "failed";
    node.reason_code = reason;
    node.timing = { total_seconds: 0.25 };
    node.items[0].checks = [];
    node.items[0].status = "unknown";
    vi.spyOn(AiJudgeService, "getSessionRunBatch").mockResolvedValue(failed);
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members.map((member) => ({ ...member, vm_status: "running" }))} /></ConfirmProvider>, queryClient));
        await new Promise((resolve) => setTimeout(resolve, 30));
      });
      expect(container.textContent).toContain(label);
      // 最近一次檢查的失敗原因與目前電源狀態各自保留；重新開機不抹掉舊失敗。
      const power = container.querySelector('[role="img"][aria-label="已開機"]');
      expect(power).toBeTruthy();
      expect(power.parentElement.title).toContain(label);
      expect(power.parentElement.title).toContain("VMID 101");
      const toggle = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("王小明"));
      await act(async () => toggle.click());
      expect(container.textContent).toContain(message);
      // 關機／狀態不明有自己的說明，不再同時出現「請檢查 Python、timeout」這種主機環境提示
      expect(container.textContent).not.toContain(i18n.t("AiJudgePanel.executionEnvironmentHint", { ns: "teaching" }));
      expect(container.textContent).not.toContain("此檢查點沒有回傳可顯示的結果。");
      expect(container.textContent).not.toContain("腳本沒有回傳可顯示的檢查項目。");
      expect([...container.querySelectorAll("summary")].filter((element) => element.textContent === i18n.t("AiJudgePanel.timingTitle", { ns: "teaching" }))).toHaveLength(1);
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

  test("部分步驟未回傳時會顯示不完整提示並保留已取得的證據", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([
      { id: "run-1", artifact_id: "artifact-1", run_batch_id: "batch-1", status: "completed" },
    ]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([{ artifact_set_id: "set-1", is_active: true, status: "approved", children: [] }]);
    const incomplete = structuredClone(batchPayload);
    incomplete.students[0].nodes[0].items[0].status = "unknown";
    incomplete.students[0].nodes[0].items[0].missing_check_ids = ["missing-step"];
    vi.spyOn(AiJudgeService, "getSessionRunBatch").mockResolvedValue(incomplete);

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    const toggle = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("王小明"));
    await act(async () => {
      toggle.click();
    });
    expect(container.textContent).toContain("檢查結果不完整：有 1 個步驟未回傳結果");
    expect(container.textContent).toContain("pg_isready");
    await act(async () => root.unmount());
    container.remove();
  });

  test("離開再回來時整批還在跑：接回進度，一次執行維持停用", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([
      { id: "run-1", artifact_id: "artifact-1", run_batch_id: "batch-1", status: "running" },
    ]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([
      { artifact_set_id: "set-1", is_active: true, status: "approved", children: [] },
    ]);
    vi.spyOn(AiJudgeService, "getSessionRunBatch").mockResolvedValue({
      ...batchPayload,
      status: "running",
      summary: { ...batchPayload.summary, completed: 0 },
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const runButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.endsWith("一次執行"));
    expect(runButton.disabled).toBe(true);
    expect(container.textContent).toContain("執行中 0 / 1 台");
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("腳本集讀取失敗不當成尚未建立，改顯示錯誤狀態並可重試", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
    const listSets = vi.spyOn(AiJudgeService, "listSessionScriptSets")
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue([]);

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(container.textContent).not.toContain("尚未建立可執行的檢查腳本集");
    const retry = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes(i18n.t("Error.retry", { ns: "common" })));
    expect(retry).toBeTruthy();
    await act(async () => {
      retry.click();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(listSets).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("還沒有可核查的結果");
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("核查草稿逐項比對：取消再勾回來不算改過", () => {
    const saved = { feedback: "", decisions: { a: "pass", b: "fail" } };
    expect(isReviewDraftDirty({ feedback: "", decisions: { b: "fail", a: "pass" } }, saved)).toBe(false);
    expect(isReviewDraftDirty({ feedback: "", decisions: { a: "pass" } }, saved)).toBe(true);
    expect(isReviewDraftDirty({ feedback: "", decisions: { a: "fail", b: "fail" } }, saved)).toBe(true);
    expect(isReviewDraftDirty({ feedback: "補交", decisions: { a: "pass", b: "fail" } }, saved)).toBe(true);
    expect(isReviewDraftDirty(undefined, saved)).toBe(false);
  });

  test("生效組切換時保留並標明上一組未儲存核查，查詢改用新組", async () => {
    vi.spyOn(AiJudgeService, "listSessionScriptSets")
      .mockResolvedValueOnce([{ artifact_set_id: "set-1", is_active: true, status: "approved", children: [] }])
      .mockResolvedValue([{ artifact_set_id: "set-2", is_active: true, status: "approved", children: [] }]);
    const listRuns = vi.spyOn(AiJudgeService, "listSessionRuns").mockImplementation(async (_classId, _sessionId, setId) => (
      setId === "set-1" ? [{ id: "run-1", run_batch_id: "batch-1", status: "completed" }] : []
    ));
    vi.spyOn(AiJudgeService, "getSessionRunBatch").mockResolvedValue(batchPayload);
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const render = (active) => root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members} active={active} /></ConfirmProvider>, queryClient));
    await act(async () => {
      render(true);
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    await act(async () => {
      [...container.querySelectorAll("button")].find((button) => button.textContent.includes("王小明")).click();
    });
    await act(async () => {
      [...container.querySelectorAll("button")].find((button) => button.getAttribute("aria-pressed") !== null && button.textContent.includes("通過")).click();
    });
    await act(async () => render(false));
    await act(async () => {
      render(true);
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(listRuns).toHaveBeenLastCalledWith("class-1", "session-1", "set-2");
    expect(container.textContent).toContain(i18n.t("AiJudgePanel.previousSetDraftsWaiting", { ns: "teaching" }));
    expect(container.textContent).toContain("pg_isready");
    expect([...container.querySelectorAll("button")].find((button) => button.textContent.includes(i18n.t("AiJudgePanel.saveStudentBtn", { ns: "teaching" }))).disabled).toBe(false);
    await act(async () => root.unmount());
    container.remove();
  });

  test("批次模式下判定會以對應 run 與 vmid 儲存", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([
      { id: "run-1", artifact_id: "artifact-1", run_batch_id: "batch-1", status: "completed" },
    ]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([{ artifact_set_id: "set-1", is_active: true, status: "approved", children: [] }]);
    vi.spyOn(AiJudgeService, "getSessionRunBatch").mockResolvedValue(batchPayload);
    const updateReview = vi.spyOn(AiJudgeService, "updateTargetReview").mockResolvedValue({
      id: "run-1",
      target_results_json: {
        targets: [{
          vmid: 101,
          status: "completed",
          teacher_review: { feedback: "", decisions: { "check-db": "pass" }, updated_at: "2026-09-20T00:00:00Z" },
          parsed_result: { checks: [{ id: "check-db", title: "pg_isready", status: "warning" }] },
        }],
      },
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const toggle = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("王小明"));
    await act(async () => {
      toggle.click();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    const passButton = [...container.querySelectorAll("button")]
      .find((button) => button.getAttribute("aria-pressed") !== null && button.textContent.includes("通過"));
    expect(passButton).toBeTruthy();
    await act(async () => {
      passButton.click();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    const saveButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes(i18n.t("AiJudgePanel.saveStudentBtn", { ns: "teaching" })));
    expect(saveButton.disabled).toBe(false);
    await act(async () => {
      saveButton.click();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(updateReview).toHaveBeenCalledWith(
      "class-1",
      "session-1",
      "run-1",
      101,
      { feedback: "舊留言", decisions: { "check-db": "pass" } },
    );
    expect(container.textContent).toContain("上次儲存");
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test.each([false, true])("一個學生共用儲存按鈕：跨機器判定與回覆一起存，回覆失敗可重試（%s）", async (failReplyOnce) => {
    const batch = structuredClone(batchPayload);
    batch.students[0].nodes.push({
      node_key: "web", run_id: "run-web", execution_status: "completed", vmid: 102,
      items: [{ rubric_item_id: "web", title: "網站檢查", status: "warning",
        checks: [{ id: "check-web", title: "表單資料", status: "warning" }] }],
    });
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([
      { id: "run-1", run_batch_id: "batch-1", status: "completed" },
    ]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([
      { artifact_set_id: "set-1", is_active: true, status: "approved", children: [] },
    ]);
    vi.spyOn(AiJudgeService, "getSessionRunBatch").mockResolvedValue(batch);
    vi.spyOn(AiJudgeService, "getSessionProgress").mockResolvedValue({
      items: [], students: [{ user_id: "user-1", email: "s1@example.edu", full_name: "王小明" }],
    });
    const updateReview = vi.spyOn(AiJudgeService, "updateTargetReview")
      .mockImplementation(async (_classId, _sessionId, runId, vmid, draft) => ({
        id: runId,
        target_results_json: { targets: [{ vmid, teacher_review: {
          ...draft, updated_at: "2026-10-16T04:00:00Z",
        } }] },
      }));
    const saveReply = vi.spyOn(AiJudgeService, "saveStudentReply").mockResolvedValue({
      teacher_reply: "請補上備份排程。", teacher_reply_at: "2026-10-16T04:00:00Z",
    });
    if (failReplyOnce) saveReply.mockRejectedValueOnce(new Error("network"));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onDirtyChange = vi.fn();
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1"
        members={members} onDirtyChange={onDirtyChange} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    await act(async () => {
      [...container.querySelectorAll("button")].find((button) => button.textContent.includes("王小明")).click();
    });
    const saveButtons = [...container.querySelectorAll("button")].filter((button) => (
      button.textContent.includes(i18n.t("AiJudgePanel.saveStudentBtn", { ns: "teaching" }))
    ));
    expect(saveButtons).toHaveLength(1);
    const saveButton = saveButtons[0];
    expect(saveButton.disabled).toBe(true);
    const textarea = container.querySelector("textarea");
    const cards = container.querySelectorAll("section[aria-label]");
    expect(cards).toHaveLength(2);
    for (const card of cards) {
      expect(card.compareDocumentPosition(textarea) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    await act(async () => {
      for (const button of container.querySelectorAll('button[aria-pressed="false"]')) {
        if (button.textContent.endsWith("通過") && !button.textContent.endsWith("未通過")) button.click();
      }
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(textarea, "請補上備份排程。");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(saveButton.disabled).toBe(false);
    await act(async () => {
      saveButton.click();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(updateReview).toHaveBeenCalledTimes(2);
    expect(updateReview).toHaveBeenCalledWith("class-1", "session-1", "run-1", 101,
      { feedback: "舊留言", decisions: { "check-db": "pass" } });
    expect(updateReview).toHaveBeenCalledWith("class-1", "session-1", "run-web", 102,
      { feedback: "", decisions: { "check-web": "pass" } });
    if (failReplyOnce) {
      expect(saveButton.disabled).toBe(false);
      expect(textarea.value).toBe("請補上備份排程。");
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
      await act(async () => {
        saveButton.click();
        await new Promise((resolve) => setTimeout(resolve, 30));
      });
      expect(updateReview).toHaveBeenCalledTimes(2);
      expect(saveReply).toHaveBeenCalledTimes(2);
    }
    expect(saveReply).toHaveBeenLastCalledWith("class-1", "session-1", "user-1", "請補上備份排程。");
    expect(saveButton.disabled).toBe(true);
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    await act(async () => root.unmount());
    container.remove();
  });

  test("回覆是給學生這一週的：檢查失敗、沒有 vmid 也能回覆，再回覆會覆蓋", async () => {
    const failedBatch = {
      ...batchPayload,
      summary: { nodes: 1, students: 1, targets: 1, completed: 0, failed: 1 },
      students: [{
        student_id: "enrollment-1",
        nodes: [{
          node_key: "db",
          display_label: "P2",
          execution_status: "failed",
          reason_code: "missing_vmid",
          vmid: null,
          items: [],
          unmapped_checks: [],
        }],
      }],
    };
    const failedMembers = [{
      student_id: "enrollment-1",
      user_id: "user-1",
      full_name: "王小明",
      email: "s1@example.edu",
      vmid: null,
      node_key: "db",
    }];
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([
      { id: "run-1", artifact_id: "artifact-1", run_batch_id: "batch-1", status: "completed" },
    ]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([{ artifact_set_id: "set-1", is_active: true, status: "approved", children: [] }]);
    vi.spyOn(AiJudgeService, "getSessionRunBatch").mockResolvedValue(failedBatch);
    vi.spyOn(AiJudgeService, "getSessionProgress").mockResolvedValue({
      items: [],
      students: [{ user_id: "user-1", email: "s1@example.edu", full_name: "王小明", completed_item_ids: [], teacher_reply: "舊的回覆" }],
    });
    const saveReply = vi.spyOn(AiJudgeService, "saveStudentReply").mockResolvedValue({
      user_id: "user-1",
      teacher_reply: "請先確認虛擬機配置。",
      teacher_reply_at: "2026-09-20T00:00:00Z",
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={failedMembers} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const toggle = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("王小明"));
    expect(toggle).toBeTruthy();
    await act(async () => {
      toggle.click();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    const textarea = container.querySelector("textarea");
    expect(textarea.value).toBe("舊的回覆");
    // 回覆框在這位學生的檢查結果下面
    const machineCard = container.querySelector("section[aria-label]");
    expect(machineCard.compareDocumentPosition(textarea) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      ).set;
      setValue.call(textarea, "請先確認虛擬機配置。");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    const saveButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes(i18n.t("AiJudgePanel.saveStudentBtn", { ns: "teaching" })));
    expect(saveButton.disabled).toBe(false);
    await act(async () => {
      saveButton.click();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(saveReply).toHaveBeenCalledExactlyOnceWith("class-1", "session-1", "user-1", "請先確認虛擬機配置。");
    expect(container.textContent).toContain(i18n.t("AiJudgePanel.replied", { ns: "teaching" }));

    // Ctrl+Enter 也能送出
    await act(async () => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setValue.call(textarea, "記得重開服務。");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    await act(async () => {
      textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(saveReply).toHaveBeenLastCalledWith("class-1", "session-1", "user-1", "記得重開服務。");
    // 每台機器不再各自留言：展開後只有這一個回覆框
    expect(container.querySelectorAll("textarea")).toHaveLength(1);
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("沒有已核准腳本集時，一次執行不可用且空狀態說明要先核准腳本", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([]);

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(container.textContent).toContain("還沒有可核查的結果");
    expect(container.textContent).toContain("在「任務設定」按「製作腳本」後再回來");
    expect(container.textContent).not.toContain("一次執行");
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  test("學生總覽以學生分組並彙總跨機器通過/未通過/待確認", () => {
    const rows = buildBatchReviewRows(
      {
        students: [
          {
            student_id: "enrollment-1",
            nodes: [
              {
                node_key: "web",
                run_id: "run-web",
                execution_status: "completed",
                vmid: 101,
                teacher_review: { feedback: "", decisions: {} },
                items: [
                  {
                    rubric_item_id: "item-web",
                    title: "確認 nginx",
                    status: "pass",
                    checks: [{ id: "check-web", title: "nginx", status: "pass" }],
                  },
                ],
                unmapped_checks: [],
              },
              {
                node_key: "db",
                run_id: "run-db",
                execution_status: "completed",
                vmid: 102,
                teacher_review: { feedback: "", decisions: {} },
                items: [
                  {
                    rubric_item_id: "item-db",
                    title: "確認 PostgreSQL",
                    status: "warning",
                    checks: [{ id: "check-db", title: "pg_isready", status: "warning" }],
                  },
                  {
                    rubric_item_id: "item-db-fail",
                    title: "確認連線",
                    status: "fail",
                    checks: [{ id: "check-fail", title: "ping", status: "fail" }],
                  },
                ],
                unmapped_checks: [],
              },
            ],
          },
          {
            student_id: "enrollment-2",
            nodes: [
              {
                node_key: "web",
                run_id: "run-web",
                execution_status: "completed",
                vmid: 201,
                teacher_review: { feedback: "", decisions: {} },
                items: [
                  {
                    rubric_item_id: "item-web",
                    title: "確認 nginx",
                    status: "pass",
                    checks: [{ id: "check-web", title: "nginx", status: "pass" }],
                  },
                ],
                unmapped_checks: [],
              },
            ],
          },
        ],
      },
      [
        { user_id: "user-1", full_name: "王小明", email: "s1@example.edu", vmid: 101, node_key: "web" },
        { user_id: "user-1", full_name: "王小明", email: "s1@example.edu", vmid: 102, node_key: "db" },
        { user_id: "user-2", full_name: "李小華", email: "s2@example.edu", vmid: 201, node_key: "web" },
      ],
    );
    const students = buildStudentOverviewRows(rows);
    expect(students).toHaveLength(2);
    const first = students.find((item) => item.studentId === "enrollment-1");
    expect(first.machines).toHaveLength(2);
    expect(aggregateStudentCheckTotals(first.machines)).toMatchObject({
      pass: 1,
      fail: 1,
      pending: 1,
    });
    expect(getStudentOverviewStatus(first.machines)).toMatchObject({
      kind: "pending",
      pending: 1,
    });
    const sorted = sortStudentOverviewRows(students, "pending");
    expect(sorted[0].studentId).toBe("enrollment-1");
    const byNumber = sortStudentOverviewRows(students, "student-number");
    expect(byNumber[0].user.email).toBe("s1@example.edu");
  });

  test("已儲存的導師判定會從待確認移到通過/未通過", () => {
    const machines = [
      {
        key: "enrollment-1|db|101",
        member: { email: "s1@example.edu", full_name: "王小明" },
        target: {
          status: "completed",
          teacher_review: { feedback: "", decisions: { "check-db": "pass" } },
          parsed_result: { checks: [{ id: "check-db", status: "warning" }] },
        },
      },
    ];
    expect(aggregateStudentCheckTotals(machines)).toMatchObject({
      pass: 1,
      pending: 0,
    });
    expect(getStudentOverviewStatus(machines).kind).toBe("reviewed");
  });

  test("有已核准腳本集但尚未執行時，空狀態也能直接一次執行", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([
      {
        artifact_set_id: "set-1", is_active: true,
        status: "approved",
        source_analysis_revision: 3,
        children: [{ id: "artifact-1", target_node_key: "db", name: "db 腳本", status: "approved" }],
      },
    ]);
    const createRun = vi.spyOn(AiJudgeService, "createSessionScriptSetRun").mockResolvedValue({
      run_batch_id: "batch-2",
      status: "pending",
      summary: { nodes: 1, students: 1, targets: 1, completed: 0, failed: 0 },
      nodes: [],
      students: [],
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={members} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const runOnceButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("一次執行"));
    expect(runOnceButton).toBeTruthy();
    await act(async () => {
      runOnceButton.click();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });

    /* 對話框 portal 到 body，不在 container 裡 */
    expect(document.body.textContent).toContain("一次執行整組檢查點");
    const confirmButton = [...document.body.querySelectorAll("button")]
      .find((button) => button.textContent.includes("確認執行"));
    expect(confirmButton).toBeTruthy();
    await act(async () => {
      confirmButton.click();
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(createRun).toHaveBeenCalledWith("class-1", "session-1", "set-1", {
      userIds: null, runBatchId: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
    });
    expect(container.textContent).not.toContain("確認執行");
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});

describe("導師核查：全班當週回報與指定檢查", () => {
  beforeEach(() => {
    vi.spyOn(AiJudgeService, "listSessionRunBatches").mockResolvedValue([]);
  });

  const studentBatch = (batchId, studentId, vmid, evidence) => ({
    run_batch_id: batchId,
    status: "completed",
    nodes: [{ target_node_key: "web", run_id: `${batchId}-run`, status: "completed" }],
    students: [{
      student_id: studentId,
      nodes: [{
        node_key: "web", run_id: `${batchId}-run`, execution_status: "completed", vmid,
        items: [{ rubric_item_id: "service", checks: [{ id: "service", status: "pass", evidence }] }],
      }],
    }],
  });

  test("多批結果合併時，每位學生取最近一次有檢查到他的那一批", () => {
    const newest = studentBatch("batch-2", "enrollment-a", 101, "新的");
    const older = {
      ...studentBatch("batch-1", "enrollment-a", 101, "舊的"),
      students: [
        ...studentBatch("batch-1", "enrollment-a", 101, "舊的").students,
        ...studentBatch("batch-1", "enrollment-b", 102, "B 的結果").students,
      ],
    };
    const rows = buildMergedBatchReviewRows([newest, older], []);
    expect(rows.map((row) => [row.studentId, row.batchId])).toEqual([
      ["enrollment-a", "batch-2"],
      ["enrollment-b", "batch-1"],
    ]);
    expect(rows[0].target.parsed_result.checks[0].evidence).toBe("新的");
  });

  test("總覽補上自評回報，沒有結果的學生也列出來", () => {
    const students = [{
      studentId: "enrollment-a",
      machines: [{ member: { user_id: "user-a" } }],
      user: { user_id: "user-a", full_name: "甲" },
      totals: {},
      status: { kind: "automatic" },
    }];
    const progress = {
      items: [{ id: "service" }, { id: "database" }],
      students: [
        { user_id: "user-a", email: "a@x", completed_item_ids: ["service", "database"], ready_at: "2026-09-08T01:00:00Z" },
        { user_id: "user-b", email: "b@x", full_name: "乙", completed_item_ids: ["service"], ready_at: null },
        { user_id: "user-c", email: "c@x", full_name: "丙", completed_item_ids: [], ready_at: null },
      ],
    };
    const result = attachStudentReports(students, progress);
    expect(result.map((row) => [row.userId, row.report.kind])).toEqual([
      ["user-a", "ready"],
      ["user-b", "partial"],
      ["user-c", "none"],
    ]);
    expect(result[1]).toMatchObject({ machines: [], status: { kind: "missing" }, report: { done: 1, total: 2 } });
  });

  test("總覽只看通過／未通過；每位學生列出自己的機器與那台的結果", () => {
    const passTarget = { parsed_result: { checks: [{ id: "a", status: "pass" }] } };
    const failTarget = { parsed_result: { checks: [{ id: "b", status: "fail" }] } };
    expect(checkOutcome([])).toEqual({ kind: "none", count: 0 });
    expect(checkOutcome([{ target: passTarget }]).kind).toBe("pass");
    expect(checkOutcome([{ target: passTarget }, { target: failTarget }]).kind).toBe("fail");

    const student = {
      userId: "user-a",
      machines: [
        { key: "101", vmid: 101, nodeKey: "web", target: passTarget },
        { key: "999", vmid: 999, nodeKey: "old", target: failTarget },
      ],
    };
    const members = [
      { user_id: "user-a", vmid: 101, node_key: "web", node_name: "網站" },
      { user_id: "user-a", vmid: 102, node_key: "db", node_name: "資料庫" },
      { user_id: "user-b", vmid: 201, node_key: "web" },
    ];
    expect(studentMachineChips(student, members).map((chip) => [chip.vmid, chip.label, chip.outcome])).toEqual([
      [101, "網站", "pass"],
      [102, "資料庫", "none"],
      [999, "old", "fail"],
    ]);
  });

  test("檢查點標籤：待判定優先，其次未通過，全過才寫幾項都通過；老師判定會蓋過原始狀態", () => {
    const item = {
      status: "warning",
      checks: [
        { id: "a", status: "pass" },
        { id: "b", status: "warning" },
        { id: "c", status: "collected" },
      ],
    };
    expect(checkpointBadge(item)).toMatchObject({ kind: "pending" });
    expect(checkpointBadge(item, { b: "pass" })).toMatchObject({ kind: "pending" });
    expect(checkpointBadge(item, { b: "pass", c: "fail" })).toMatchObject({ kind: "fail" });
    expect(checkpointBadge(item, { b: "pass", c: "pass" })).toMatchObject({ kind: "pass" });
    expect(checkpointBadge({ status: "fail", checks: [] })).toMatchObject({ kind: "fail" });
    expect(checkpointBadge({ status: "pass", checks: [{ id: "x", status: "pass" }] })).toMatchObject({ kind: "pass" });
  });

  test("機器標籤帶上 PVE 開機狀態，只有在線名單有的機器才算得出來", () => {
    expect(["running", "RUNNING"].map(machinePowerState)).toEqual(["on", "on"]);
    expect(["stopped", "paused"].map(machinePowerState)).toEqual(["off", "off"]);
    expect(["completed", "unknown", undefined, ""].map(machinePowerState)).toEqual(["unknown", "unknown", "unknown", "unknown"]);

    const student = { userId: "user-a", machines: [{ key: "999", vmid: 999, nodeKey: "old" }] };
    const members = [
      { user_id: "user-a", vmid: 101, node_key: "web", vm_status: "running" },
      { user_id: "user-a", vmid: 102, node_key: "db", vm_status: "stopped" },
    ];
    expect(studentMachineChips(student, members).map((chip) => [chip.vmid, chip.power])).toEqual([
      [101, "on"],
      [102, "off"],
      [999, "unknown"],
    ]);
  });

  test("還沒執行過也列出全班；可以只檢查已回報完成的學生", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([
      { artifact_set_id: "set-1", is_active: true, status: "approved", children: [] },
    ]);
    vi.spyOn(AiJudgeService, "getSessionProgress").mockResolvedValue({
      items: [{ id: "service" }],
      students: [
        { user_id: "user-a", email: "a@x", full_name: "甲", completed_item_ids: ["service"], ready_at: "2026-09-08T01:00:00Z" },
        { user_id: "user-b", email: "b@x", full_name: "乙", completed_item_ids: [], ready_at: null },
      ],
    });
    const createRun = vi.spyOn(AiJudgeService, "createSessionScriptSetRun").mockResolvedValue({
      run_batch_id: "batch-9", status: "pending", summary: {}, nodes: [], students: [],
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={[]} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const text = container.textContent;
    expect(text).toContain("甲");
    expect(text).toContain("乙");
    expect(text).toContain(i18n.t("AiJudgePanel.reportReady", { ns: "teaching" }));
    expect(text).toContain(i18n.t("AiJudgePanel.reportNone", { ns: "teaching" }));
    // 打勾比例以全班為分母；還沒檢查任何人
    const bars = [...container.querySelectorAll('[role="img"]')].map((node) => node.getAttribute("aria-label"));
    expect(bars).toContain(i18n.t("AiJudgePanel.metricReportedValue", { ns: "teaching", done: 1, total: 2 }));
    expect(bars).toContain(i18n.t("AiJudgePanel.metricCheckValue", { ns: "teaching", pass: 0, fail: 0, error: 0, checked: 0, total: 2 }));

    const runReady = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes(i18n.t("AiJudgePanel.runReadyBtn", { ns: "teaching", count: 1 })));
    await act(async () => {
      runReady.click();
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(createRun).toHaveBeenCalledExactlyOnceWith("class-1", "session-1", "set-1", {
      userIds: ["user-a"], runBatchId: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });

    await act(async () => root.unmount());
    container.remove();
  });

  test("還沒有腳本也看得到全班回報進度，只是不能執行檢查", async () => {
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([]);
    vi.spyOn(AiJudgeService, "getSessionProgress").mockResolvedValue({
      items: [{ id: "service" }],
      students: [
        { user_id: "user-a", email: "a@x", full_name: "甲", completed_item_ids: ["service"], ready_at: "2026-09-08T01:00:00Z" },
        { user_id: "user-b", email: "b@x", full_name: "乙", completed_item_ids: [], ready_at: null },
      ],
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={[]} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const text = container.textContent;
    expect(text).toContain("甲");
    expect(text).toContain("乙");
    expect(text).toContain(i18n.t("AiJudgePanel.reportReady", { ns: "teaching" }));
    expect(text).toContain(i18n.t("AiJudgePanel.reviewNoScriptNotice", { ns: "teaching" }));
    const runButtons = [...container.querySelectorAll("button")].filter((button) => (
      button.textContent.includes(i18n.t("AiJudgePanel.runReadyBtn", { ns: "teaching", count: 1 }))
      || button.textContent.includes(i18n.t("AiJudgePanel.runAllBtn", { ns: "teaching" }))
    ));
    expect(runButtons).toHaveLength(2);
    expect(runButtons.every((button) => button.disabled)).toBe(true);

    await act(async () => root.unmount());
    container.remove();
  });

  test("這週內容還沒發布：有腳本也不能檢查，可以直接前往每週內容發布", async () => {
    const goToPublish = vi.fn();
    vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
    vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([
      { artifact_set_id: "set-1", is_active: true, status: "approved", children: [] },
    ]);
    vi.spyOn(AiJudgeService, "getSessionProgress").mockResolvedValue({
      items: [{ id: "service" }],
      students: [
        { user_id: "user-a", email: "a@x", full_name: "甲", completed_item_ids: ["service"], ready_at: "2026-09-08T01:00:00Z" },
      ],
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(<ConfirmProvider><TeacherReviewTab classId="class-1" sessionId="session-1" members={[]} weekPublished={false} onGoToPublish={goToPublish} /></ConfirmProvider>, queryClient));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    const publishButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes(i18n.t("AiJudgePanel.goToPublish", { ns: "teaching" })));
    await act(async () => publishButton.click());
    expect(goToPublish).toHaveBeenCalledOnce();

    expect(container.textContent).toContain(i18n.t("AiJudgePanel.reviewWeekDraftNotice", { ns: "teaching" }));
    const runButtons = [...container.querySelectorAll("button")].filter((button) => (
      button.textContent.includes(i18n.t("AiJudgePanel.runReadyBtn", { ns: "teaching", count: 1 }))
      || button.textContent.includes(i18n.t("AiJudgePanel.runAllBtn", { ns: "teaching" }))
    ));
    expect(runButtons).toHaveLength(2);
    expect(runButtons.every((button) => button.disabled)).toBe(true);

    await act(async () => root.unmount());
    container.remove();
  });
});
