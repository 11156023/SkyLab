/**
 * AiFloatingChat.test.jsx
 * 助手的純函式：問題交給哪個能力、流程走到哪一步、配置怎麼講給人聽。
 */

import { describe, expect, test } from "vitest";
import { describePlan, routeQuestion, stepStatuses, indexableSurfaces } from "./AiFloatingChat";

const STEPS = [
  { path: "/my-resources", status: "current" },
  { path: "/reverse-proxy", status: "todo" },
  { path: "/firewall", status: "todo" },
];

describe("routeQuestion", () => {
  test("整件事的描述交給導覽，才走得到流程", () => {
    expect(routeQuestion("我要申請一台機器")).toBe("navigate");
    expect(routeQuestion("如何把網站公開出去")).toBe("navigate");
    expect(routeQuestion("幫我開一個班級")).toBe("navigate");
  });

  test("原本的導覽句型仍然成立", () => {
    expect(routeQuestion("帶我到我的資源")).toBe("navigate");
    expect(routeQuestion("申請審核在哪裡")).toBe("navigate");
  });

  test("問規格與選型交給推薦，回來的是可以直接填的配置", () => {
    expect(routeQuestion("推薦我一個規格")).toBe("recommend");
    expect(routeQuestion("我該申請 LXC 還是 VM？")).toBe("recommend");
    expect(routeQuestion("跑深度學習要幾核心？")).toBe("recommend");
    expect(routeQuestion("記憶體要開多大")).toBe("recommend");
  });

  test("其餘問題走一般問答", () => {
    expect(routeQuestion("什麼是 LXC")).toBe("chat");
    expect(routeQuestion("謝謝")).toBe("chat");
  });
});

describe("describePlan", () => {
  test("把配置講成人看得懂的幾行", () => {
    expect(describePlan({
      resource_type: "vm",
      cores: 8,
      memory_mb: 16384,
      disk_gb: 100,
      gpu_mapping_id: "gpu-a",
      mode: "immediate",
    })).toEqual([
      "類型：虛擬機",
      "規格：8 核心 · 16.0 GB RAM · 100 GB 硬碟",
      "GPU：gpu-a",
      "時段：立即使用",
    ]);
  });

  test("沒有的欄位就不要生出一行「-」", () => {
    expect(describePlan({ resource_type: "lxc" })).toEqual(["類型：LXC 容器"]);
    expect(describePlan()).toEqual([]);
  });
});

describe("stepStatuses", () => {
  test("開啟頁面不代表前面的工作已完成", () => {
    expect(stepStatuses(STEPS, "/reverse-proxy")).toEqual(["current", "todo", "todo"]);
    expect(stepStatuses(STEPS, "/firewall")).toEqual(["current", "todo", "todo"]);
  });

  test("目前頁面不在流程裡時，沿用後端算好的狀態", () => {
    expect(stepStatuses(STEPS, "/account")).toEqual(["current", "todo", "todo"]);
  });

  test("配置產生後不會倒退回規劃那一步", () => {
    const flow = [
      { path: "/my-requests", status: "current", action: "recommend" },
      { path: "/my-requests", status: "todo" },
      { path: "/my-resources", status: "todo" },
    ];
    // 人還停在 /my-requests，但規劃已經做完了：目前進度應該是「填申請單」
    expect(stepStatuses(flow, "/my-requests", 1)).toEqual(["done", "current", "todo"]);
    // 還沒規劃時，同一頁的進度就是第一步
    expect(stepStatuses(flow, "/my-requests")).toEqual(["current", "todo", "todo"]);
  });

  test("後端也沒標記時，原樣顯示而不是全部歸零", () => {
    const noCurrent = [
      { path: "/a", status: "todo" },
      { path: "/b", status: "todo" },
    ];
    expect(stepStatuses(noCurrent, "/zzz")).toEqual(["todo", "todo"]);
  });
});

describe("routeQuestion 的畫面說明分流", () => {
  test("問欄位與問頁面都交給畫面說明", () => {
    expect(routeQuestion("這格要填什麼？")).toBe("help");
    expect(routeQuestion("這頁在做什麼")).toBe("help");
    expect(routeQuestion("為什麼不能送出")).toBe("help");
  });

  test("問規格仍然走配置規劃", () => {
    expect(routeQuestion("推薦我一個規格")).toBe("recommend");
  });

  test("問路仍然走導覽", () => {
    expect(routeQuestion("帶我去防火牆")).toBe("navigate");
  });
});

describe("全站問題與畫面問題要分得開", () => {
  test("問整個平台有哪些功能 → 功能索引", () => {
    expect(routeQuestion("有哪些功能可以使用？")).toBe("index");
    expect(routeQuestion("有什麼功能")).toBe("index");
    expect(routeQuestion("這個系統可以做什麼")).toBe("index");
    expect(routeQuestion("平台怎麼用")).toBe("index");
  });

  test("指著畫面問就還是畫面說明，即使句子裡有「系統」", () => {
    expect(routeQuestion("系統設定這頁在做什麼")).toBe("help");
    expect(routeQuestion("這格要填什麼")).toBe("help");
  });

  test("要做某件事仍然走流程導覽，不會被說明攔截", () => {
    expect(routeQuestion("我要申請一台機器")).toBe("navigate");
    expect(routeQuestion("我想開一個班級")).toBe("navigate");
    expect(routeQuestion("怎麼把服務公開出去")).toBe("navigate");
    expect(routeQuestion("帶我去防火牆")).toBe("navigate");
  });
});

describe("indexableSurfaces", () => {
  const SURFACES = [
    { id: "my-requests", path: "/my-requests", title: "我的申請" },
    { id: "request-form", path: "/my-requests", title: "申請虛擬機 / 容器" },
    { id: "resource-detail", path: "/my-resources/:vmid", title: "資源詳細" },
    { id: "jobs", path: "/jobs", title: "背景任務" },
  ];

  test("同一個路徑只留一個，帶參數的路徑不列", () => {
    expect(indexableSurfaces(SURFACES).map((s) => s.id)).toEqual([
      "my-requests",
      "jobs",
    ]);
  });

  test("清單還沒載到時回空陣列，交給下一個能力", () => {
    expect(indexableSurfaces(undefined)).toEqual([]);
  });
});

describe("流程與說明的優先順序", () => {
  test("提到流程或步驟一律走導覽，即使句子裡有「說明」", () => {
    expect(routeQuestion("說明 AI API 申請流程")).toBe("navigate");
    expect(routeQuestion("申請機器的完整流程")).toBe("navigate");
    expect(routeQuestion("開班步驟")).toBe("navigate");
  });

  test("問某個東西的用途走畫面說明", () => {
    expect(routeQuestion("說明防火牆規則的用途")).toBe("help");
    expect(routeQuestion("說明資源監控用途")).toBe("help");
  });

  test("一般知識問題不會被畫面說明攔截", () => {
    expect(routeQuestion("說明 LXC 與 VM 的差異")).not.toBe("help");
  });
});
