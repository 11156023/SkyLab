// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

const toast = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => true }));
vi.mock("../../../contexts/UnsavedChangesContext", () => ({
  useUnsavedChanges: () => ({ confirmLeave: async () => true }),
  useUnsavedChangesGuard: () => {},
}));

import ClassSetupPage from "./ClassSetupPage";
import { withQueryClient } from "../../../queries/testing";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import i18n from "../../../i18n";

// en／ja 語系檔改為用到才載入；這裡要檢查三語都有 key，先全部載入
beforeAll(() => i18n.loadLanguages(["en", "ja"]));

/* i18n.t 找不到 key 時會回傳 key 本身，單純比對 t(key) 抓不到漏翻；先確認三個語系都有這個 key */
const tt = (key, options) => {
  for (const lng of ["zh-TW", "en", "ja"]) {
    expect(i18n.getResource(lng, "teaching", key), `${lng} teaching.json 缺少 ${key}`).toBeTypeOf("string");
  }
  return i18n.t(key, { ns: "teaching", ...options });
};

afterEach(() => {
  vi.restoreAllMocks();
  Object.values(toast).forEach((fn) => fn.mockClear());
});

/* TanStack Query 的通知走 setTimeout(0)：先等一個 timer tick 再清 microtask */
async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

function baseClass(overrides = {}) {
  return {
    id: "c1",
    name: "網路實務",
    start_date: "2026-09-07",
    end_date: "2027-01-10",
    weekday: 0,
    start_time: "13:10:00",
    end_time: "16:00:00",
    students: [],
    machine_nodes: [],
    weeks: [],
    ...overrides,
  };
}

let currentSearch = "";
function LocationProbe() {
  currentSearch = useLocation().search;
  return null;
}

async function mount(url) {
  window.scrollTo = vi.fn();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(withQueryClient(
      <MemoryRouter initialEntries={[url]}>
        <ClassSetupPage />
        <LocationProbe />
      </MemoryRouter>,
    ));
  });
  /* 畫面在上一個 act 結束時才掛上、開始抓資料；資料回來要另一個 act 等 TanStack Query 的通知 */
  await act(async () => {
    await flush();
    await flush();
  });
  return {
    container,
    cleanup: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

function typeInto(textarea, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
  act(() => {
    setter.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("ClassSetupPage 學生名單", () => {
  async function submitEmails(container, value) {
    typeInto(container.querySelector("textarea"), value);
    const nextButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes(tt("ClassSetupPage.saveAndNextBtn")));
    await act(async () => {
      nextButton.click();
      await flush();
      await flush();
    });
  }

  test("一位都沒加進來（全是非學生帳號）時停在第二步並說明原因", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(baseClass());
    const addStudents = vi.spyOn(TeachingClassesService, "addStudents").mockResolvedValue({
      added: 0,
      not_found: [],
      invalid_role: ["teacher@example.edu"],
      class: baseClass(),
    });

    const { container, cleanup } = await mount("/class-setup?classId=c1&step=2");
    await submitEmails(container, "teacher@example.edu");

    expect(addStudents).toHaveBeenCalledWith("c1", ["teacher@example.edu"]);
    expect(currentSearch).toContain("step=2");
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining(tt("ClassSetupPage.invalidRoleList", { list: "teacher@example.edu" })));
    expect(container.querySelector("textarea").getAttribute("aria-invalid")).toBe("true");
    expect(container.querySelector("textarea").value).toBe("teacher@example.edu");
    cleanup();
  });

  test("有帳號加不進去時也停在第二步：加成功的收進名單，輸入框只留加不進去的帳號", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(baseClass());
    vi.spyOn(TeachingClassesService, "addStudents").mockResolvedValue({
      added: 1,
      not_found: [],
      invalid_role: ["teacher@example.edu"],
      class: baseClass({ students: [{ id: "s1", email: "s1@example.edu" }] }),
    });

    const { container, cleanup } = await mount("/class-setup?classId=c1&step=2");
    await submitEmails(container, "s1@example.edu teacher@example.edu");

    expect(currentSearch).toContain("step=2");
    expect(container.querySelector("textarea").value).toBe("teacher@example.edu");
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining(tt("ClassSetupPage.invalidRoleList", { list: "teacher@example.edu" })));
    cleanup();
  });

  test("全部加入成功時照常前往下一步", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(baseClass());
    vi.spyOn(TeachingClassesService, "addStudents").mockResolvedValue({
      added: 1,
      not_found: [],
      invalid_role: [],
      class: baseClass({ students: [{ id: "s1", email: "s1@example.edu" }] }),
    });

    const { container, cleanup } = await mount("/class-setup?classId=c1&step=2");
    await submitEmails(container, "s1@example.edu");

    expect(currentSearch).toContain("step=3");
    expect(toast.warning).not.toHaveBeenCalled();
    cleanup();
  });
});

