import { describe, expect, it } from "vitest";
import { formatCount, formatDuration, formatMoment } from "../src/lib/format";

const MIN = 60;
const HOUR = 3600;
const DAY = 86_400;

describe("formatDuration", () => {
  it.each([
    [0, "less than 1 min"],
    [59, "less than 1 min"],
    [MIN, "1 min"],
    [5 * MIN, "5 min"],
    [59 * MIN + 59, "59 min"],
    [HOUR, "1 h"],
    [2 * HOUR + 10 * MIN, "2 h 10 min"],
    [2 * HOUR + 10 * MIN + 45, "2 h 10 min"],
    [23 * HOUR + 59 * MIN, "23 h 59 min"],
    [DAY, "1 d"],
    [3 * DAY + 4 * HOUR, "3 d 4 h"],
    [3 * DAY + 4 * HOUR + 30 * MIN, "3 d 4 h"],
    [3 * DAY, "3 d"],
  ])("reads %i seconds as %s", (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text);
  });

  it.each([null, undefined, Number.NaN, -5, Number.POSITIVE_INFINITY])("shows an em dash for %s", (value) => {
    expect(formatDuration(value)).toBe("—");
  });
});

describe("formatMoment", () => {
  it("shows the moment in UTC, in English, with the zone named", () => {
    expect(formatMoment("2026-11-07T17:50:30Z")).toBe("Nov 7, 2026, 17:50 UTC");
  });

  it("converts an offset to UTC instead of keeping the local clock", () => {
    expect(formatMoment("2026-11-07T23:30:00-03:00")).toBe("Nov 8, 2026, 02:30 UTC");
  });

  it.each([null, undefined, "", "not a date"])("shows an em dash for %s", (value) => {
    expect(formatMoment(value)).toBe("—");
  });
});

describe("formatCount", () => {
  it("groups thousands the English way", () => {
    expect(formatCount(1234567)).toBe("1,234,567");
    expect(formatCount(0)).toBe("0");
  });

  it.each([null, undefined, Number.NaN])("shows an em dash for %s", (value) => {
    expect(formatCount(value)).toBe("—");
  });
});
