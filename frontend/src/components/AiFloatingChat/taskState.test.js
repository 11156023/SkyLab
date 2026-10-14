import { describe, expect, test } from "vitest";
import { routeQuestion, stepStatuses } from "./AiFloatingChat";
import { newTask, withTaskMemory, recentMessages, intakeFactsPayload, planErrorKey, requestIsReady, markStep, shouldContinueIntake, rememberWorkflow, addWorkflows, environmentStepPath, flowOwnsPath } from "./taskState";

test("environment steps enter the editor and preserve draft identity and return destination", () => {
  expect(environmentStepPath("/course-template-management", undefined, "basic")).toBe("/course-template-management/new?tab=basic");
  const path = "/course-template-management/env-42?returnTo=%2Fclass-setup%3FclassId%3D9%26step%3D3&tab=basic";
  const target = environmentStepPath(path, undefined, "machines");
  expect(target.split("?")[0]).toBe("/course-template-management/env-42");
  expect(new URLSearchParams(target.split("?")[1]).get("returnTo")).toBe("/class-setup?classId=9&step=3");
  expect(new URLSearchParams(target.split("?")[1]).get("tab")).toBe("machines");
  expect(environmentStepPath("/templates", target, "basic")).toContain("/course-template-management/env-42?");
  const fromClass = environmentStepPath("/class-setup?classId=9&step=3", undefined, "basic");
  expect(new URLSearchParams(fromClass.split("?")[1]).get("returnTo")).toBe("/class-setup?classId=9&step=3");
  expect(flowOwnsPath({ id: "prepare_environment" }, "/course-template-management/env-42")).toBe(true);
  expect(flowOwnsPath({ id: "prepare_environment" }, "/course-template-management")).toBe(false);
});

test("teaching comparisons and multiple workflows are not machine recommendations", () => {
  for (const text of ["教學環境和班級有什麼差別", "先建立範本還是班級？", "幫我建立班級並推薦教學環境"])
    expect(routeQuestion(text)).toBe("navigate");
  expect(routeQuestion("LXC 與 VM 的差別？", newTask(), "open_class")).toBe("chat");
  expect(routeQuestion("好", newTask(), "open_class", true)).toBe("continueTask");
  for (const text of ["建立課程", "建立課堂", "建立班級", "建立環境", "建立教學環境"]) {
    expect(routeQuestion(text, { ...newTask(), stage: "collecting" }, "prepare_environment")).toBe("navigate");
  }
});

test("side questions leave the intake question pending", () => {
  const task = { ...newTask(), stage: "collecting", pendingKey: "duration" };
  for (const text of ["順便問什麼是 DNS？", "GPU 為什麼比較快？"]) {
    const route = routeQuestion(text, task);
    expect(route).toBe("chat");
    expect(shouldContinueIntake(text, task, route)).toBe(false);
  }
  expect(shouldContinueIntake("幾週", task, "chat")).toBe(true);
  expect(task.pendingKey).toBe("duration");
});

test("adding another workflow preserves saved facts and resume URL", () => {
  const workflows = new Map();
  const flow = { id: "open_class", resumePath: "/class-setup?classId=42&step=3", steps: [] };
  const task = { ...newTask(), goal: "開班" };
  rememberWorkflow(workflows, flow, task, null);
  addWorkflows(workflows, [
    { flow_id: "open_class", steps: [{ title: "不要重設" }] },
    { flow_id: "share_template", flow_title: "建立範本", steps: [] },
  ], "另外做範本");
  expect(workflows.size).toBe(2);
  expect(workflows.get("open_class").flow.resumePath).toContain("classId=42");
  expect(workflows.get("open_class").task.goal).toBe("開班");
  expect(workflows.get("share_template").task).not.toBe(task);
});

describe("task continuity", () => {
  test("missing machine and fill requests trigger actual planning", () => {
    expect(routeQuestion("我沒有機器")).toBe("recommend");
    expect(routeQuestion("幫我填")).toBe("recommend");
    expect(routeQuestion("好", { ...newTask(), stage: "collecting" })).toBe("recommend");
    expect(routeQuestion("好", newTask(), "request_machine")).toBe("recommend");
    expect(routeQuestion("好", newTask())).toBe("chat");
  });

  test("acceptance after filling or submitting checks the next action instead of planning again", () => {
    for (const stage of ["planned", "filled", "submitted"]) {
      expect(routeQuestion("好", { ...newTask(), stage })).toBe("continueTask");
    }
    expect(routeQuestion("取消", { ...newTask(), stage: "collecting" })).toBe("cancel");
    expect(routeQuestion("這格要填什麼？", { ...newTask(), stage: "collecting" })).toBe("help");
  });

  test("planner keeps the original goal and actual answers after long conversations", () => {
    const history = Array.from({ length: 20 }, () => ({ role: "assistant", content: "舊對話" }));
    history.push({ role: "user", content: "幾週" });
    const task = { ...newTask(), goal: "Node.js 網站上線", facts: {
      purpose: "Node.js 網站", gpu: "不需要 GPU", duration: "幾週", inferred: ["gpu"],
    } };
    const recent = withTaskMemory(history, task).slice(-10);
    expect(recent.at(-1).content).toContain("Node.js 網站上線");
    expect(recent.at(-1).content).toContain("不需要 GPU");
    expect(recent.at(-1).content).toContain("建議預設，可調整");
    expect(recent.at(-1).content).toContain("本輪要求：幾週");
    expect(history).toHaveLength(21);
  });
});

