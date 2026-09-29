// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

const toast = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => true }));
vi.mock("../../../contexts/UnsavedChangesContext", () => ({
  useUnsavedChanges: () => ({ confirmLeave: async () => true }),
  useUnsavedChangesGuard: () => {},
}));

import ClassSetupPage from "./ClassSetupPage";
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

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
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
    root.render(
      <MemoryRouter initialEntries={[url]}>
        <ClassSetupPage />
        <LocationProbe />
      </MemoryRouter>,
    );
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
