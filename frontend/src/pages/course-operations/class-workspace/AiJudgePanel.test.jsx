// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, test, vi } from "vitest";
import {
  ChatPanel,
  CreateCheckDialog,
  RubricTable,
  ProposalPanel,
  SaveAndCreateAction,
  ScriptGenerationNotice,
  SessionTitle,
  applyProposalOperations,
  buildProposalDiff,
  getRubricDisplayName,
  getRubricCheckTitle,
  getRubricItemsValue,
  getRubricReviewItemIds,
  getPendingRubricItemIds,
  resolveDetectabilityNeedsReview,
  getScriptCreationBlocker,
  getSessionMenuPosition,
  getSelectedRubricSource,
  getScriptCreationDestination,
  getSelectableProposalIds,
  mergeSessionMessages,
  resolveActiveSessionId,
  proposalToolCallLines,
  RubricsTab,
} from "./AiJudgePanel";
import {
  AiJudgeService,
  RUBRIC_POLISH_PROMPT,
} from "../../../services/aiJudge";

const originalScrollIntoView = Element.prototype.scrollIntoView;

afterEach(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
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
    expect(html).not.toContain("儲存並製作");
    expect(html).not.toContain("製作檢查腳本");
    expect(html).toContain("資料來源");
    expect(html).toContain('aria-controls="ai-chat-data-sources"');
    expect(html).toContain("描述想檢查的需求");
    expect(html).toContain("同意提案後才會正式保存");
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
          message: "上游服務逾時。可再次按「儲存並製作」重試。",
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
    expect(html).toContain("spinning");
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

describe("RubricsTab 儲存並製作流程", () => {
  test("重新核對缺少資訊時把 server assistant 結果加入 Chat，且不啟動腳本", async () => {
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
      content: "重新核對後，「確認服務 Port」還缺少：服務 Port。",
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
    const createScript = vi.spyOn(AiJudgeService, "createSessionScript").mockResolvedValue({
      status: "approved",
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <RubricsTab
          classId="class-1"
          judgeSession={{ id: "session-1", selected_file_id: "file-1" }}
        />,
      );
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    const saveButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes("儲存並製作"));
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
    expect(container.textContent).toContain("確認服務 Port");
    expect(container.textContent).toContain("尚有項目需要補充");
    expect(createScript).not.toHaveBeenCalled();
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});

describe("CreateCheckDialog", () => {
  test("新增檢查直接詢問名稱並說明會建立空白檢查表", () => {
    const html = renderToStaticMarkup(
      <CreateCheckDialog onClose={() => {}} onSubmit={() => {}} />,
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('id="create-check-name-input"');
    expect(html).toContain("輸入名稱後，會直接建立一份空白檢查表");
    expect(html).toContain("建立空白檢查");
    expect(html).not.toContain("使用已有評分文件");
    expect(html).not.toContain("選擇建立方式");
  });
});

describe("ProposalPanel", () => {
  test("以可展開透明預覽呈現 Ready 操作與正式套用文案", () => {
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
    expect(html).toContain("Ready 3");
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

    expect(html).toContain("儲存並製作");
    expect(html).toContain("AI 核對全部項目；全綠後會直接製作腳本");
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

  test("有待處理提案時停用並顯示原因", () => {
    const html = renderToStaticMarkup(
      <SaveAndCreateAction onClick={() => {}} blocker="請先套用目前提案" />,
    );

    expect(html).toContain("disabled");
    expect(html).toContain('title="請先套用目前提案"');
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
    expect(html).toContain('value="Python 版本檢查"');
    expect(html).toContain("可以");
    expect(html).toContain("缺少資訊");
    expect(html).toContain("導師檢查");
    expect(html).toContain("導師核查／無法執行");
    expect(html).toContain("check_circle");
    expect(html).toContain("warning_amber");
    expect(html.match(/cancel/g)).toHaveLength(2);
    expect(html.match(/detBadge_manual/g)).toHaveLength(2);
    expect(html).not.toContain("可執行取證");
    expect(html).not.toContain("導師人工審核");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('aria-label="展開第 1 項檢查設定"');
    expect(html.indexOf('aria-label="展開第 1 項檢查設定"')).toBeLessThan(html.indexOf('value="Python 版本檢查"'));
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
    expect(html).not.toContain("導師檢查");
  });
});

describe("getScriptCreationBlocker", () => {
  const completeItem = {
    id: "python-run",
    title: "執行 main.py",
    detectable: "auto",
    detection_method: "依 exit code 與 stdout 判定",
    check_steps: [{
      template_key: "python",
      command_key: "python.run_entrypoint",
      parameters: {
        cwd: "/home/student/project",
        argv: ["python3", "main.py"],
        timeout_seconds: 30,
        success_criteria: "exit code 為 0 且 stdout 等於 20",
      },
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
        parameters: {
          cwd: "/home/student/project",
          argv: ["python3", "main.py"],
          timeout_seconds: 30,
        },
      }],
    };

    expect(getScriptCreationBlocker({ analysis: { items: [teacherReviewItem] } })).toBeNull();
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
    const changed = {
      ...saved,
      items: [{ ...saved.items[0], title: "檢查 Python 版本" }],
    };

    expect(getRubricItemsValue(same)).toBe(getRubricItemsValue(saved));
    expect(getRubricItemsValue(changed)).not.toBe(getRubricItemsValue(saved));
  });

  test("只回傳實際變動的項目 ID，不把整張表標成待更新", () => {
    const savedItems = [
      { id: "item-1", title: "檢查版本", detection_method: "執行版本檢查", detectable: "auto" },
      { id: "item-2", title: "檢查輸出", detection_method: "讀取輸出內容", detectable: "partial" },
    ];
    const currentItems = [
      { ...savedItems[0], title: "檢查 Python 版本" },
      savedItems[1],
    ];

    expect([...getPendingRubricItemIds(currentItems, savedItems)]).toEqual(["item-1"]);
    expect([...getPendingRubricItemIds([savedItems[1]], savedItems)]).toEqual([]);
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
});

describe("uploaded rubric naming", () => {
  test("匯入檔名移除副檔名，且檢查名稱保留檔名主體並限制長度", () => {
    expect(getRubricDisplayName({ name: "AI檢查表審核系統_Python服務Running狀態檢測_簡短版.docx" }))
      .toBe("AI檢查表審核系統_Python服務Running狀態檢測_簡短版");
    expect(getRubricCheckTitle({ original_filename: "保存的檢查表.docx" })).toBe("保存的檢查表");
    expect(getRubricCheckTitle({ display_name: "自訂檢查表", original_filename: "保存的檢查表.docx" })).toBe("自訂檢查表");
    expect(getRubricCheckTitle({ name: "  " })).toBe("未命名檢查");
    expect(getRubricCheckTitle({ name: "a".repeat(300) })).toHaveLength(255);
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
    const title = "這是一個很長的 AI 檢查 session 名稱";
    const html = renderToStaticMarkup(<SessionTitle title={title}>{title}</SessionTitle>);

    expect(html).toContain('title="這是一個很長的 AI 檢查 session 名稱"');
    expect(html).toContain(title);
  });
});

describe("getSelectedRubricSource", () => {
  const files = [
    { id: "file-other", status: "active", display_name: "其他檢查" },
    { id: "file-selected", status: "active", display_name: "目前檢查" },
    { id: "file-replaced", status: "replaced", display_name: "已取代來源" },
  ];

  test("只回傳目前檢查選用的 active 來源", () => {
    expect(getSelectedRubricSource(files, "file-selected")).toEqual(files[1]);
    expect(getSelectedRubricSource(files, "file-other")).toEqual(files[0]);
  });

  test("沒有選用來源或來源已失效時不回傳其他班級來源", () => {
    expect(getSelectedRubricSource(files, null)).toBeNull();
    expect(getSelectedRubricSource(files, "file-replaced")).toBeNull();
  });
});

describe("resolveActiveSessionId", () => {
  const sessions = [{ id: "session-1" }, { id: "session-2" }];

  test("沒有目前選擇時保持未選取，不自動帶入第一筆檢查", () => {
    expect(resolveActiveSessionId(null, sessions)).toBeNull();
  });

  test("保留仍存在的選擇，清除已不存在的選擇", () => {
    expect(resolveActiveSessionId("session-2", sessions)).toBe("session-2");
    expect(resolveActiveSessionId("session-missing", sessions)).toBeNull();
  });
});

describe("script creation workflow", () => {
  test("通過自動檢查後進入執行結果，失敗時進入腳本總覽", () => {
    expect(getScriptCreationDestination({ status: "approved" })).toBe("execution");
    expect(getScriptCreationDestination({ status: "review_failed", id: "script-1" })).toBe("scripts");
  });
});
