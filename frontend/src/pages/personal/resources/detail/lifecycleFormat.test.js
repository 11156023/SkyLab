import { describe, expect, it } from "vitest";
import { AUTO_STOP_REASON_KEYS, formatDate, formatDateTime, parseDateOnly } from "./lifecycleFormat";

describe("lifecycleFormat", () => {
  it("parseDateOnly 以本地時區拆解純日期，不會因 UTC 解析差一天", () => {
    const d = parseDateOnly("2026-10-01");
    expect([d.getFullYear(), d.getMonth(), d.getDate()]).toEqual([2026, 9, 1]);
    expect(d.getHours()).toBe(0);
  });

  it("formatDate 與本地建構的同一天格式一致；空值回 null", () => {
    const opts = { year: "numeric", month: "2-digit", day: "2-digit" };
    expect(formatDate("2026-10-01", "en")).toBe(new Date(2026, 9, 1).toLocaleDateString("en", opts));
    expect(formatDate(null, "en")).toBeNull();
    expect(formatDate("", "en")).toBeNull();
  });

  it("formatDateTime 空值回 null，有值時含時分", () => {
    expect(formatDateTime(null, "en")).toBeNull();
    const value = new Date(2026, 9, 1, 13, 5);
    expect(formatDateTime(value, "en")).toBe(value.toLocaleString("en", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }));
  });

  it("自動關機原因對到 LifecycleCard 的文案 key", () => {
    expect(AUTO_STOP_REASON_KEYS.idle).toBe("LifecycleCard.reasonIdle");
    expect(Object.keys(AUTO_STOP_REASON_KEYS).sort()).toEqual(["idle", "practice_quota", "ttl_expired", "window_grace"]);
  });
});
