// @vitest-environment jsdom
// F10 guards: FE-23, DW-F10 (stand-in), SEC-27, SEC-32, ARCH-22.
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Knowledge } from "../src/components/knowledge/knowledge";
import { makeQueryClient } from "../src/lib/query-client";
import { installDomStubs } from "./dom-stubs";

const source = (over: Record<string, unknown> = {}) => ({
  source: "schema_scan",
  kind: "schema_scan",
  chunks: 12,
  table_names: ["users", "orders"],
  updated_at: "2026-10-29T10:00:00Z",
  ...over,
});
const upload = (name: string, tables: string[] = []) =>
  source({ source: name, kind: "upload", chunks: 2, table_names: tables });
const system = (id: string, name: string) => ({
  id,
  name,
  kind: "postgres",
  connection_profile: "local",
});

interface Backend {
  role: string | "pending" | "error";
  sources: ReturnType<typeof source>[];
  systems: ReturnType<typeof system>[] | "error";
  post?: () => Response | Promise<Response>;
  del?: () => Response | Promise<Response>;
  scan?: () => Response | Promise<Response>;
}
let backend: Backend;
let picked = "";
let calls: { method: string; path: string; text: string }[];

const err = (status: number, message: string) =>
  Response.json({ error: { code: "x", message } }, { status });

function serve(over: Partial<Backend> = {}) {
  backend = { role: "admin", sources: [source()], systems: [system("s1", "Company")], ...over };
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (req: Request) => {
      const url = new URL(req.url);
      const path = url.pathname.replace("/api/blastproof", "");
      const text = req.method === "POST" ? await req.clone().text() : "";
      calls.push({ method: req.method, path, text });
      if (path === "/v1/auth/me") {
        if (backend.role === "pending") return new Promise<Response>(() => {});
        if (backend.role === "error") return err(500, "Me failed");
        return Response.json({ id: "u", email: "a@x", display_name: "A", role: backend.role });
      }
      if (req.method === "GET" && path === "/v1/knowledge") {
        return Response.json({
          items: backend.sources,
          total: backend.sources.length,
          limit: 100,
          offset: 0,
        });
      }
      if (req.method === "GET" && path === "/v1/systems") {
        if (backend.systems === "error") return err(503, "Systems down");
        return Response.json({
          items: backend.systems,
          total: backend.systems.length,
          limit: 100,
          offset: 0,
        });
      }
      if (req.method === "POST" && path === "/v1/knowledge") {
        if (backend.post) return backend.post();
        const name = picked;
        const tables = [...text.matchAll(/name="tables"\r\n\r\n([^\r]*)/g)].map((m) => m[1]);
        backend.sources = [
          ...backend.sources.filter((s) => s.source !== name),
          upload(name, tables),
        ];
        return Response.json(upload(name, tables));
      }
      if (req.method === "DELETE") {
        if (backend.del) return backend.del();
        const name = decodeURIComponent(path.split("/").pop()!);
        backend.sources = backend.sources.filter((s) => s.source !== name);
        return Response.json({});
      }
      if (req.method === "POST" && path.endsWith("/scan")) {
        return backend.scan
          ? backend.scan()
          : Response.json({ system_id: "s1", source: "schema_scan", chunks: 7 });
      }
      return err(404, "Not found");
    }),
  );
}

function mount() {
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <Knowledge />
    </QueryClientProvider>,
  );
}
const pick = (file: File) => {
  picked = file.name; // jsdom files lose their name in the multipart body
  fireEvent.change(screen.getByLabelText(/Document/), { target: { files: [file] } });
};
const sizedFile = (name: string, size: number) =>
  new File([new Uint8Array(size)], name, { type: "text/markdown" });
const sent = (method: string, prefix: string) =>
  calls.filter((c) => c.method === method && c.path.startsWith(prefix));
const knowledgeCalls = () => calls.filter((c) => /^\/v1\/(knowledge|systems)/.test(c.path));

