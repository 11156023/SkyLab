import { describe, expect, it } from "vitest";
import {
  WINDOWS_PASSWORD_RULES,
  windowsCategoryShortfall,
  windowsPasswordIssues,
} from "./windowsPassword";

const categoriesRule = WINDOWS_PASSWORD_RULES.find((rule) => rule.key === "windowsCategories");

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

describe("windowsCategoryShortfall", () => {
  it("counts how many more types are needed and which ones are unused", () => {
    expect(windowsCategoryShortfall("password1")).toEqual({
      need: 1,
      missing: ["uppercase", "symbol"],
    });
    expect(windowsCategoryShortfall("password")).toEqual({
      need: 2,
      missing: ["uppercase", "digit", "symbol"],
    });
    expect(windowsCategoryShortfall("Passw0rd")).toEqual({ need: 0, missing: ["symbol"] });
  });

  it("counts caseless letters as their own type without suggesting them", () => {
    expect(windowsCategoryShortfall("學生pass")).toEqual({
      need: 1,
      missing: ["uppercase", "digit", "symbol"],
    });
  });
});

describe("windowsCategories rule describe", () => {
  it("stays hidden until something is typed", () => {
    expect(categoriesRule.describe("")).toBeNull();
  });

  it("only suggests the types that are still missing", () => {
    expect(categoriesRule.describe("password1")).toEqual({
      key: "windowsAddOneType",
      options: ["windowsType.uppercase", "windowsType.symbol"],
    });
    expect(categoriesRule.describe("password")).toEqual({
      key: "windowsAddTwoTypes",
      options: ["windowsType.uppercase", "windowsType.digit", "windowsType.symbol"],
    });
  });

  it("reports enough types once three are present", () => {
    expect(categoriesRule.describe("Passw0rd")).toEqual({ key: "windowsCategoriesMet" });
  });
});
