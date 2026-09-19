import { describe, expect, it } from "vitest";
import {
  buildTeacherDashboardDemo,
  nextClassSession,
  summarizeCheckpointReports,
} from "./TeacherDashboardPage";

describe("teacher dashboard checkpoint summary", () => {
  it("aggregates completed checkpoints and students", () => {
    const result = summarizeCheckpointReports([
      {
        path: { id: "course-a", title: "Linux" },
        report: {
          students: [
            { user_id: "s1", completed_questions: 3, total_questions: 4, progress_percent: 75 },
            { user_id: "s2", completed_questions: 1, total_questions: 4, progress_percent: 25 },
          ],
        },
      },
    ]);

    expect(result.completed).toBe(4);
    expect(result.possible).toBe(8);
    expect(result.percent).toBe(50);
    expect(result.students).toBe(2);
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
    const summary = summarizeCheckpointReports(demo.reports);

    expect(demo.classes).toHaveLength(3);
    expect(demo.reports).toHaveLength(3);
    expect(summary.students).toBe(78);
    expect(summary.completed).toBeGreaterThan(0);
    expect(nextClassSession(demo.classes[0], now)).not.toBeNull();
  });
});