beforeEach(() => {
  installDomStubs();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("who sees the page", () => {
  it("shows a member the list read-only: no write controls and no systems request", async () => {
    serve({ role: "member", sources: [source(), upload("a.md", ["users"])] });
    mount();
    const table = within(await screen.findByRole("table"));
    expect(table.getByText("a.md")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Upload" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Rescan/ })).toBeNull();
    expect(screen.queryByLabelText(/Document/)).toBeNull();
    expect(screen.queryByText("Rescan to replace")).toBeNull();
    expect(calls.filter((c) => c.path.startsWith("/v1/systems"))).toEqual([]);
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
  });

  it("fetches nothing while the role is unknown", async () => {
    serve({ role: "pending" });
    mount();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByRole("status")).toBeTruthy();
    expect(knowledgeCalls()).toEqual([]);
  });

  it("shows no data when the role lookup fails", async () => {
    serve({ role: "error" });
    mount();
    await screen.findByText("Me failed");
    expect(knowledgeCalls()).toEqual([]);
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("the source list", () => {
  it("shows kind, chunks and tables, and a hostile name as plain text (SEC-27)", async () => {
    serve({ sources: [source(), upload("<b>x</b>.md", ["users"])] });
    mount();
    const table = within(await screen.findByRole("table"));
    expect(table.getByText("Schema scan")).toBeTruthy();
    expect(table.getByText("12")).toBeTruthy();
    expect(table.getByText("<b>x</b>.md")).toBeTruthy();
    expect(table.getByText("users")).toBeTruthy();
    expect(document.querySelector("table b")).toBeNull();
  });

  it("keeps a usual source name whole instead of breaking it mid-word", async () => {
    serve({ sources: [source(), upload("schema_docs.md")] });
    mount();
    const cell = (await screen.findByText("schema_docs.md")).closest("td")!;
    expect(cell.className).toContain("min-w-40");
    expect(cell.className).toContain("[overflow-wrap:anywhere]");
    expect(cell.className).not.toContain("break-all");
  });

  it("offers no Delete on the schema scan row", async () => {
    serve({ sources: [source(), upload("a.md")] });
    mount();
    await screen.findByRole("table");
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(1);
    expect(screen.getByText("Rescan to replace")).toBeTruthy();
  });

  it("shows an error with Retry when the list fails", async () => {
    serve();
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", async (req: Request) =>
      new URL(req.url).pathname.endsWith("/v1/knowledge") ? err(500, "List broke") : original(req),
    );
    mount();
    await screen.findByText("List broke");
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});

describe("uploading", () => {
  it("sends the file with the ticked tables and then lists it (DW-F10 stand-in)", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    pick(new File(["# notes"], "notes.md"));
    fireEvent.click(screen.getByLabelText("users"));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() => expect(sent("POST", "/v1/knowledge")).toHaveLength(1));
    const body = sent("POST", "/v1/knowledge")[0].text;
    expect(body).toContain('name="file"');
    expect(body).toContain('name="tables"\r\n\r\nusers');
    const row = (await screen.findByText("notes.md")).closest("tr")!;
    expect(within(row).getByText("users")).toBeTruthy();
    expect(toast.success).toHaveBeenCalled();
  });

  it("refuses a 2 MB file and a .pdf without a request", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    pick(sizedFile("big.md", 2 * 1024 * 1024));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText(/larger than/)).toBeTruthy();
    pick(sizedFile("x.pdf", 10));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText(/Only \.md and \.txt files/)).toBeTruthy();
    // The owner's hand test: a comma in the name.
    pick(sizedFile("Act as a ruthless, elite technical.txt", 10));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText(/can't contain ","/)).toBeTruthy();
    expect(sent("POST", "/v1/knowledge")).toHaveLength(0);
  });

  it("refuses when no file is chosen", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText(/Choose a/)).toBeTruthy();
    expect(sent("POST", "/v1/knowledge")).toHaveLength(0);
  });

  it("says a large document can take a minute while the upload runs, and not after", async () => {
    let finish: (r: Response) => void = () => {};
    serve({ post: () => new Promise<Response>((r) => (finish = r)) });
    mount();
    await screen.findByRole("table");
    pick(new File(["x"], "a.md"));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText(/can take a minute to index/)).toBeTruthy();
    finish(Response.json({ source: "a.md", chunks: 1 }));
    await waitFor(() => expect(screen.queryByText(/can take a minute to index/)).toBeNull());
  });

  it("says an upload replaces an existing name", async () => {
    serve({ sources: [source(), upload("a.md")] });
    mount();
    await screen.findByRole("table");
    pick(new File(["x"], "a.md"));
    expect(screen.getByText(/replaces the existing a\.md/)).toBeTruthy();
  });

  it("offers no tags without a scan, and an untagged upload still works", async () => {
    serve({ sources: [] });
    mount();
    await screen.findByText("No sources yet");
    expect(screen.getByText(/Rescan the schema to tag tables/)).toBeTruthy();
    pick(new File(["x"], "a.md"));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    await waitFor(() => expect(sent("POST", "/v1/knowledge")).toHaveLength(1));
    expect(sent("POST", "/v1/knowledge")[0].text).not.toContain('name="tables"');
  });

  it.each([
    [403, "Admins only", "Admins only"],
    [413, "Too big for us", "Too big for us"],
    [422, "Not valid text", "Not valid text"],
    [503, "Embedder down", "Embedder down"],
    [504, "gateway", "may still be finishing"],
  ])("shows a %i inside the form and keeps the file", async (status, message, shown) => {
    serve({ post: () => err(status, message) });
    mount();
    await screen.findByRole("table");
    pick(new File(["x"], "a.md"));
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText(new RegExp(shown))).toBeTruthy();
    expect((screen.getByLabelText(/Document/) as HTMLInputElement).files).toHaveLength(1);
  });
});

