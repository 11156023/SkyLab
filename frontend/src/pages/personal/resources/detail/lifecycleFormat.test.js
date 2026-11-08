import { describe, expect, it } from "vitest";
import { AUTO_STOP_REASON_KEYS, effectiveExpiryIso, formatDate, formatDateTime, isExpiryFromWindow, localDateIso, parseDateOnly } from "./lifecycleFormat";

/* 12:00Z 在 UTC-12～UTC+11 都是同一個日曆日：本機（台灣 +8）與 CI（UTC）跑出來的日期一樣 */
const NOON_1015 = "2026-10-15T12:00:00.000Z";

describe("lifecycleFormat", () => {
  it("effectiveExpiryIso 取到期日與核准時段迄日較早者；只有時段的機器也算有到期", () => {
    expect(effectiveExpiryIso({ expiry_date: null, window_end_at: null })).toBeNull();
    expect(effectiveExpiryIso(null)).toBeNull();
    expect(effectiveExpiryIso({ expiry_date: "2026-11-30" })).toBe("2026-11-30");
    // 自己申請的機器：沒有 expiry_date，只有時段迄
    expect(effectiveExpiryIso({ window_end_at: NOON_1015 })).toBe("2026-10-15");
    // 兩個都有：時段比到期日早 → 時段迄；反過來 → 到期日
    expect(effectiveExpiryIso({ expiry_date: "2026-11-30", window_end_at: NOON_1015 })).toBe("2026-10-15");
    expect(effectiveExpiryIso({ expiry_date: "2026-10-01", window_end_at: NOON_1015 })).toBe("2026-10-01");
    expect(effectiveExpiryIso({ window_end_at: "not a date" })).toBeNull();
  });

  it("時段迄的日期依本地時區拆，跟總覽「使用至」的本地時間同一天，不用 UTC 日期", () => {
    // 台灣 2026-11-08 03:00 = 2026-11-07T19:00Z：UTC 日期是 11/07，本地是 11/08
    const windowEnd = "2026-11-07T19:00:00.000Z";
    const expected = localDateIso(new Date(windowEnd));
    expect(effectiveExpiryIso({ window_end_at: windowEnd })).toBe(expected);
    // 本地時區若在 UTC+5 以東，這個時刻就不該被顯示成 11/07
    if (new Date(windowEnd).getTimezoneOffset() <= -300) expect(expected).toBe("2026-11-08");
  });

  it("isExpiryFromWindow：生效的到期來自核准時段（沒有到期日，或時段比到期日早）", () => {
    expect(isExpiryFromWindow({ expiry_date: null, window_end_at: null })).toBe(false);
    expect(isExpiryFromWindow({ expiry_date: "2026-11-30" })).toBe(false);
    expect(isExpiryFromWindow({ window_end_at: NOON_1015 })).toBe(true);
    expect(isExpiryFromWindow({ expiry_date: "2026-11-30", window_end_at: NOON_1015 })).toBe(true);
    expect(isExpiryFromWindow({ expiry_date: "2026-10-01", window_end_at: NOON_1015 })).toBe(false);
    // 同一天以到期日為準（它帶實際截止時刻）
    expect(isExpiryFromWindow({ expiry_date: "2026-10-15", window_end_at: NOON_1015 })).toBe(false);
  });

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
