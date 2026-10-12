import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import i18n from "../i18n";
import { formatDate, formatDateTime, formatExpiryDate, formatMonthDay } from "./formatDate";

/* 純日期字串不能被當成 UTC 午夜：負時區（美洲）會差一天，正時區（台灣）看不出來 */
const ORIGINAL_TZ = process.env.TZ;

function inTimeZone(tz) {
  process.env.TZ = tz;
}

describe("formatDate utilities", () => {
  let previousLanguage;
  beforeAll(async () => {
    previousLanguage = i18n.language;
    await i18n.changeLanguage("zh-TW");
  });
  afterEach(() => {
    if (ORIGINAL_TZ === undefined) delete process.env.TZ;
    else process.env.TZ = ORIGINAL_TZ;
  });
  afterAll(async () => {
    await i18n.changeLanguage(previousLanguage);
  });

  it("shows a date-only string as that calendar day in a negative-offset time zone", () => {
    inTimeZone("America/New_York");
    expect(formatDate("2026-10-15")).toBe("2026/10/15");
    expect(formatMonthDay("2026-10-15")).toBe("10/15");
  });

  it("shows a date-only string as that calendar day in Taiwan too", () => {
    inTimeZone("Asia/Taipei");
    expect(formatDate("2026-10-15")).toBe("2026/10/15");
  });

  it("still converts full timestamps to the viewer's local time", () => {
    inTimeZone("America/New_York");
    // 2026-10-15 02:00 UTC 是紐約的 10/14 晚上
    expect(formatDate("2026-10-15T02:00:00Z")).toBe("2026/10/14");
    expect(formatDateTime("2026-10-15T02:00:00Z")).toBe("2026/10/14 22:00:00");
  });

  it("returns the fallback for empty or impossible date-only strings", () => {
    expect(formatDate("")).toBe("—");
    expect(formatDate(null, null)).toBeNull();
    expect(formatDate("2026-02-31")).toBe("—");
  });

  describe("formatExpiryDate", () => {
    it("Taiwan: shows the calendar date and the 08:00 local cutoff (00:00 UTC)", () => {
      inTimeZone("Asia/Taipei");
      const expiry = formatExpiryDate("2026-10-15");
      expect(expiry.date).toBe("2026/10/15");
      expect(expiry.cutoff).toBe("08:00 截止");
      expect(expiry.title).toContain("2026/10/15 08:00");
      expect(expiry.text).toBe("2026/10/15（08:00 截止）");
    });

    it("negative offset: keeps the calendar date and labels the cutoff with its earlier local day", () => {
      inTimeZone("America/New_York");
      const expiry = formatExpiryDate("2026-10-15");
      expect(expiry.date).toBe("2026/10/15");
      // 00:00 UTC 在紐約（EDT, UTC-4）是前一天 20:00
      expect(expiry.cutoff).toBe("10/14 20:00 截止");
      expect(expiry.title).toContain("2026/10/14 20:00");
    });

    it("follows the UI language", async () => {
      inTimeZone("Asia/Taipei");
      i18n.addResources("en", "common", {
        "ExpiryDate.cutoff": "ends {{time}}",
        "ExpiryDate.inline": "{{date}} ({{cutoff}})",
      });
      await i18n.changeLanguage("en");
      try {
        expect(formatExpiryDate("2026-10-15").text).toBe("10/15/2026 (ends 08:00)");
      } finally {
        await i18n.changeLanguage("zh-TW");
      }
    });

    it("returns null for empty values and no cutoff for non date-only values", () => {
      expect(formatExpiryDate(null)).toBeNull();
      expect(formatExpiryDate("")).toBeNull();
      expect(formatExpiryDate("not-a-date")).toBeNull();
      inTimeZone("Asia/Taipei");
      expect(formatExpiryDate("2026-10-15T08:00:00Z")).toEqual({
        date: "2026/10/15", cutoff: null, title: null, text: "2026/10/15",
      });
    });
  });
});
