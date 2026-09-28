// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";
import ClassSetupPage from "./ClassSetupPage";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import i18n from "../../../i18n";

/* i18n.t 找不到 key 時會回傳 key 本身，單純比對 t(key) 抓不到漏翻；先確認三個語系都有這個 key */
const tt = (key, options) => {
  for (const lng of ["zh-TW", "en", "ja"]) {
    expect(i18n.getResource(lng, "teaching", key), `${lng} teaching.json 缺少 ${key}`).toBeTypeOf("string");
  }
  return i18n.t(key, { ns: "teaching", ...options });
};

afterEach(() => {
  vi.restoreAllMocks();
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
    const textarea = container.querySelector("textarea");
    typeInto(textarea, "teacher@example.edu");
    const nextButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes(tt("ClassSetupPage.saveAndNextBtn")));
    await act(async () => {
      nextButton.click();
      await flush();
      await flush();
    });

    expect(addStudents).toHaveBeenCalledWith("c1", ["teacher@example.edu"]);
    expect(currentSearch).toContain("step=2");
    expect(container.textContent).toContain(tt("ClassWorkspacePage.invalidRoleList", { list: "teacher@example.edu" }));
    expect(container.querySelector("textarea").getAttribute("aria-invalid")).toBe("true");
    expect(container.querySelector("textarea").value).toBe("teacher@example.edu");
    cleanup();
  });

  test("有學生加入時照常前往下一步", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(baseClass());
    vi.spyOn(TeachingClassesService, "addStudents").mockResolvedValue({
      added: 1,
      not_found: [],
      invalid_role: ["teacher@example.edu"],
      class: baseClass({ students: [{ id: "s1", email: "s1@example.edu" }] }),
    });

    const { container, cleanup } = await mount("/class-setup?classId=c1&step=2");
    typeInto(container.querySelector("textarea"), "s1@example.edu teacher@example.edu");
    const nextButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent.includes(tt("ClassSetupPage.saveAndNextBtn")));
    await act(async () => {
      nextButton.click();
      await flush();
      await flush();
    });

    expect(currentSearch).toContain("step=3");
    expect(container.textContent).toContain(tt("ClassWorkspacePage.invalidRoleList", { list: "teacher@example.edu" }));
    cleanup();
  });
});

describe("ClassSetupPage 確認建立", () => {
  test("班級沒有學生時直接顯示未通過原因，不會一直停在預檢中", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(baseClass({ machine_nodes: [{ id: "n1", name: "Web" }] }));
    const capacityPreview = vi.spyOn(TeachingClassesService, "capacityPreview").mockResolvedValue({ ready: true });

    const { container, cleanup } = await mount("/class-setup?classId=c1&step=5");

    expect(capacityPreview).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain(tt("ClassSetupPage.capacityChecking"));
    expect(container.textContent).toContain(tt("ClassSetupPage.capacityNotReady"));
    expect(container.textContent).toContain(tt("ClassSetupPage.needAtLeastOneStudent"));
    expect(container.textContent).not.toContain(tt("ClassWorkspacePage.noEnvSelected"));
    cleanup();
  });

  test("班級尚未選環境時也直接列出原因", async () => {
    vi.spyOn(CourseEnvironmentsService, "listPublished").mockResolvedValue([]);
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(baseClass({ students: [{ id: "s1", email: "s1@example.edu" }] }));
    const capacityPreview = vi.spyOn(TeachingClassesService, "capacityPreview").mockResolvedValue({ ready: true });

    const { container, cleanup } = await mount("/class-setup?classId=c1&step=5");

    expect(capacityPreview).not.toHaveBeenCalled();
    expect(container.textContent).toContain(tt("ClassWorkspacePage.noEnvSelected"));
    expect(container.textContent).toContain("尚未選擇課程環境");
    cleanup();
  });
});
