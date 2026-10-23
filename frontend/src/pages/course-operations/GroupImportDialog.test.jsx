// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

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

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function render(props = {}) {
  const onImport = vi.fn();
  await act(async () => {
    root.render(<GroupImportDialog closing={false} busy={false} onClose={() => {}} onImport={onImport} {...props} />);
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
  mocks.list.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
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
