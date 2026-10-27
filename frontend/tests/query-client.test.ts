// F2 guards: FE-03 (refetch on focus, no background polling).
import { describe, expect, it } from "vitest";

import { getQueryClient, makeQueryClient } from "../src/lib/query-client";

describe("makeQueryClient", () => {
  it("pins the focus and background refetch options", () => {
    const queries = makeQueryClient().getDefaultOptions().queries;
    expect(queries?.refetchOnWindowFocus).toBe(true);
    expect(queries?.refetchIntervalInBackground).toBe(false);
  });
});

describe("getQueryClient", () => {
  it("returns a new client on each call on the server", () => {
    expect(getQueryClient()).not.toBe(getQueryClient());
  });
});
