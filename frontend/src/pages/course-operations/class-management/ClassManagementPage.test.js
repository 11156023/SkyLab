import { describe, expect, it } from "vitest";
import { classSetupResumeStep, setupChecklist } from "./ClassManagementPage";

const environment = { id: "env-1", name: "Linux 實務" };

describe("classSetupResumeStep", () => {
  it("讓未完成班級回到下一個尚未完成的建立步驟", () => {
    expect(classSetupResumeStep({ students: 0, nodes: [], weeks: [] })).toBe(2);
    expect(classSetupResumeStep({ students: 12, nodes: [], weeks: [] })).toBe(3);
    expect(classSetupResumeStep({
      students: 12,
      nodes: [{ id: "main" }],
      weeks: [],
      course_environment: environment,
    })).toBe(4);
    expect(classSetupResumeStep({
      students: 12,
      nodes: [{ id: "main" }],
      weeks: [{ title: "Linux 權限" }],
      course_environment: environment,
    })).toBe(5);
  });

  it("沒有課程環境時不算完成環境設定，即使機器節點還留著", () => {
    expect(classSetupResumeStep({ students: 12, nodes: [{ id: "main" }], weeks: [] })).toBe(3);
  });
});

describe("setupChecklist", () => {
  it("與 classSetupResumeStep 共用同一份完成度定義", () => {
    expect(setupChecklist({ students: 0, nodes: [], weeks: [] })).toEqual([false, false, false]);
    expect(setupChecklist({
      students: 12,
      nodes: [{ id: "main" }],
      weeks: [{ title: " " }],
      course_environment: environment,
    })).toEqual([true, true, false]);
    expect(setupChecklist({
      students: 12,
      nodes: [{ id: "main" }],
      weeks: [{ title: "Linux 權限" }],
      course_environment: environment,
    })).toEqual([true, true, true]);
  });
});
