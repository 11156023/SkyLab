import { expect, it } from "vitest";
import { MAX_SEGMENTS, coreSegments, gbSegments, nextPeak, visiblePeak } from "./kpiBar";

const GB = 1024 ** 3;

it("gives the CPU bar one segment per core", () => {
  expect(coreSegments(2)).toBe(2);
  expect(coreSegments(MAX_SEGMENTS)).toBe(MAX_SEGMENTS);
  expect(coreSegments(1)).toBe(0);
  expect(coreSegments(32)).toBe(0);
  expect(coreSegments(undefined)).toBe(0);
});

it("cuts memory and disk into whole 1/2/4… GB segments, at most sixteen", () => {
  expect(gbSegments(4 * GB)).toBe(4);
  expect(gbSegments(16 * GB)).toBe(16);
  expect(gbSegments(32 * GB)).toBe(16); // 2 GB a segment
  expect(gbSegments(128 * GB)).toBe(16); // 8 GB a segment
  expect(gbSegments(100 * GB)).toBe(0); // 8 GB steps give 12.5 segments → leave it whole
});

it("leaves the bar whole when the capacity does not divide evenly or is too small", () => {
  expect(gbSegments(1.5 * GB)).toBe(0);
  expect(gbSegments(1 * GB)).toBe(0);
  expect(gbSegments(512 * 1024 ** 2)).toBe(0);
  expect(gbSegments(null)).toBe(0);
});

it("keeps the highest reading and forgets it when the readings stop", () => {
  expect(nextPeak(null, 20)).toBe(20);
  expect(nextPeak(62, 20)).toBe(62);
  expect(nextPeak(62, 80)).toBe(80);
  expect(nextPeak(62, null)).toBe(null);
});

it("marks the peak only when it stands clear of the current reading", () => {
  expect(visiblePeak(62, 20)).toBe(62);
  expect(visiblePeak(22, 20)).toBe(null);
  expect(visiblePeak(62, null)).toBe(null);
  expect(visiblePeak(null, 20)).toBe(null);
});