describe("deleting", () => {
  it("asks first, and Cancel sends nothing", async () => {
    serve({ sources: [source(), upload("a.md")] });
    mount();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/a\.md \(2 chunks\)/)).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent("DELETE", "/v1/knowledge")).toHaveLength(0);
  });

  it("Confirm sends one DELETE even on a double click, toasts and refreshes", async () => {
    serve({ sources: [source(), upload("my notes.md")] });
    mount();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const confirm = within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Delete",
    });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByText("my notes.md")).toBeNull());
    expect(sent("DELETE", "/v1/knowledge")).toHaveLength(1);
    expect(sent("DELETE", "/v1/knowledge")[0].path).toBe("/v1/knowledge/my%20notes.md");
    expect(toast.success).toHaveBeenCalled();
  });

  it("toasts a 404 and refreshes the list", async () => {
    serve({ sources: [source(), upload("a.md")], del: () => err(404, "No such source") });
    mount();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }),
    );
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("No such source"));
    await waitFor(() => expect(sent("GET", "/v1/knowledge").length).toBeGreaterThanOrEqual(2));
  });
});

describe("rescanning", () => {
  it("has one button per system and toasts the count", async () => {
    serve({ systems: [system("s1", "Company"), system("s2", "Other")] });
    mount();
    const buttons = await screen.findAllByRole("button", { name: "Rescan schema" });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Schema rescanned: 7 chunks"));
    expect(sent("POST", "/v1/systems/s1/scan")).toHaveLength(1);
    await waitFor(() => expect(sent("GET", "/v1/knowledge").length).toBeGreaterThanOrEqual(2));
  });

  it("disables the buttons while a scan runs", async () => {
    let finish: (r: Response) => void = () => {};
    serve({ scan: () => new Promise<Response>((r) => (finish = r)) });
    mount();
    fireEvent.click((await screen.findAllByRole("button", { name: "Rescan schema" }))[0]);
    const busy = await screen.findByRole("button", { name: "Rescanning…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    finish(Response.json({ system_id: "s1", source: "schema_scan", chunks: 1 }));
    await screen.findByRole("button", { name: "Rescan schema" });
  });

  it("toasts a 503 from the backend", async () => {
    serve({ scan: () => err(503, "Company DB down") });
    mount();
    fireEvent.click((await screen.findAllByRole("button", { name: "Rescan schema" }))[0]);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Company DB down"));
  });

  it("shows a systems failure in the Schema section only", async () => {
    serve({ systems: "error" });
    mount();
    await screen.findByText("Systems down");
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Upload" })).toBeTruthy();
  });
});
