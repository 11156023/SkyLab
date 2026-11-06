// @vitest-environment happy-dom

/*
 * 範本列的「⋯」選單：編輯／更新循環／重新轉換／刪除只有擁有者與管理員做得到（後端 require_template_owner）。
 * 2026-11-06 ntub 實測：老師看別人（管理員）建的全域範本，選單裡照樣有「刪除」，按下去才 403。
 * 別人的範本只留「克隆」與「使用手冊」。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { withQueryClient } from "../../../queries/testing";
import TemplatesPage from "./TemplatesPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
  auth: { user: { id: "teacher-1", role: "teacher" } },
}));

vi.mock("../../../services/templates", () => ({
  TemplatesService: {
    list: mocks.list,
    listAttachments: vi.fn(),
    downloadAttachment: vi.fn(),
    startUpdateCycle: vi.fn(),
    finishUpdateCycle: vi.fn(),
    cancelUpdateCycle: vi.fn(),
    retry: vi.fn(),
    remove: vi.fn(),
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => mocks.auth }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("./TemplateFormDialog", () => ({ default: () => null }));
vi.mock("./TemplateCloneDialog", () => ({ default: () => null }));

const template = (overrides) => ({
  id: 1,
  name: "ubuntu-base",
  status: "ready",
  resource_type: "qemu",
  visibility: "global",
  version: 1,
  vmid: 9001,
  attachment_count: 1,
  owner_id: "teacher-1",
  ...overrides,
});

let root;
let host;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.auth.user = { id: "teacher-1", role: "teacher" };
  notifyManager.setScheduler((cb) => cb());
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  notifyManager.setScheduler((cb) => setTimeout(cb, 0));
});

const menuButtonOf = (name) =>
  [...host.querySelectorAll("tr")].find((tr) => tr.textContent.includes(name)).querySelector('[data-guide="templates-menu"]');

async function openMenuOf(name) {
  await act(async () => { menuButtonOf(name).click(); });
  await flush();
  /* 選單以 portal 掛在 body；按鈕文字前面帶 Material 圖示的 ligature 名稱，只取 i18n key */
  return [...document.body.querySelectorAll("button")]
    .map((b) => b.textContent.match(/TemplatesPage\.menu\w+/)?.[0])
    .filter(Boolean);
}

/* 「⋯」再按一次就收起；等離場動畫（130ms）播完才真的卸載 */
async function closeMenuOf(name) {
  await act(async () => { menuButtonOf(name).click(); });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 150)); });
}

describe("TemplatesPage：範本選單依擁有者顯示管理動作", () => {
  test("老師看自己的範本：完整選單；看別人的範本：只有克隆與使用手冊", async () => {
    mocks.list.mockResolvedValue({
      data: [template({ id: 1, name: "mine", owner_id: "teacher-1" }), template({ id: 2, name: "theirs", owner_id: "admin-9" })],
    });
    await act(async () => { root.render(withQueryClient(<TemplatesPage />)); });
    await flush();

    const mine = await openMenuOf("mine");
    expect(mine).toEqual(expect.arrayContaining([
      "TemplatesPage.menuClone",
      "TemplatesPage.menuEdit",
      "TemplatesPage.menuManual",
      "TemplatesPage.menuStartCycle",
      "TemplatesPage.menuDelete",
    ]));

    await closeMenuOf("mine");
    const theirs = await openMenuOf("theirs");
    expect(theirs).toEqual(["TemplatesPage.menuClone", "TemplatesPage.menuManual"]);
  });

  test("管理員看任何人的範本都有完整選單", async () => {
    mocks.auth.user = { id: "admin-9", role: "admin" };
    mocks.list.mockResolvedValue({ data: [template({ id: 2, name: "theirs", owner_id: "teacher-1" })] });
    await act(async () => { root.render(withQueryClient(<TemplatesPage />)); });
    await flush();

    const items = await openMenuOf("theirs");
    expect(items).toContain("TemplatesPage.menuEdit");
    expect(items).toContain("TemplatesPage.menuDelete");
  });
});
