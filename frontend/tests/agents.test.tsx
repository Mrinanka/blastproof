// @vitest-environment jsdom
// F11 guards: FE-24, DW-F11 (stand-in), SEC-27, SEC-32, ARCH-22.
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

import { Agents } from "../src/components/agents/agents";
import { makeQueryClient } from "../src/lib/query-client";
import { installDomStubs } from "./dom-stubs";

const KEY = "bp_live_SECRETKEY123";
const MCP_ADD = `claude mcp add --transport http blastproof http://x/mcp --header "Authorization: Bearer ${KEY}"`;
const ENV_LINE = `BLASTPROOF_AGENT_KEY=${KEY}`;

const agent = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id,
  name,
  key_prefix: "bp_ab12",
  is_active: true,
  created_at: "2026-10-29T10:00:00Z",
  owner: { id: "u1", display_name: "Olga Owner" },
  ...over,
});

interface Backend {
  role: string | "error";
  agents: ReturnType<typeof agent>[];
  total?: number;
  post?: () => Response | Promise<Response>;
  deactivate?: () => Response | Promise<Response>;
}
let backend: Backend;
let calls: { method: string; path: string; text: string }[];
let client: ReturnType<typeof makeQueryClient>;
let writeText: ReturnType<typeof vi.fn>;

const err = (status: number, message: string) =>
  Response.json({ error: { code: "x", message } }, { status });

function serve(over: Partial<Backend> = {}) {
  backend = { role: "member", agents: [agent("a1", "bot")], ...over };
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (req: Request) => {
      const path = new URL(req.url).pathname.replace("/api/blastproof", "");
      const text = req.method === "POST" ? await req.clone().text() : "";
      calls.push({ method: req.method, path, text });
      if (path === "/v1/auth/me") {
        if (backend.role === "error") return err(500, "Me failed");
        return Response.json({ id: "u", email: "a@x", display_name: "A", role: backend.role });
      }
      if (req.method === "GET" && path === "/v1/agents") {
        return Response.json({
          items: backend.agents,
          total: backend.total ?? backend.agents.length,
          limit: 100,
          offset: 0,
        });
      }
      if (req.method === "POST" && path === "/v1/agents") {
        if (backend.post) return backend.post();
        const created = agent("a2", JSON.parse(text).name);
        backend.agents = [...backend.agents, created];
        return Response.json({
          ...created,
          key: KEY,
          mcp_url: "http://x/mcp",
          claude_mcp_add: MCP_ADD,
          agent_key_env: ENV_LINE,
        });
      }
      if (req.method === "POST" && path.endsWith("/deactivate")) {
        if (backend.deactivate) return backend.deactivate();
        const id = path.split("/")[3];
        backend.agents = backend.agents.map((a) => (a.id === id ? { ...a, is_active: false } : a));
        return Response.json({});
      }
      return err(404, "Not found");
    }),
  );
}

function mount() {
  client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <Agents />
    </QueryClientProvider>,
  );
}
const sent = (method: string, prefix: string) =>
  calls.filter((c) => c.method === method && c.path.startsWith(prefix));
async function create(name = "  new bot ") {
  await screen.findByRole("table");
  fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: name } });
  fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
}

beforeEach(() => {
  installDomStubs();
  writeText = vi.fn(async () => {});
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the agent list", () => {
  it("lists agents as plain text with no Owner column for a member", async () => {
    serve({ agents: [agent("a1", "<b>x</b>"), agent("a2", "old", { is_active: false })] });
    mount();
    const table = within(await screen.findByRole("table"));
    expect(table.getByText("<b>x</b>")).toBeTruthy();
    expect(document.querySelector("table b")).toBeNull();
    expect(table.getByText("Active")).toBeTruthy();
    expect(table.getByText("Inactive")).toBeTruthy();
    expect(table.queryByText("Owner")).toBeNull();
    expect(table.getAllByRole("button", { name: "Deactivate" })).toHaveLength(1);
  });

  it("shows admins an Owner column", async () => {
    serve({ role: "admin" });
    mount();
    const table = within(await screen.findByRole("table"));
    expect(table.getByText("Owner")).toBeTruthy();
    expect(table.getByText("Olga Owner")).toBeTruthy();
  });

  it("notes when only the first 100 are shown", async () => {
    serve({ total: 150 });
    mount();
    expect(await screen.findByText(/Showing the first 100 of 150 agents/)).toBeTruthy();
  });

  it("fetches no agents when /me fails", async () => {
    serve({ role: "error" });
    mount();
    await screen.findByText("Me failed");
    expect(sent("GET", "/v1/agents")).toEqual([]);
  });
});

