// @vitest-environment jsdom
// F10 guards: FE-23 (hooks), ARCH-22.
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});

import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useDeleteKnowledge, useKnowledge, useUploadKnowledge } from "../src/hooks/use-knowledge";
import { useRescan } from "../src/hooks/use-systems";
import { ApiError } from "../src/lib/api/errors";
import { makeQueryClient } from "../src/lib/query-client";

function setup() {
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, client };
}
const page = () => Response.json({ items: [], total: 0, limit: 100, offset: 0 });
const requestOf = (spy: ReturnType<typeof vi.fn>, n = 0) => spy.mock.calls[n][0] as Request;

afterEach(() => vi.unstubAllGlobals());

describe("knowledge hooks", () => {
  it("lists the first 100 sources", async () => {
    const spy = vi.fn(async () => page());
    vi.stubGlobal("fetch", spy);
    const { result } = renderHook(() => useKnowledge(true), { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    const url = new URL(requestOf(spy).url);
    expect(url.pathname + url.search).toBe("/api/blastproof/v1/knowledge?limit=100");
  });

  it("fetches nothing while disabled", async () => {
    const spy = vi.fn(async () => page());
    vi.stubGlobal("fetch", spy);
    renderHook(() => useKnowledge(false), { wrapper: setup().wrapper });
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();
  });

  it("uploads multipart with the file and the tables, then refetches the list", async () => {
    const spy = vi.fn(async (req: Request) =>
      req.method === "POST" ? Response.json({ source: "n.md" }) : page(),
    );
    vi.stubGlobal("fetch", spy);
    const { wrapper } = setup();
    const { result } = renderHook(() => ({ list: useKnowledge(true), up: useUploadKnowledge() }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.list.data).toBeDefined());
    result.current.up.mutate({ file: new File(["hello"], "n.md"), tables: ["users", "orders"] });
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThanOrEqual(3));
    const post = spy.mock.calls.map((c) => c[0] as Request).find((r) => r.method === "POST")!;
    const type = post.headers.get("content-type") ?? "";
    expect(type).toMatch(/^multipart\/form-data; boundary=/);
    // jsdom's File reaches Node's multipart encoder as an empty blob (a test-environment limit,
    // not a browser one), so the file's bytes are covered by uploadForm's unit test.
    const text = await post.text();
    expect(text).toContain('name="file"; filename=');
    const tables = [...text.matchAll(/name="tables"\r\n\r\n([^\r]*)/g)].map((m) => m[1]);
    expect(tables).toEqual(["users", "orders"]);
  });

  it("encodes a source name with a space on delete and refreshes the list", async () => {
    const spy = vi.fn(async () => page());
    vi.stubGlobal("fetch", spy);
    const { wrapper } = setup();
    const { result } = renderHook(() => ({ list: useKnowledge(true), del: useDeleteKnowledge() }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.list.data).toBeDefined());
    result.current.del.mutate("my notes.md");
    await waitFor(() => expect(spy.mock.calls.length).toBe(3));
    const del = requestOf(spy, 1);
    expect(del.method).toBe("DELETE");
    expect(new URL(del.url).pathname).toBe("/api/blastproof/v1/knowledge/my%20notes.md");
  });

  it("rescans the right system and refreshes the list", async () => {
    const spy = vi.fn(async (req: Request) =>
      req.method === "POST"
        ? Response.json({ system_id: "s1", source: "schema_scan", chunks: 4 })
        : page(),
    );
    vi.stubGlobal("fetch", spy);
    const { wrapper } = setup();
    const { result } = renderHook(() => ({ list: useKnowledge(true), scan: useRescan() }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.list.data).toBeDefined());
    result.current.scan.mutate("sys-1");
    await waitFor(() => expect(spy.mock.calls.length).toBe(3));
    const post = requestOf(spy, 1);
    expect(post.method).toBe("POST");
    expect(new URL(post.url).pathname).toBe("/api/blastproof/v1/systems/sys-1/scan");
  });

  it("turns a 503 envelope into an ApiError and a network failure into status 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: "x", message: "Down" } }, { status: 503 })),
    );
    const a = renderHook(() => useUploadKnowledge(), { wrapper: setup().wrapper });
    a.result.current.mutate({ file: new File(["x"], "n.md"), tables: [] });
    await waitFor(() => expect(a.result.current.isError).toBe(true));
    expect((a.result.current.error as ApiError).message).toBe("Down");
    expect((a.result.current.error as ApiError).status).toBe(503);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    );
    const b = renderHook(() => useUploadKnowledge(), { wrapper: setup().wrapper });
    b.result.current.mutate({ file: new File(["x"], "n.md"), tables: [] });
    await waitFor(() => expect(b.result.current.isError).toBe(true));
    expect((b.result.current.error as ApiError).status).toBe(0);
  });
});
