import { describe, expect, it } from "vitest";
import {
  classSchedulePayload,
  createClassScheduleForm,
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
