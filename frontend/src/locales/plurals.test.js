import { beforeAll, expect, test } from "vitest";

import i18n from "../i18n";

// en／ja 語系檔改為用到才載入
beforeAll(() => i18n.loadLanguages(["en", "ja"]));

/* 英文要分單複數（_one／_other）；中文、日文沒有複數形，沿用沒有後綴的鍵 */
test.each([
  ["ai", "AiMonitoringPage.attentionModelsTitle", "1 model deployment is unhealthy", "2 model deployments are unhealthy"],
  ["personal", "RequestFormPage.coresValue", "1 core", "2 cores"],
])("%s %s uses the singular for one and the plural otherwise", (ns, key, one, many) => {
  const t = i18n.getFixedT("en", ns);
  expect(t(key, { count: 1 })).toBe(one);
  expect(t(key, { count: 2 })).toBe(many);
  for (const lng of ["zh-TW", "ja"]) {
    const text = i18n.getFixedT(lng, ns)(key, { count: 1 });
    expect(text, `${lng} ${key}`).not.toBe(key);
    expect(text, `${lng} ${key}`).toContain("1");
  }
});

test("the teacher dashboard class summary reads correctly for one student and one machine", () => {
  const t = i18n.getFixedT("en", "personal");
  expect(t("TeacherDashboardPage.classMemberSummary", { count: 1, nodes: 1 })).toBe("Students: 1 · Machines per student: 1");
});