describe("creating an agent", () => {
  it("posts the trimmed name and shows the key and both lines exactly", async () => {
    serve();
    mount();
    await create();
    const dialog = within(await screen.findByRole("dialog"));
    expect(JSON.parse(sent("POST", "/v1/agents")[0].text)).toEqual({ name: "new bot" });
    expect(dialog.getByText(KEY)).toBeTruthy();
    expect(dialog.getByText(MCP_ADD)).toBeTruthy();
    expect(dialog.getByText(ENV_LINE)).toBeTruthy();
    expect(dialog.getByText(/shown once/)).toBeTruthy();
    for (const [label, value] of [
      ["Key", KEY],
      ["Claude Code setup", MCP_ADD],
      ["Hook environment", ENV_LINE],
    ]) {
      fireEvent.click(dialog.getByRole("button", { name: `Copy ${label}` }));
      await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(value));
    }
    expect(writeText).toHaveBeenCalledTimes(3);
  });

  it("toasts when the clipboard refuses, and the text stays visible", async () => {
    serve();
    mount();
    writeText.mockRejectedValue(new Error("denied"));
    await create();
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Copy Key" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(dialog.getByText(KEY)).toBeTruthy();
  });

  it("does not close on Escape or outside click, and Done drops the key everywhere", async () => {
    serve();
    mount();
    await create();
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape", code: "Escape" });
    await new Promise((r) => setTimeout(r, 20));
    fireEvent.pointerDown(document.body);
    fireEvent.click(document.body);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("dialog")).not.toBeNull();
    // No cache entry holds the key, even while the dialog is open.
    const cached = () =>
      JSON.stringify([
        ...client
          .getQueryCache()
          .getAll()
          .map((q) => q.state.data),
        ...client
          .getMutationCache()
          .getAll()
          .map((m) => [m.state.data, m.state.variables]),
      ]);
    expect(cached()).not.toContain(KEY);
    fireEvent.click(screen.getByRole("button", { name: "Done, I have copied it" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.body.textContent).not.toContain(KEY);
    expect(cached()).not.toContain(KEY);
  });

  it("sends one POST on a double click", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "bot2" } });
    const button = screen.getByRole("button", { name: "Create agent" });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByRole("dialog");
    expect(sent("POST", "/v1/agents")).toHaveLength(1);
  });

  it("refuses a blank name without a request", async () => {
    serve();
    mount();
    await create("   ");
    expect(await screen.findByText("Give the agent a name.")).toBeTruthy();
    expect(sent("POST", "/v1/agents")).toHaveLength(0);
  });

  it("shows a backend 422 inline and no dialog", async () => {
    serve({ post: () => err(422, "Name already used") });
    mount();
    await create();
    expect(await screen.findByText("Name already used")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("refreshes the list after creating", async () => {
    serve();
    mount();
    await create("fresh");
    await screen.findByRole("dialog");
    await waitFor(() => expect(sent("GET", "/v1/agents").length).toBeGreaterThanOrEqual(2));
  });
});

describe("deactivating", () => {
  it("asks first, and Cancel sends nothing", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/key stops working at once/)).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sent("POST", "/v1/agents/")).toHaveLength(0);
  });

  it("Confirm sends one POST even on a double click, then refetches", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    const confirm = within(await screen.findByRole("dialog")).getByRole("button", {
      name: "Deactivate",
    });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.getByText("Inactive")).toBeTruthy());
    expect(sent("POST", "/v1/agents/a1/deactivate")).toHaveLength(1);
    expect(toast.success).toHaveBeenCalled();
  });

  it("toasts a 404 and refetches", async () => {
    serve({ deactivate: () => err(404, "No such agent") });
    mount();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Deactivate" }),
    );
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("No such agent"));
    await waitFor(() => expect(sent("GET", "/v1/agents").length).toBeGreaterThanOrEqual(2));
  });
});
