// F5 guards: CONV-17.
import { describe, expect, it } from "vitest";

import {
  formatConfidence,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatRelative,
  formatUsd,
  parseTimestamp,
} from "../src/lib/format";

describe("formatNumber", () => {
  it.each([
    [0, "0"],
    [48213, "48,213"],
    [null, "—"],
    [Number.NaN, "—"],
  ])("%s -> %s", (n, out) => expect(formatNumber(n)).toBe(out));
});

describe("formatUsd", () => {
  it.each([
    [null, "Unknown"],
    [0, "$0.00"],
    [0.00421, "$0.0042"],
    [1.5, "$1.50"],
    [12, "$12.00"],
  ])("%s -> %s", (n, out) => expect(formatUsd(n)).toBe(out));
});

describe("formatDuration", () => {
  it.each([
    [null, "—"],
    [850, "850 ms"],
    [999, "999 ms"],
    [1000, "1 s"],
    [14200, "14.2 s"],
    [59_999, "1 min"],
    [60_000, "1 min"],
    [61_000, "1 min 1 s"],
  ])("%s -> %s", (ms, out) => expect(formatDuration(ms)).toBe(out));
});

describe("formatConfidence", () => {
  it.each([
    [0.87, "87%"],
    [1, "100%"],
    [null, "—"],
  ])("%s -> %s", (c, out) => expect(formatConfidence(c)).toBe(out));
});

describe("parseTimestamp", () => {
  it("accepts Z and offsets", () => {
    expect(parseTimestamp("2026-10-27T10:00:00Z")).toBeInstanceOf(Date);
    expect(parseTimestamp("2026-10-27T10:00:00+05:30")).toBeInstanceOf(Date);
    expect(parseTimestamp("2026-10-27T10:00:00.123456+00:00")).toBeInstanceOf(Date);
  });
  it.each(["2026-10-27T10:00:00", "not a date", "", "2026-13-45T10:00:00Z"])("rejects %j", (bad) =>
    expect(parseTimestamp(bad)).toBeNull(),
  );
});

describe("formatDateTime", () => {
  const iso = "2026-10-27T10:00:00Z";
  it("shows the same instant differently per time zone (local time, CONV-17)", () => {
    const utc = formatDateTime(iso, { timeZone: "UTC" });
    const kolkata = formatDateTime(iso, { timeZone: "Asia/Kolkata" });
    expect(utc).toContain("10:00");
    expect(kolkata).toContain("3:30");
    expect(utc).not.toBe(kolkata);
  });
  it("adds seconds on request", () => {
    expect(formatDateTime("2026-10-27T10:00:07Z", { timeZone: "UTC", seconds: true })).toContain(
      "10:00:07",
    );
  });
  it("gives a dash for invalid and naive timestamps", () => {
    expect(formatDateTime("garbage")).toBe("—");
    expect(formatDateTime("2026-10-27T10:00:00")).toBe("—");
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-10-27T12:00:00Z");
  const ago = (seconds: number) => new Date(now.getTime() - seconds * 1000).toISOString();
  const ahead = (seconds: number) => new Date(now.getTime() + seconds * 1000).toISOString();

  it.each([
    [44, "just now"],
    [45, "1 minute ago"],
    [180, "3 minutes ago"],
    [44 * 60, "44 minutes ago"],
    [45 * 60, "1 hour ago"],
    [21 * 3600, "21 hours ago"],
    [22 * 3600, "yesterday"],
    [3 * 86_400, "3 days ago"],
    [65 * 86_400, "2 months ago"],
  ])("%s s ago -> %s", (s, out) => expect(formatRelative(ago(s), now)).toBe(out));

  it("handles the future", () => {
    expect(formatRelative(ahead(3 * 3600), now)).toBe("in 3 hours");
  });
  it("gives a dash for invalid timestamps", () => {
    expect(formatRelative("nope", now)).toBe("—");
    expect(formatRelative("2026-10-27T11:00:00", now)).toBe("—");
  });
});
