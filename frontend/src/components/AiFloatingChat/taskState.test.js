import { describe, expect, test } from "vitest";
import { routeQuestion, stepStatuses } from "./AiFloatingChat";
import { newTask, withTaskMemory, requestIsReady, markStep } from "./taskState";

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
    expect(stepStatuses(steps, "/my-resources")).toEqual(["current", "todo", "todo", "todo"]);
    expect(stepStatuses(markStep(steps, 1), "/my-resources")).toEqual(["done", "current", "todo", "todo"]);
    expect(stepStatuses(markStep(steps, 2), "/my-requests")).toEqual(["done", "done", "current", "todo"]);
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
