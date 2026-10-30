// F8 guards: FE-21, CONV-12 (filters live in the URL), DW-F08 (stand-in: the URL round-trips).
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  HISTORY_PAGE_SIZE,
  applyPatch,
  hasFilters,
  parseHistoryParams,
  toActionsQuery,
  toSearchString,
  withPage,
  type HistoryFilters,
} from "../src/lib/history-filters";

const AGENT = "0b6f2d4e-8a1c-4f3b-9d2e-7c5a1b3e9f00";
const DEFAULTS: HistoryFilters = {
  statuses: [],
  agentId: null,
  table: null,
  from: null,
  to: null,
  page: 1,
};
const parse = (qs: string) => parseHistoryParams(new URLSearchParams(qs));

describe("parseHistoryParams", () => {
  it("gives the defaults for an empty query", () => {
    expect(parse("")).toEqual(DEFAULTS);
  });

  it("keeps repeated statuses in canonical order and drops unknown and duplicate ones", () => {
    expect(parse("status=rejected&status=bogus&status=blocked&status=rejected").statuses).toEqual([
      "rejected",
      "blocked",
    ]);
    expect(parse("status=failed&status=awaiting_review").statuses).toEqual([
      "awaiting_review",
      "failed",
    ]);
  });

  it("keeps a UUID agent (lowercased) and drops anything else", () => {
    expect(parse(`agent=${AGENT.toUpperCase()}`).agentId).toBe(AGENT);
    for (const bad of ["abc", `${AGENT}x`, "", "1; DROP TABLE users"]) {
      expect(parse(`agent=${encodeURIComponent(bad)}`).agentId).toBeNull();
    }
  });

  it("trims the table and drops an empty or over-long one", () => {
    expect(parse("table=%20users%20").table).toBe("users");
    expect(parse("table=%20%20").table).toBeNull();
    expect(parse(`table=${"t".repeat(63)}`).table).toBe("t".repeat(63));
    expect(parse(`table=${"t".repeat(64)}`).table).toBeNull();
  });

  it("keeps real calendar dates and drops malformed or impossible ones", () => {
    expect(parse("from=2026-10-01&to=2026-10-29")).toMatchObject({
      from: "2026-10-01",
      to: "2026-10-29",
    });
    for (const bad of ["2026-02-30", "2026-13-01", "10/29/2026", "2026-1-1", "0099-01-01", "x"]) {
      expect(parse(`from=${bad}`).from).toBeNull();
      expect(parse(`to=${bad}`).to).toBeNull();
    }
    expect(parse("from=2028-02-29").from).toBe("2028-02-29");
  });

  it("reads page as a whole number of at least 1, otherwise 1", () => {
    expect(parse("page=3").page).toBe(3);
    for (const bad of ["0", "-1", "abc", "1.5", "1e3", "", "99999999999999999999"]) {
      expect(parse(`page=${bad}`).page).toBe(1);
    }
  });
});

describe("toSearchString", () => {
  it("omits defaults, including page 1", () => {
    expect(toSearchString(DEFAULTS)).toBe("");
    expect(toSearchString({ ...DEFAULTS, page: 1, table: "users" })).toBe("table=users");
  });

  it("writes keys in a fixed order and round-trips through parse", () => {
    const f: HistoryFilters = {
      statuses: ["rejected", "blocked"],
      agentId: AGENT,
      table: "order_items",
      from: "2026-10-01",
      to: "2026-10-29",
      page: 2,
    };
    expect(toSearchString(f)).toBe(
      `status=rejected&status=blocked&agent=${AGENT}&table=order_items&from=2026-10-01&to=2026-10-29&page=2`,
    );
    expect(parse(toSearchString(f))).toEqual(f);
  });

  it("encodes a table name safely", () => {
    const f = { ...DEFAULTS, table: "a&b=c" };
    expect(parse(toSearchString(f)).table).toBe("a&b=c");
  });
});

describe("applyPatch and withPage", () => {
  it("resets the page on any filter change", () => {
    const f = { ...DEFAULTS, page: 4 };
    expect(applyPatch(f, { table: "users" })).toEqual({ ...DEFAULTS, table: "users", page: 1 });
    expect(applyPatch(f, { statuses: ["failed"] }).page).toBe(1);
  });

  it("changes only the page with withPage", () => {
    const f = { ...DEFAULTS, table: "users" };
    expect(withPage(f, 3)).toEqual({ ...f, page: 3 });
  });

  it("knows when any filter is set", () => {
    expect(hasFilters({ ...DEFAULTS, page: 5 })).toBe(false);
    expect(hasFilters({ ...DEFAULTS, to: "2026-10-01" })).toBe(true);
  });
});

describe("toActionsQuery", () => {
  const savedTz = process.env.TZ;
  beforeAll(() => {
    process.env.TZ = "America/New_York";
  });
  afterAll(() => {
    process.env.TZ = savedTz;
  });

  it("runs in the test time zone (otherwise the local-day checks below prove nothing)", () => {
    expect(new Date(2026, 9, 1).getTimezoneOffset()).toBe(240);
  });

  it("sends 25 per page, newest first, and no owner or user parameter", () => {
    expect(toActionsQuery(DEFAULTS)).toEqual({ order: "newest", limit: 25, offset: 0 });
    expect(toActionsQuery({ ...DEFAULTS, page: 2 })).toMatchObject({ offset: HISTORY_PAGE_SIZE });
  });

  it("maps every filter to the backend's names", () => {
    const query = toActionsQuery({
      statuses: ["rejected"],
      agentId: AGENT,
      table: "users",
      from: "2026-10-01",
      to: "2026-10-29",
      page: 1,
    });
    expect(Object.keys(query!).sort()).toEqual(
      ["agent_id", "from", "limit", "offset", "order", "status", "table", "to"].sort(),
    );
    expect(query).toMatchObject({ status: ["rejected"], agent_id: AGENT, table: "users" });
  });

  it("starts at local midnight and ends at the next local midnight (to is exclusive)", () => {
    const query = toActionsQuery({ ...DEFAULTS, from: "2026-10-01", to: "2026-10-01" })!;
    expect(query.from).toBe("2026-10-01T04:00:00.000Z");
    expect(query.to).toBe("2026-10-02T04:00:00.000Z");
  });

  it("covers a 25-hour local day when daylight saving ends", () => {
    const query = toActionsQuery({ ...DEFAULTS, from: "2026-11-01", to: "2026-11-01" })!;
    expect(query.from).toBe("2026-11-01T04:00:00.000Z");
    expect(query.to).toBe("2026-11-02T05:00:00.000Z");
  });

  it("sends one side of the range alone", () => {
    expect(toActionsQuery({ ...DEFAULTS, from: "2026-10-01" })).not.toHaveProperty("to");
    expect(toActionsQuery({ ...DEFAULTS, to: "2026-10-01" })).not.toHaveProperty("from");
  });

  it("refuses a range whose start is after its end", () => {
    expect(toActionsQuery({ ...DEFAULTS, from: "2026-10-02", to: "2026-10-01" })).toBeNull();
  });
});
