// @vitest-environment happy-dom
/**
 * 導覽示範頁：六個分頁都要畫得出來、版面照正式分頁（工具列按鈕、表格、進階設定卡片順序），
 * 而且整頁不打任何後端。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import ResourceDetailGuideDemo from "./ResourceDetailGuideDemo";

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: (key) => key, i18n: { language: "zh-TW" } }),
}));

let host, root, toolbar, fetchSpy;

async function render(tab, props = {}) {
  await act(async () => root.render(<ResourceDetailGuideDemo tab={tab} toolbar={toolbar} {...props} />));
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.reject(new Error("no network in demo")));
  host = document.createElement("div");
  toolbar = document.createElement("div");
  document.body.append(host, toolbar);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  toolbar.remove();
  fetchSpy.mockRestore();
});

describe("ResourceDetailGuideDemo", () => {
  test.each(["overview", "monitoring", "specifications", "snapshots", "auditLogs", "advanced"])(
    "%s 分頁畫得出來且不打後端",
    async (tab) => {
      await render(tab);
      expect(host.textContent).not.toBe("");
      expect(fetchSpy).not.toHaveBeenCalled();
    },
  );

  test("監控與快照的控制項放進分頁列右側的工具槽，換分頁就收掉", async () => {
    await render("monitoring");
    expect(toolbar.textContent).toContain("MonitoringTab.timeframeHour");

    await render("snapshots");
    expect(toolbar.textContent).toContain("SnapshotsTab.oneClickReset");
    expect(toolbar.textContent).toContain("SnapshotsTab.createSnapshot");

    await render("specifications");
    expect(toolbar.textContent).toBe("");
  });

  test("快照：受保護的初始快照只能還原，一般快照多一顆刪除", async () => {
    await render("snapshots");
    const rows = [...host.querySelectorAll("tbody tr")];
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("SnapshotsTab.protected");
    expect(rows[0].textContent).not.toContain("SnapshotsTab.delete");
    expect(rows[1].textContent).toContain("SnapshotsTab.delete");
  });

  test("操作紀錄的動作顯示翻譯文字，不是原始代碼", async () => {
    await render("auditLogs");
    expect(host.querySelectorAll("tbody tr")).toHaveLength(4);
    expect(host.textContent).toContain("AuditLogsTab.action.resource_start");
  });

  test("進階設定的卡片與正式頁同序：生命週期、防火牆、開機選項、登入憑證、共享與轉移", async () => {
    const onShowOverview = vi.fn();
    await render("advanced", { onShowOverview });
    const guides = [...host.querySelectorAll("[data-guide]")].map((el) => el.dataset.guide);
    expect(guides).toEqual([
      "resource-setting-lifecycle",
      "resource-setting-firewall",
      "resource-setting-boot",
      "resource-setting-credentials",
      "resource-setting-sharing",
    ]);

    const link = [...host.querySelectorAll("button")].find((b) => b.textContent === "CredentialsCard.passwordWhere");
    await act(async () => link.click());
    expect(onShowOverview).toHaveBeenCalledTimes(1);
  });
});
