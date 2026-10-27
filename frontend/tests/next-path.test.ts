// F3 guards: the login redirect cannot leave the dashboard (open redirect).
import { describe, expect, it } from "vitest";

import { safeNext } from "../src/lib/next-path";

describe("safeNext", () => {
  it.each(["/dashboard", "/dashboard/review?x=1", "/dashboard/actions/abc"])("accepts %s", (v) =>
    expect(safeNext(v)).toBe(v),
  );

  it.each([
    "//evil.com",
    "/\\evil.com",
    "https://evil.com",
    "/login",
    "/dashboardx",
    "javascript:alert(1)",
    "/dashboard//evil.com",
    "/dashboard/\\evil.com",
    "/dashboard/\n",
    "",
    null,
    undefined,
  ])("rejects %j", (v) => expect(safeNext(v)).toBe("/dashboard"));
});