describe("verified progress", () => {
  const steps = [
    { path: "/my-requests", status: "current" },
    { path: "/my-requests", status: "todo" },
    { path: "/my-requests", status: "todo" },
    { path: "/my-resources", status: "todo" },
  ];
  test("empty resource page cannot mark request, review or provisioning complete", () => {
    expect(stepStatuses(steps)).toEqual(["current", "todo", "todo", "todo"]);
    expect(stepStatuses(markStep(steps, 1))).toEqual(["done", "current", "todo", "todo"]);
    expect(stepStatuses(markStep(steps, 2))).toEqual(["done", "done", "current", "todo"]);
  });
  test("approval alone is not a usable machine; match the provisioned VM to the request", () => {
    const request = { status: "approved", provisioning_status: "completed", vmid: 101 };
    expect(requestIsReady(request, [{ vmid: 101 }])).toBe(true);
    expect(requestIsReady(request, [{ vmid: 102 }])).toBe(false);
    expect(requestIsReady({ ...request, status: "pending" }, [{ vmid: 101 }])).toBe(false);
    expect(requestIsReady({ ...request, provisioning_status: "running" }, [{ vmid: 101 }])).toBe(false);
    expect(requestIsReady({ ...request, provisioning_status: "failed" }, [{ vmid: 101 }])).toBe(false);
    expect(requestIsReady(undefined, [{ vmid: 101 }])).toBe(false);
  });
});

describe("configuration questions get answers instead of a new plan", () => {
  test("knowledge and inventory questions go to chat", () => {
    for (const text of ["LXC 跟 VM 差別？", "4GB 記憶體夠嗎？", "Ubuntu 還是 Debian 比較好", "有哪些 GPU 可以用", "GPU 有哪些型號？"]) {
      expect(routeQuestion(text)).toBe("chat");
    }
  });

  test("asking us to act still plans", () => {
    expect(routeQuestion("我該申請 LXC 還是 VM？")).toBe("recommend");
    expect(routeQuestion("推薦我一個規格")).toBe("recommend");
    expect(routeQuestion("記憶體要開多大")).toBe("recommend");
  });

  test("inventory questions during intake are not taken as answers", () => {
    const task = { ...newTask(), stage: "collecting", pendingKey: "gpu" };
    const route = routeQuestion("有哪些 GPU 可以用", task);
    expect(route).toBe("chat");
    expect(shouldContinueIntake("有哪些 GPU 可以用", task, route)).toBe(false);
  });
});

describe("planner request payload", () => {
  test("long conversations are trimmed to the most recent messages", () => {
    const history = Array.from({ length: 60 }, (_, index) => ({ role: "user", content: `第 ${index} 則` }));
    const kept = recentMessages(history);
    expect(kept).toHaveLength(12);
    expect(kept.at(-1).content).toBe("第 59 則");
    const huge = [{ role: "user", content: "x".repeat(20_000) }, { role: "user", content: "最新" }];
    expect(recentMessages(huge).map((item) => item.content)).toEqual(["最新"]);
  });

  test("answered intake facts are sent as structured fields", () => {
    expect(intakeFactsPayload(newTask())).toBeNull();
    const task = { ...newTask(), facts: { purpose: "AI 訓練", gpu: "不確定，你幫我判斷", inferred: [] } };
    expect(intakeFactsPayload(task)).toEqual({
      purpose: "AI 訓練", gpu: "不確定，你幫我判斷", display: null, duration: null, inferred: [],
    });
  });

  test("plan errors explain what the user can do", () => {
    expect(planErrorKey({ status: 408, timeout: true })).toBe("AiFloatingChat.planTimedOut");
    expect(planErrorKey({ status: 429 })).toBe("AiFloatingChat.planRateLimited");
    expect(planErrorKey({ status: 400 })).toBe("AiFloatingChat.planTooLong");
    expect(planErrorKey({ status: 502 })).toBe("AiFloatingChat.planFailed");
  });
});
