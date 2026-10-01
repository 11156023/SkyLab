import { describe, expect, test } from "vitest";
import {
  PASSWORD_MAX_LENGTH,
  PASSWORD_RULES,
  isPasswordStrong,
  passwordIssues,
} from "./passwordPolicy";

describe("passwordIssues", () => {
  test("四種字元齊全且長度足夠才通過", () => {
    expect(passwordIssues("Skylab#2026")).toEqual([]);
    expect(isPasswordStrong("Skylab#2026")).toBe(true);
  });

  test("逐項回報缺少的規則，順序與 PASSWORD_RULES 一致", () => {
    expect(passwordIssues("")).toEqual(PASSWORD_RULES.map((rule) => rule.key));
    expect(passwordIssues("Ab1!")).toEqual(["length"]);
    expect(passwordIssues("skylab#2026")).toEqual(["uppercase"]);
    expect(passwordIssues("SKYLAB#2026")).toEqual(["lowercase"]);
    expect(passwordIssues("Skylab#abcd")).toEqual(["digit"]);
    expect(passwordIssues("Skylab12026")).toEqual(["symbol"]);
    expect(passwordIssues("password")).toEqual(["uppercase", "digit", "symbol"]);
  });

  test("空白不算特殊符號，全形標點與非英文字母算", () => {
    expect(passwordIssues("Skylab 2026")).toEqual(["symbol"]);
    expect(passwordIssues("Skylab，2026")).toEqual([]);
    expect(passwordIssues("Skylab雲2026")).toEqual([]);
  });

  test("超過長度上限視為長度不符", () => {
    const tooLong = `Aa1!${"x".repeat(PASSWORD_MAX_LENGTH)}`;
    expect(passwordIssues(tooLong)).toEqual(["length"]);
  });

  test("null／undefined 當成空字串", () => {
    expect(isPasswordStrong(null)).toBe(false);
    expect(isPasswordStrong(undefined)).toBe(false);
  });
});
