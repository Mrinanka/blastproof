// F3 guards: no retry on 4xx (D-223), a 401 sends the user to /login.
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/lib/api/errors";
import { makeQueryClient, shouldRetry } from "../src/lib/query-client";

describe("shouldRetry", () => {
  it.each([400, 401, 403, 404, 409, 422])("never retries %i", (status) => {
    expect(shouldRetry(0, new ApiError(status, "c", "m"))).toBe(false);
  });

  it.each([500, 503, 0])("retries %i up to 3 times", (status) => {
    const error = new ApiError(status, "c", "m");
    expect([0, 1, 2].map((n) => shouldRetry(n, error))).toEqual([true, true, true]);
    expect(shouldRetry(3, error)).toBe(false);
  });

  it("does not retry other errors", () => {
    expect(shouldRetry(0, new Error("bug"))).toBe(false);
  });

  it("is the client's query default", () => {
    expect(makeQueryClient().getDefaultOptions().queries?.retry).toBe(shouldRetry);
  });
});

describe("401 handling", () => {
  const fail = (status: number) => () => Promise.reject(new ApiError(status, "c", "m"));

  it("calls onUnauthorized once for a 401 query", async () => {
    const onUnauthorized = vi.fn();
    const client = makeQueryClient(onUnauthorized);
    await client.fetchQuery({ queryKey: ["a"], queryFn: fail(401), retry: false }).catch(() => {});
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("calls onUnauthorized once for a 401 mutation", async () => {
    const onUnauthorized = vi.fn();
    const client = makeQueryClient(onUnauthorized);
    await client
      .getMutationCache()
      .build(client, { mutationFn: fail(401) })
      .execute(undefined)
      .catch(() => {});
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it.each([403, 500])("does not call it for %i", async (status) => {
    const onUnauthorized = vi.fn();
    const client = makeQueryClient(onUnauthorized);
    await client
      .fetchQuery({ queryKey: ["a"], queryFn: fail(status), retry: false })
      .catch(() => {});
    await client
      .getMutationCache()
      .build(client, { mutationFn: fail(status) })
      .execute(undefined)
      .catch(() => {});
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
