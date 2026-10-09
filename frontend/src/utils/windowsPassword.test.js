import { describe, expect, it } from "vitest";
import { windowsPasswordIssues } from "./windowsPassword";

describe("windowsPasswordIssues", () => {
  it.each(["Passw0rd", "student123!", "STUDENT123!", "Student!!", "學生Pass12", "abc 123 XYZ"])(
    "accepts %s (three or more categories)",
    (password) => {
      expect(windowsPasswordIssues(password)).toEqual([]);
    },
  );

  it.each(["password1", "PASSWORD", "12345678", "abcdefgh!"])(
    "rejects %s (fewer than three categories)",
    (password) => {
      expect(windowsPasswordIssues(password)).toEqual(["windowsCategories"]);
    },
  );

  it("rejects passwords containing the account name, case-insensitively", () => {
    expect(windowsPasswordIssues("MyAdmin#2026")).toEqual(["windowsUsername"]);
    expect(windowsPasswordIssues("xxADMINxx9!")).toEqual(["windowsUsername"]);
  });

  it("requires at least 8 characters", () => {
    expect(windowsPasswordIssues("Ab1!")).toEqual(["length"]);
    expect(windowsPasswordIssues(undefined)).toEqual(["length", "windowsCategories"]);
  });
});
