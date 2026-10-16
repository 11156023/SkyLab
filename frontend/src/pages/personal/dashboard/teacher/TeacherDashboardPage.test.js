import { describe, expect, it } from "vitest";
import {
  buildTeacherDashboardDemo,
  nextClassSession,
  teacherDisplayName,
} from "./TeacherDashboardPage";

describe("teacherDisplayName", () => {
  it("uses the first part of the full name", () => {
    expect(teacherDisplayName({ full_name: "  Amy Chen ", email: "amy@x.edu" })).toBe("Amy");
  });

  it("returns null (no email fallback) when the full name is blank or whitespace", () => {
    expect(teacherDisplayName({ full_name: "   ", email: "amy@x.edu" })).toBeNull();
    expect(teacherDisplayName({ full_name: "", email: "bob@x.edu" })).toBeNull();
    expect(teacherDisplayName(null)).toBeNull();
  });
});

describe("nextClassSession", () => {
  it("returns today's later session before rolling to next week", () => {
    const now = new Date("2026-08-12T10:00:00+08:00");
    const next = nextClassSession({
      start_date: "2026-08-01",
      end_date: "2026-12-31",
      weekday: 2,
      start_time: "13:10:00",
    }, now);

    expect(next.toISOString()).toBe("2026-08-12T05:10:00.000Z");
  });
});

describe("teacher dashboard demo data", () => {
  it("fills every existing dashboard section without changing production data", () => {
    const now = new Date("2026-09-17T10:00:00+08:00");
    const demo = buildTeacherDashboardDemo(now);

    expect(demo.classes).toHaveLength(3);
    expect(nextClassSession(demo.classes[0], now)).not.toBeNull();
  });
});
