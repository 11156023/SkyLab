import { expect, it } from "vitest";
import { MAX_BARS, ticketBarcode } from "./ticketBarcode";

const doneCount = (bars) => bars.filter((bar) => bar.done).length;

it("draws one bar per question with the finished ones first", () => {
  const bars = ticketBarcode("course-1", 24, 10);
  expect(bars).toHaveLength(24);
  expect(doneCount(bars)).toBe(10);
  expect(bars.slice(0, 10).every((bar) => bar.done)).toBe(true);
  expect(bars.at(-1).gap).toBe(0);
});

it("gives each course its own, stable pattern", () => {
  const widths = (id) => ticketBarcode(id, 24, 0).map((bar) => `${bar.width}:${bar.gap}`).join(",");
  expect(widths("course-1")).toBe(widths("course-1"));
  expect(widths("course-1")).not.toBe(widths("course-2"));
});

it("caps the bar count and lets one bar stand for several questions", () => {
  const bars = ticketBarcode("course-1", 120, 60);
  expect(bars).toHaveLength(MAX_BARS);
  expect(doneCount(bars)).toBe(20);
});

it("never hides a first answer or shows an unfinished course as full", () => {
  expect(doneCount(ticketBarcode("course-1", 100, 1))).toBe(1);
  expect(doneCount(ticketBarcode("course-1", 100, 99))).toBe(MAX_BARS - 1);
  expect(doneCount(ticketBarcode("course-1", 18, 18))).toBe(18);
  expect(doneCount(ticketBarcode("course-1", 18, 0))).toBe(0);
});

it("draws nothing when there are no questions", () => {
  expect(ticketBarcode("course-1", 0, 0)).toEqual([]);
  expect(ticketBarcode("course-1", undefined, undefined)).toEqual([]);
});