describe("ClassSetupPage 確認建立", () => {
  /* 必要步驟沒完成時，網址直接指到第五步也會被拉回能到的最後一步，不會卡在容量預檢 */
  test("班級沒有學生時拉回第二步，不做容量預檢", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(baseClass({ machine_nodes: [{ id: "n1", name: "Web" }] }));
    const capacityPreview = vi.spyOn(TeachingClassesService, "capacityPreview").mockResolvedValue({ ready: true });

    const { container, cleanup } = await mount("/class-setup?classId=c1&step=5");

    expect(capacityPreview).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain(tt("ClassSetupPage.capacityChecking"));
    expect(container.textContent).toContain(tt("ClassSetupPage.step2Title"));
    expect(container.textContent).toContain(tt("ClassSetupPage.needAtLeastOneStudent"));
    cleanup();
  });

  test("班級尚未選環境時拉回第三步，不做容量預檢", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(baseClass({ students: [{ id: "s1", email: "s1@example.edu" }] }));
    const capacityPreview = vi.spyOn(TeachingClassesService, "capacityPreview").mockResolvedValue({ ready: true });

    const { container, cleanup } = await mount("/class-setup?classId=c1&step=5");

    expect(capacityPreview).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain(tt("ClassSetupPage.capacityChecking"));
    expect(container.textContent).toContain(tt("ClassSetupPage.step3Title"));
    cleanup();
  });
});

describe("ClassSetupPage 建好課程環境回到第三步", () => {
  test("班級比環境清單晚載入時，剛建立的環境仍保持選取，不被班級已存的選擇蓋掉", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([
      { id: "env-1", versionId: "v1", name: "舊環境", version: 1, nodes: [] },
      { id: "env-9", versionId: "v9", name: "新環境", version: 1, nodes: [] },
    ]);
    let resolveClass;
    vi.spyOn(TeachingClassesService, "get").mockImplementation(() => new Promise((resolve) => { resolveClass = resolve; }));

    const { container, cleanup } = await mount({ pathname: "/class-setup", search: "?classId=c1&step=3", state: { createdTemplateId: "env-9" } });
    await act(async () => {
      resolveClass(baseClass({ students: [{ id: "s1", email: "s1@example.edu" }] }));
      await flush();
      await flush();
    });

    const choice = (name) => [...container.querySelectorAll("button[aria-pressed]")].find((button) => button.textContent.includes(name));
    expect(choice("新環境").getAttribute("aria-pressed")).toBe("true");
    expect(choice("舊環境").getAttribute("aria-pressed")).toBe("false");
    expect(toast.success).toHaveBeenCalledWith(tt("ClassSetupPage.templatePublishedMsg"));
    cleanup();
  });
});

describe("ClassSetupPage 第一步建立班級後按上一頁", () => {
  test("回到的是同一個班級的第一步，再按儲存是更新而不是多建一個班級", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    const create = vi.spyOn(TeachingClassesService, "create").mockResolvedValue(baseClass());
    const update = vi.spyOn(TeachingClassesService, "update").mockResolvedValue(baseClass({ name: "網路實務 B" }));
    let navigate;
    function NavigateProbe() {
      navigate = useNavigate();
      return null;
    }
    window.scrollTo = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(withQueryClient(
        <MemoryRouter initialEntries={["/class-management", "/class-setup"]} initialIndex={1}>
          <ClassSetupPage />
          <LocationProbe />
          <NavigateProbe />
        </MemoryRouter>,
      ));
      await flush();
    });
    const saveAndNext = async () => {
      const button = [...container.querySelectorAll("button")]
        .find((candidate) => candidate.textContent.includes(tt("ClassSetupPage.saveAndNextBtn")));
      await act(async () => {
        button.click();
        await flush();
        await flush();
      });
    };
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    act(() => {
      const name = container.querySelector("input");
      setter.call(name, "網路實務");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await saveAndNext();
    expect(create).toHaveBeenCalledTimes(1);
    expect(currentSearch).toBe("?classId=c1&step=2");

    await act(async () => {
      navigate(-1);
      await flush();
    });
    expect(currentSearch).toBe("?classId=c1&step=1");

    await saveAndNext();
    expect(create).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledWith("c1", expect.objectContaining({ name: "網路實務" }));
    act(() => root.unmount());
    container.remove();
  });
});
