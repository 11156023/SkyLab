import { expect, it } from "vitest";
import {
  MAX_SHEETS, folderBackPath, folderFrontPath, folderSheets, folderTotals, formatGb, sheetRole,
} from "./templateFolder";

const machine = (name, extra = {}) => ({ id: name, name, role: name, cpu: 1, memory_mb: 1024, ...extra });

it("puts one sheet per machine, the first machine deepest in the folder", () => {
  const sheets = folderSheets([machine("kali"), machine("web"), machine("db")]);
  expect(sheets.map((sheet) => [sheet.node.name, sheet.depth])).toEqual([["kali", 2], ["web", 1], ["db", 0]]);
});

it("keeps the first three machines and counts the rest on the sheet by the flap", () => {
  const sheets = folderSheets(["lb", "web-1", "web-2", "web-3", "redis", "db"].map((name) => machine(name)));
  expect(sheets).toHaveLength(MAX_SHEETS);
  expect(sheets.slice(0, 3).map((sheet) => sheet.node.name)).toEqual(["lb", "web-1", "web-2"]);
  expect(sheets.at(-1)).toMatchObject({ more: 3, depth: 0 });
  expect(folderSheets([machine("a"), machine("b"), machine("c"), machine("d")]).every((sheet) => sheet.node)).toBe(true);
});

it("handles an environment without machines", () => {
  expect(folderSheets(undefined)).toEqual([]);
});

it("shows a role only when it adds something to the name", () => {
  expect(sheetRole({ name: "kali", role: "攻擊機" })).toBe("攻擊機");
  expect(sheetRole({ name: "redis", role: "redis" })).toBe("");
  expect(sheetRole({ name: "web", role: "  " })).toBe("");
});

it("prefers the API's per-student totals and falls back to adding up the machines", () => {
  expect(folderTotals({ per_student: { cpu_cores: 7, memory_mb: 13312 }, nodes: [] })).toEqual({ cpu: 7, memoryGb: 13 });
  expect(folderTotals({ nodes: [machine("a", { cpu: 2, memory_mb: 1536 }), machine("b")] })).toEqual({ cpu: 3, memoryGb: 2.5 });
  expect(formatGb(4096)).toBe(4);
});

it("draws closed outlines at the given size", () => {
  for (const path of [folderBackPath(300, 232), folderFrontPath(300, 120)]) {
    expect(path.startsWith("M")).toBe(true);
    expect(path.endsWith("Z")).toBe(true);
  }
  expect(folderBackPath(300, 232)).toContain("300 34"); // right edge turns down at the corner
  expect(folderFrontPath(300, 120)).toContain(" 120 "); // reaches the bottom
});
