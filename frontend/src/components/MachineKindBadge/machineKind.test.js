/**
 * machineKind.test.js
 * 後端的來源欄位要對到正確的徽章；班級機依「我是學生還是老師」分兩種。
 */

import { describe, expect, test } from "vitest";
import { KIND_META, resolveKind } from "./machineKind";

describe("resolveKind", () => {
  test("班級機依 class_relation 分成我的班級機器與學生機器", () => {
    expect(resolveKind({ kind: "teaching_class", classRelation: "student" })).toBe("class_student");
    expect(resolveKind({ kind: "teaching_class", classRelation: "teacher" })).toBe("class_teacher");
    /* 管理員看班級機沒有關係欄位，當成一般班級機器 */
    expect(resolveKind({ kind: "teaching_class", classRelation: null })).toBe("class_student");
  });

  test("其他來源直接對應，未知或缺欄位退回個人申請", () => {
    expect(resolveKind({ kind: "shared" })).toBe("shared");
    expect(resolveKind({ kind: "quick_practice" })).toBe("quick_practice");
    expect(resolveKind({ kind: "course" })).toBe("course");
    expect(resolveKind({ kind: "teacher_open" })).toBe("teacher_open");
    expect(resolveKind({ kind: "whatever" })).toBe("personal");
    expect(resolveKind({})).toBe("personal");
    expect(resolveKind()).toBe("personal");
  });

  test("只有共享、學生機器、老師開放三種會帶人名", () => {
    const withOwner = Object.entries(KIND_META).filter(([, m]) => m.showOwner).map(([k]) => k);
    expect(withOwner.sort()).toEqual(["class_teacher", "shared", "teacher_open"]);
  });

  test("每種徽章都有圖示、顏色變體與翻譯鍵", () => {
    for (const meta of Object.values(KIND_META)) {
      expect(meta.icon).toBeTruthy();
      expect(meta.variant).toBeTruthy();
      expect(meta.labelKey.startsWith("MachineKind.")).toBe(true);
      expect(meta.hintKey.startsWith("MachineKind.")).toBe(true);
    }
  });
});
