// F3 guards: errors carry the HTTP status so retry and 401 handling can use it.
import { describe, expect, it } from "vitest";

import { ApiError, unwrap } from "../src/lib/api/errors";

const result = (data: unknown, error: unknown, status: number) =>
  Promise.resolve({ data, error, response: new Response(null, { status }) });

describe("unwrap", () => {
  it("returns the data", async () => {
    expect(await unwrap(result({ a: 1 }, undefined, 200))).toEqual({ a: 1 });
  });

  it("throws an ApiError with status and code from the envelope", async () => {
    const error = { error: { code: "not_found", message: "Missing." } };
    const caught = await unwrap(result(undefined, error, 404)).catch((e) => e);
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ status: 404, code: "not_found", message: "Missing." });
  });

  it("copes with an error body that is not the envelope", async () => {
    const caught = await unwrap(result(undefined, "<html>", 502)).catch((e) => e);
    expect(caught).toMatchObject({ status: 502, code: "unknown_error" });
  });

  it("names the status, never the body, when there is no envelope", async () => {
    const failed = unwrap(result(undefined, "<html>Bad gateway</html>", 502));
    const caught = (await failed.catch((e: unknown) => e)) as ApiError;
    expect(caught.message).toBe("The server returned an error (HTTP 502).");
    expect(caught.message).not.toContain("html");
  });

  it("turns a network failure into status 0", async () => {
    const caught = await unwrap(Promise.reject(new TypeError("fetch failed"))).catch((e) => e);
    expect(caught).toMatchObject({ status: 0, code: "network_error" });
  });
});
