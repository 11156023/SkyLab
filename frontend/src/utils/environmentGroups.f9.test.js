import { afterAll, beforeAll, describe, expect, it } from "vitest";
import i18n from "../i18n";
import { buildEnvironmentGroups } from "./environmentGroups";

/* 與 locale_changes 提交的英文字串一致；locales/en/common.json 補上後這段只是覆寫同值 */
const EN = {
  "EnvironmentGroups.expiresAt": "Expires {{time}}",
  "EnvironmentGroups.expiresByPolicy": "Expires per environment policy",
  "EnvironmentGroups.byCourseSchedule": "Managed by course schedule",
  "EnvironmentGroups.multiNode": "Multiple nodes",
  "EnvironmentGroups.provisioning": "Provisioning",
  "EnvironmentGroups.machine": "Machine",
};

const CJK = /[぀-ヿ一-鿿]/;

describe("environment group labels follow the UI language", () => {
  let previous;
  beforeAll(async () => {
    previous = i18n.language;
    i18n.addResources("en", "common", EN);
    await i18n.changeLanguage("en");
  });
  afterAll(async () => {
    await i18n.changeLanguage(previous);
  });

  it("quick practice timing and node labels are translated", () => {
    const [group] = buildEnvironmentGroups([], [
      { id: "s1", title: "DB", status: "running", expiresAt: null, machines: [{ id: "m1", requestId: "r1" }] },
    ]);
    expect(group.timingLabel).toBe("Expires per environment policy");
    expect(group.nodeLabel).toBe("Provisioning");
  });

  it("an expiry time is interpolated", () => {
    const [group] = buildEnvironmentGroups([], [
      { id: "s2", title: "DB", status: "running", expiresAt: "2026-08-27T15:00:00Z", machines: [] },
    ]);
    expect(group.timingLabel).toMatch(/^Expires \d/);
  });

  it("course groups and fallback machine roles have no CJK text", () => {
    const [group] = buildEnvironmentGroups([
      { vmid: 1, request_id: "c1", teaching_class_id: "k1", status: "running", node: "pve1" },
      { vmid: 2, request_id: "c2", teaching_class_id: "k1", status: "running", node: "pve2" },
    ]);
    expect(group.timingLabel).toBe("Managed by course schedule");
    expect(group.nodeLabel).toBe("Multiple nodes");
    expect(group.machines[0].role).toBe("Machine");
    for (const text of [group.timingLabel, group.nodeLabel, group.machines[0].role]) {
      expect(CJK.test(text)).toBe(false);
    }
  });
});
