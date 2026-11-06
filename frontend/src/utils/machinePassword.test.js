import { describe, expect, test } from "vitest";
import { machinePasswordIssues, machinePasswordRules } from "./machinePassword";

describe("machinePasswordRules", () => {
  test("一般機器只有長度與不含空白兩條", () => {
    expect(machinePasswordRules().map((rule) => rule.key)).toEqual(["length", "noWhitespace"]);
  });

  test("Windows 機器加上字元種類與帳號名稱，長度不重複列", () => {
    expect(machinePasswordRules({ windows: true }).map((rule) => rule.key))
      .toEqual(["length", "noWhitespace", "windowsCategories", "windowsUsername"]);
  });
});

describe("machinePasswordIssues", () => {
  test("同後端：8–64 字、不含空白", () => {
    expect(machinePasswordIssues("abcdefgh")).toEqual([]);
    expect(machinePasswordIssues("abc")).toEqual(["length"]);
    expect(machinePasswordIssues("a".repeat(65))).toEqual(["length"]);
    expect(machinePasswordIssues("abcd efgh")).toEqual(["noWhitespace"]);
    /* 還沒輸入時「不含空白」也不打勾 */
    expect(machinePasswordIssues("")).toEqual(["length", "noWhitespace"]);
  });

  test("Windows 另外要三類字元、不含帳號名稱", () => {
    expect(machinePasswordIssues("abcdefg1", { windows: true })).toEqual(["windowsCategories"]);
    expect(machinePasswordIssues("Abcdefg1", { windows: true })).toEqual([]);
    expect(machinePasswordIssues("Admin-pw1", { windows: true })).toEqual(["windowsUsername"]);
  });
});
