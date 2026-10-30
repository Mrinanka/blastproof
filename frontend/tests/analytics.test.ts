// D-304 guards: no action id, filter or login target reaches Vercel; analytics only in production.
import { describe, expect, it } from "vitest";

import { collectsAnalytics, redactEvent, redactUrl } from "../src/lib/analytics";

const ID = "2ff1f40f-6c1e-4b8a-9d3e-0a1b2c3d4e5f";
const SITE = "https://blastproof.in";

describe("redactUrl", () => {
  it("leaves the landing page and plain dashboard pages as they are", () => {
    expect(redactUrl(`${SITE}/`)).toBe(`${SITE}/`);
    expect(redactUrl(`${SITE}/dashboard/review`)).toBe(`${SITE}/dashboard/review`);
  });

  it("replaces an action id with [id]", () => {
    expect(redactUrl(`${SITE}/dashboard/actions/${ID}`)).toBe(`${SITE}/dashboard/actions/[id]`);
    expect(redactUrl(`${SITE}/dashboard/actions/${ID.toUpperCase()}`)).toBe(
      `${SITE}/dashboard/actions/[id]`,
    );
  });

  it("replaces only whole UUID segments", () => {
    expect(redactUrl(`${SITE}/dashboard/actions/${ID}x`)).toBe(`${SITE}/dashboard/actions/${ID}x`);
  });

  it("drops the History filters", () => {
    expect(redactUrl(`${SITE}/dashboard/history?agent=${ID}&status=blocked&from=2026-10-01`)).toBe(
      `${SITE}/dashboard/history`,
    );
  });

  it("drops the login target", () => {
    expect(redactUrl(`${SITE}/login?next=%2Fdashboard%2Factions%2F${ID}`)).toBe(`${SITE}/login`);
  });

  it("drops the hash", () => {
    expect(redactUrl(`${SITE}/#faq`)).toBe(`${SITE}/`);
  });

  it("returns null for a URL it cannot parse", () => {
    expect(redactUrl(`/dashboard/actions/${ID}`)).toBeNull();
  });
});

describe("redactEvent", () => {
  it("redacts the url and keeps the other fields", () => {
    const event = { type: "vital", url: `${SITE}/dashboard/actions/${ID}?x=1`, route: "/r" };
    expect(redactEvent(event)).toEqual({ ...event, url: `${SITE}/dashboard/actions/[id]` });
  });

  it("drops an event whose url cannot be parsed", () => {
    expect(redactEvent({ type: "pageview", url: "not a url" })).toBeNull();
  });
});

describe("collectsAnalytics", () => {
  it("is on for the production deployment only", () => {
    expect(collectsAnalytics("production")).toBe(true);
    for (const env of ["preview", "development", "", undefined]) {
      expect(collectsAnalytics(env)).toBe(false);
    }
  });
});
