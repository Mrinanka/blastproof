// F2 guards: FE-02, CONV-14 (client half), DW-F02. The type tests run under `npm run typecheck`.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import createClient from "openapi-fetch";
import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { api, API_BASE_URL, type ApiErrorBody, type Schemas } from "../src/lib/api/client";
import type { paths } from "../src/lib/api/schema";

// Never called: the body is only checked by the compiler, so no request is made.
async function typeChecks(action_id: string) {
  const detail = await api.GET("/v1/actions/{action_id}", { params: { path: { action_id } } });
  expectTypeOf(detail.data).toEqualTypeOf<Schemas["ActionDetail"] | undefined>();
  expectTypeOf(detail.error).toEqualTypeOf<ApiErrorBody | undefined>();
  if (detail.data) {
    expectTypeOf(detail.data.can_decide).toEqualTypeOf<boolean>();
    expectTypeOf(detail.data).toHaveProperty("sql_display");
  }

  const list = await api.GET("/v1/actions", {
    params: { query: { status: ["awaiting_review"], limit: 1 } },
  });
  if (list.data) {
    expectTypeOf(list.data).toHaveProperty("items");
    expectTypeOf(list.data.total).toEqualTypeOf<number>();
    expectTypeOf(list.data.limit).toEqualTypeOf<number>();
    expectTypeOf(list.data.offset).toEqualTypeOf<number>();
  }

  await api.POST("/v1/actions/{action_id}/reject", {
    params: { path: { action_id } },
    body: { reason: "no" },
  });
  // @ts-expect-error reject needs a body
  await api.POST("/v1/actions/{action_id}/reject", { params: { path: { action_id } } });

  // @ts-expect-error the old plan path does not exist
  await api.GET("/v1/actions/{id}", { params: { path: { id: action_id } } });
  // @ts-expect-error unknown path
  await api.GET("/v1/nope");
  // @ts-expect-error missing action_id
  await api.GET("/v1/actions/{action_id}", { params: { path: {} } });
  // @ts-expect-error GET-only path
  await api.POST("/v1/actions", { body: {} });
  if (detail.data) {
    // @ts-expect-error field does not exist
    void detail.data.no_such_field;
  }
}
void typeChecks;

describe("api client", () => {
  it("uses the same-origin proxy path", () => {
    expect(API_BASE_URL).toBe("/api/blastproof");
  });

  it("knows no backend address, token or env access", () => {
    const src = readFileSync(join(resolve(__dirname, ".."), "src/lib/api/client.ts"), "utf8");
    for (const bad of ["BLASTPROOF_API_URL", "http://", "https://", "process.env"]) {
      expect(src).not.toContain(bad);
    }
  });

  it("sends arrays as repeated params and adds no Authorization header", async () => {
    const fetchSpy = vi.fn(async () => Response.json({}));
    const client = createClient<paths>({
      baseUrl: `http://dashboard.test${API_BASE_URL}`,
      fetch: fetchSpy,
    });
    await client.GET("/v1/actions", {
      params: { query: { status: ["awaiting_review", "blocked"] as never, limit: 1 } },
    });
    const req = (fetchSpy.mock.calls[0] as unknown as [Request])[0];
    expect(req.method).toBe("GET");
    expect(req.url).toBe(
      "http://dashboard.test/api/blastproof/v1/actions?status=awaiting_review&status=blocked&limit=1",
    );
    expect(req.headers.get("authorization")).toBeNull();
  });
});
