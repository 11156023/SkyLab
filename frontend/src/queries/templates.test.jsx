// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  TEMPLATE_BUSY_POLL_INTERVAL,
  templatesPollInterval,
  useTemplateAttachmentsQuery,
  useTemplateCacheUpdater,
  useTemplatesQuery,
} from "./templates";

const mocks = vi.hoisted(() => ({ list: vi.fn(), listAttachments: vi.fn() }));
vi.mock("../services/templates", () => ({
  TemplatesService: { list: mocks.list, listAttachments: mocks.listAttachments },
}));

let host;
let root;
let seen;

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪 */
function ListProbe() {
  const query = useTemplatesQuery();
  const updater = useTemplateCacheUpdater();
  seen = { data: query.data, isError: query.isError, updater };
  return null;
}

function AttachmentsProbe({ templateId }) {
  const query = useTemplateAttachmentsQuery(templateId);
  const updater = useTemplateCacheUpdater();
  seen = { data: query.data, fetchStatus: query.fetchStatus, refetch: query.refetch, updater };
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

describe("templatesPollInterval", () => {
  test("polls every 4 seconds while a template is creating or updating, otherwise 30 seconds", () => {
    expect(templatesPollInterval([{ status: "ready" }])).toBe(30_000);
    expect(templatesPollInterval([{ status: "ready" }, { status: "creating" }])).toBe(TEMPLATE_BUSY_POLL_INTERVAL);
    expect(templatesPollInterval([{ status: "updating" }])).toBe(TEMPLATE_BUSY_POLL_INTERVAL);
    expect(templatesPollInterval(undefined)).toBe(30_000);
  });
});

describe("useTemplatesQuery", () => {
  test("caches the template array (not the { data } envelope) so update() can patch a row", async () => {
    mocks.list.mockResolvedValue({ data: [{ id: "t1", status: "ready" }, { id: "t2", status: "ready" }] });
    await act(async () => { root.render(withQueryClient(<ListProbe />)); });
    await flush();
    expect(seen.data).toEqual([{ id: "t1", status: "ready" }, { id: "t2", status: "ready" }]);

    await act(async () => { seen.updater.update({ id: "t2", status: "updating" }); });
    await flush();
    expect(seen.data).toEqual([{ id: "t1", status: "ready" }, { id: "t2", status: "updating" }]);
    expect(mocks.list).toHaveBeenCalledTimes(1);
  });
});

describe("useTemplateAttachmentsQuery + attachment updaters", () => {
  test("addAttachment()/removeAttachment() patch the cached list without refetching", async () => {
    mocks.listAttachments.mockResolvedValue({ data: [{ id: "a" }] });
    await act(async () => { root.render(withQueryClient(<AttachmentsProbe templateId="tpl-1" />)); });
    await flush();
    expect(mocks.listAttachments).toHaveBeenCalledWith("tpl-1");
    expect(seen.data).toEqual([{ id: "a" }]);

    await act(async () => { await seen.updater.addAttachment("tpl-1", { id: "b" }); });
    await act(async () => { await seen.updater.removeAttachment("tpl-1", "a"); });
    await flush();
    expect(seen.data).toEqual([{ id: "b" }]);
    expect(mocks.listAttachments).toHaveBeenCalledTimes(1);
  });

  test("an in-flight refetch is cancelled so it cannot drop a just-uploaded attachment", async () => {
    let resolveSecond;
    mocks.listAttachments
      .mockResolvedValueOnce({ data: [{ id: "a" }] })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<AttachmentsProbe templateId="tpl-1" />, client)); });
    await flush();

    await act(async () => { seen.refetch(); });
    await act(async () => { await seen.updater.addAttachment("tpl-1", { id: "b" }); });
    await act(async () => { resolveSecond({ data: [{ id: "a" }] }); });
    await flush();
    expect(seen.data).toEqual([{ id: "a" }, { id: "b" }]);
  });

  test("an upload before the first load lands triggers a fresh load instead of patching", async () => {
    let resolveFirst;
    mocks.listAttachments
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ data: [{ id: "a" }, { id: "b" }] });
    await act(async () => { root.render(withQueryClient(<AttachmentsProbe templateId="tpl-1" />)); });

    await act(async () => { await seen.updater.addAttachment("tpl-1", { id: "b" }); });
    await act(async () => { resolveFirst({ data: [{ id: "a" }] }); });
    await flush();
    expect(mocks.listAttachments).toHaveBeenCalledTimes(2);
    expect(seen.data).toEqual([{ id: "a" }, { id: "b" }]);
  });
});
