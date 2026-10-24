// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../queries/testing";
import GroupImportDialog from "./GroupImportDialog";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  t: (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key),
}));

vi.mock("../../services/userGroups", () => ({ UserGroupsService: { list: mocks.list } }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

let root;
let host;
let client;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function render(props = {}) {
  const onImport = vi.fn();
  await act(async () => {
    root.render(withQueryClient(<GroupImportDialog closing={false} busy={false} onClose={() => {}} onImport={onImport} {...props} />, client));
  });
  await flush();
  return onImport;
}

function submitButton() {
  return [...document.querySelectorAll("button[type=submit]")][0];
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  client = createTestQueryClient();
  mocks.list.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
  document.body.innerHTML = "";
});

describe("GroupImportDialog", () => {
  test("preselects the first group that has students and imports it", async () => {
    const empty = { id: "g0", name: "空班", member_count: 1, student_count: 0 };
    const cls = { id: "g1", name: "資管三甲", member_count: 41, student_count: 40 };
    mocks.list.mockResolvedValue({ data: [empty, cls], count: 2 });

    const onImport = await render();

    expect(document.querySelector("select").value).toBe("g1");
    expect(document.body.textContent).toContain("GroupImportDialog.snapshotNote");
    await act(async () => { submitButton().click(); });
    expect(onImport).toHaveBeenCalledWith(cls);
  });

  test("imports the group the teacher picked", async () => {
    const a = { id: "g1", name: "資管三甲", member_count: 41, student_count: 40 };
    const b = { id: "g2", name: "資管三乙", member_count: 38, student_count: 38 };
    mocks.list.mockResolvedValue({ data: [a, b], count: 2 });

    const onImport = await render();
    const select = document.querySelector("select");
    await act(async () => {
      select.value = "g2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => { submitButton().click(); });
    expect(onImport).toHaveBeenCalledWith(b);
  });

  test("a group without students cannot be imported", async () => {
    mocks.list.mockResolvedValue({ data: [{ id: "g0", name: "老師群", member_count: 3, student_count: 0 }], count: 1 });

    await render();

    expect(document.body.textContent).toContain("GroupImportDialog.noStudents");
    expect(submitButton().disabled).toBe(true);
  });

  test("tells the teacher when no group exists yet", async () => {
    mocks.list.mockResolvedValue({ data: [], count: 0 });

    await render();

    expect(document.body.textContent).toContain("GroupImportDialog.empty");
    expect(document.querySelector("select")).toBeNull();
    expect(submitButton().disabled).toBe(true);
  });

  test("shows the load error instead of an empty list", async () => {
    mocks.list.mockRejectedValue(new Error("boom"));

    await render();

    expect(document.body.textContent).toContain("boom");
    expect(submitButton().disabled).toBe(true);
  });
});
