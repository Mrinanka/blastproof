// F4 guards: FE-09, FE-10.
import { describe, expect, it } from "vitest";

import { badgeText, isActive, navFor } from "../src/lib/nav";

const hrefs = (role: "admin" | "member" | undefined) =>
  navFor(role).flatMap((g) => g.items.map((i) => i.href));

describe("navFor", () => {
  it("shows Knowledge to members (read-only) and hides Users", () => {
    expect(hrefs("member")).toContain("/dashboard/knowledge");
    expect(hrefs("member")).not.toContain("/dashboard/settings/users");
  });

  it("shows the member view while the role is unknown", () => {
    expect(hrefs(undefined)).toEqual(hrefs("member"));
  });

  it("gives an admin all seven links in order", () => {
    expect(hrefs("admin")).toEqual([
      "/dashboard",
      "/dashboard/review",
      "/dashboard/history",
      "/dashboard/knowledge",
      "/dashboard/settings/agents",
      "/dashboard/settings/rules",
      "/dashboard/settings/users",
    ]);
  });

  it("puts the pending badge on Review only", () => {
    const withBadge = navFor("admin").flatMap((g) => g.items.filter((i) => i.badge));
    expect(withBadge.map((i) => i.href)).toEqual(["/dashboard/review"]);
  });
});

describe("isActive", () => {
  it("matches the overview exactly", () => {
    expect(isActive("/dashboard", "/dashboard")).toBe(true);
    expect(isActive("/dashboard/review", "/dashboard")).toBe(false);
  });

  it("matches a section by whole path segments", () => {
    expect(isActive("/dashboard/review", "/dashboard/review")).toBe(true);
    expect(isActive("/dashboard/review/abc", "/dashboard/review")).toBe(true);
    expect(isActive("/dashboard/reviewer", "/dashboard/review")).toBe(false);
  });
});

describe("badgeText", () => {
  it.each([
    [undefined, null],
    [0, null],
    [1, "1"],
    [99, "99"],
    [100, "99+"],
  ])("%s gives %s", (total, text) => {
    expect(badgeText(total)).toBe(text);
  });
});
