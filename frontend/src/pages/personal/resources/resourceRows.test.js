// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { isRowBackgroundClick, resourceRowKey } from "./resourceRows";

describe("resourceRowKey", () => {
  it("keeps the same key when a machine moves in the list", () => {
    const vm = { type: "qemu", node: "pve1", vmid: 105, name: "lab" };
    expect(resourceRowKey(vm, 0)).toBe(resourceRowKey(vm, 3));
  });

  it("keeps the key when a machine migrates to another node", () => {
    expect(resourceRowKey({ type: "qemu", node: "pve1", vmid: 105 }, 2))
      .toBe(resourceRowKey({ type: "qemu", node: "pve2", vmid: 105 }, 2));
  });

  it("gives placeholders without a vmid distinct, stable keys by request id", () => {
    const a = { is_placeholder: true, vmid: null, request_id: 11, name: "same" };
    const b = { is_placeholder: true, vmid: null, request_id: 12, name: "same" };
    expect(resourceRowKey(a, 0)).not.toBe(resourceRowKey(b, 1));
    expect(resourceRowKey(a, 0)).toBe(resourceRowKey(a, 5));
  });

  it("falls back to the index only when there is no identity", () => {
    const anon = { type: "qemu", name: "x" };
    expect(resourceRowKey(anon, 0)).not.toBe(resourceRowKey(anon, 1));
  });
});

describe("isRowBackgroundClick", () => {
  function setup() {
    const table = document.createElement("table");
    const tbody = document.createElement("tbody");
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    const button = document.createElement("button");
    cell.append(button);
    row.append(cell);
    tbody.append(row);
    table.append(tbody);
    /* 選單用 portal 掛在 body，DOM 上不是列的子孫 */
    const portalMenu = document.createElement("div");
    const menuTitle = document.createElement("div");
    portalMenu.append(menuTitle);
    document.body.append(table, portalMenu);
    return { row, cell, button, menuTitle };
  }

  it("accepts clicks on the row itself and ignores its interactive elements", () => {
    const { row, cell, button } = setup();
    expect(isRowBackgroundClick({ target: cell, currentTarget: row })).toBe(true);
    expect(isRowBackgroundClick({ target: button, currentTarget: row })).toBe(false);
  });

  it("ignores clicks that bubbled up from a portaled menu", () => {
    const { row, menuTitle } = setup();
    expect(isRowBackgroundClick({ target: menuTitle, currentTarget: row })).toBe(false);
  });

  it("honours a narrower interactive selector for group rows", () => {
    const { row, cell } = setup();
    const link = document.createElement("a");
    cell.append(link);
    expect(isRowBackgroundClick({ target: link, currentTarget: row }, "button")).toBe(true);
  });
});
