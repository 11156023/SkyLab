import { describe, expect, it } from "vitest";
import {
  classSchedulePayload,
  createClassScheduleForm,
  rocTermOf,
} from "./classScheduleForm";

describe("class schedule form", () => {
  it("uses the same normalized fields for setup and management editing", () => {
    expect(
      createClassScheduleForm({
        name: "Linux 實務",
        term: "115-1",
        location: "A201",
        start_date: "2026-09-07",
        end_date: "2027-01-11",
        weekday: 0,
        start_time: "09:10:00",
        end_time: "12:00:00",
        boot_lead_minutes: 15,
        shutdown_grace_minutes: 60,
      }),
    ).toEqual({
      name: "Linux 實務",
      term: "115-1",
      location: "A201",
      startDate: "2026-09-07",
      endDate: "2027-01-11",
      weekday: 0,
      startTime: "09:10",
      endTime: "12:00",
      bootLeadMinutes: 15,
      shutdownGraceMinutes: 60,
    });
  });

  it("defaults the shutdown grace when the class predates the field", () => {
    expect(createClassScheduleForm({ name: "X" }).shutdownGraceMinutes).toBe(30);
  });

  it("starts on the next Monday and ends four months later", () => {
    const form = createClassScheduleForm(null, new Date(2026, 9, 7));
    expect(form.startDate).toBe("2026-10-12");
    expect(form.endDate).toBe("2027-02-12");
    expect(form.weekday).toBe(0);
  });

  it("clamps the default end date to the last day of the target month", () => {
    // 10/30 + 4 個月：舊寫法 setMonth 會溢位成 3/2
    expect(createClassScheduleForm(null, new Date(2028, 9, 30)).endDate).toBe("2029-02-28");
    expect(createClassScheduleForm(null, new Date(2028, 6, 31)).endDate).toBe("2028-11-30");
    expect(createClassScheduleForm(null, new Date(2028, 0, 31)).endDate).toBe("2028-05-31");
  });

  it("derives the default term from the start date's academic year", () => {
    expect(createClassScheduleForm(null, new Date(2026, 9, 12)).term).toBe("115-1");
    expect(createClassScheduleForm(null, new Date(2027, 1, 15)).term).toBe("115-2");
    expect(createClassScheduleForm(null, new Date(2027, 0, 11)).term).toBe("115-1");
    expect(createClassScheduleForm(null, new Date(2026, 7, 3)).term).toBe("115-1");
    expect(createClassScheduleForm(null, new Date(2026, 6, 27)).term).toBe("114-2");
  });

  it("maps every month boundary of the ROC academic year", () => {
    expect(rocTermOf(new Date(2026, 6, 31))).toBe("114-2");
    expect(rocTermOf(new Date(2026, 7, 1))).toBe("115-1");
    expect(rocTermOf(new Date(2027, 0, 31))).toBe("115-1");
    expect(rocTermOf(new Date(2027, 1, 1))).toBe("115-2");
  });

  it("keeps an existing class's term", () => {
    expect(createClassScheduleForm({ term: "113-2" }, new Date(2026, 9, 12)).term).toBe("113-2");
  });

  it("builds one consistent API payload without the internal class code", () => {
    expect(
      classSchedulePayload({
        name: " Linux 實務 ",
        term: " 115-1 ",
        location: " ",
        startDate: "2026-09-07",
        endDate: "2027-01-11",
        weekday: "0",
        startTime: "09:10",
        endTime: "12:00",
        bootLeadMinutes: "15",
        shutdownGraceMinutes: "60",
      }),
    ).toEqual({
      name: "Linux 實務",
      term: "115-1",
      location: null,
      start_date: "2026-09-07",
      end_date: "2027-01-11",
      weekday: 0,
      start_time: "09:10",
      end_time: "12:00",
      boot_lead_minutes: 15,
      shutdown_grace_minutes: 60,
    });
  });
});
