// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import { quickPracticeKeys, useQuickPracticeTemplateQuery, useQuickPracticeTemplatesQuery } from "./quickPractice";

const mocks = vi.hoisted(() => ({ listTemplates: vi.fn(), getTemplate: vi.fn() }));
vi.mock("../services/quickPractice", () => ({
  QuickPracticeService: { listTemplates: mocks.listTemplates, getTemplate: mocks.getTemplate },
}));

let host;
let root;
let seen;

/* 結果物件是存取追蹤的：欄位要在 render 期間讀過才會重繪 */
function Probe({ templateId }) {
  const templates = useQuickPracticeTemplatesQuery();
  const template = useQuickPracticeTemplateQuery(templateId);
  seen = {
    templates: templates.data, templatesError: templates.isError,
    template: template.data, templatePending: template.isPending, templateFetching: template.isFetching,
  };
  return null;
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("quickPractice queries", () => {
  test("keys are stable and the template key is per environment", () => {
    expect(quickPracticeKeys.template(7)).toEqual(["quickPractice", "template", "7"]);
    expect(quickPracticeKeys.template("7")).toEqual(quickPracticeKeys.template(7));
  });

  test("loads the template list with an abort signal and a single template by id", async () => {
    mocks.listTemplates.mockResolvedValue([{ id: "1", name: "Tiny" }]);
    mocks.getTemplate.mockResolvedValue({ id: "7", name: "Seven" });
    await act(async () => { root.render(withQueryClient(<Probe templateId="7" />)); });
    await flush();

    expect(mocks.listTemplates).toHaveBeenCalledWith({ signal: expect.any(AbortSignal) });
    expect(mocks.getTemplate).toHaveBeenCalledWith("7");
    expect(seen.templates).toEqual([{ id: "1", name: "Tiny" }]);
    expect(seen.template).toEqual({ id: "7", name: "Seven" });
  });

  test("the single-template query stays idle without an id", async () => {
    mocks.listTemplates.mockResolvedValue([]);
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<Probe templateId={undefined} />, client)); });
    await flush();

    expect(mocks.getTemplate).not.toHaveBeenCalled();
    expect(seen.template).toBeUndefined();
    expect(seen.templateFetching).toBe(false);
  });

  test("a failed list exposes isError without data", async () => {
    mocks.listTemplates.mockRejectedValue(new Error("503"));
    await act(async () => { root.render(withQueryClient(<Probe templateId={undefined} />)); });
    await flush();

    expect(seen.templatesError).toBe(true);
    expect(seen.templates).toBeUndefined();
  });
});
