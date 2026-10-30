// @vitest-environment jsdom
// F11 guards: FE-25, SEC-27, SEC-32, ARCH-22.
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
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Users } from "../src/components/users/users";
import { makeQueryClient } from "../src/lib/query-client";
import { installDomStubs } from "./dom-stubs";

const account = (email: string, role = "member") => ({
  id: email,
  email,
  display_name: "Ann",
  role,
  is_active: true,
  created_at: "2026-10-29T10:00:00Z",
});

interface Backend {
  role: string;
  users: ReturnType<typeof account>[];
  post?: () => Response | Promise<Response>;
}
let backend: Backend;
let calls: { method: string; path: string; text: string }[];

const err = (status: number, message: string) =>
  Response.json({ error: { code: "x", message } }, { status });

function serve(over: Partial<Backend> = {}) {
  backend = { role: "admin", users: [account("boss@x.io", "admin")], ...over };
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (req: Request) => {
      const path = new URL(req.url).pathname.replace("/api/blastproof", "");
      const text = req.method === "POST" ? await req.clone().text() : "";
      calls.push({ method: req.method, path, text });
      if (path === "/v1/auth/me") {
        return Response.json({ id: "u", email: "a@x", display_name: "A", role: backend.role });
      }
      if (req.method === "GET" && path === "/v1/users") {
        return Response.json({
          items: backend.users,
          total: backend.users.length,
          limit: 100,
          offset: 0,
        });
      }
      if (req.method === "POST" && path === "/v1/users") {
        if (backend.post) return backend.post();
        const body = JSON.parse(text);
        backend.users = [...backend.users, account(body.email, body.role)];
        return Response.json(account(body.email, body.role));
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
      <Users />
    </QueryClientProvider>,
  );
}
const posts = () => calls.filter((c) => c.method === "POST" && c.path === "/v1/users");
function fill(over: Record<string, string> = {}) {
  const v = { email: "new@x.io", name: "New One", password: "p".repeat(12), ...over };
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: v.email } });
  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: v.name } });
  fireEvent.change(screen.getByLabelText("Initial password"), { target: { value: v.password } });
}

beforeEach(() => installDomStubs());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("who sees the page", () => {
  it("tells a member it is for admins and sends no /v1/users request", async () => {
    serve({ role: "member" });
    mount();
    expect(await screen.findByText("Users is for admins.")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 30));
    expect(calls.filter((c) => c.path.startsWith("/v1/users"))).toEqual([]);
    expect(screen.queryByLabelText("Email")).toBeNull();
  });

  it("lists users for an admin, emails as plain text", async () => {
    serve({ users: [account("boss@x.io", "admin"), account("<i>m</i>@x.io")] });
    mount();
    const table = within(await screen.findByRole("table"));
    expect(table.getByText("boss@x.io")).toBeTruthy();
    expect(table.getByText("<i>m</i>@x.io")).toBeTruthy();
    expect(document.querySelector("table i")).toBeNull();
  });
});

describe("creating a user", () => {
  it("sends the fields, refetches, and clears the form", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));
    await screen.findByText("new@x.io");
    expect(JSON.parse(posts()[0].text)).toEqual({
      email: "new@x.io",
      display_name: "New One",
      password: "p".repeat(12),
      role: "member",
    });
    expect((screen.getByLabelText("Initial password") as HTMLInputElement).value).toBe("");
  });

  it("sends the chosen role", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    fill();
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "admin" } });
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(JSON.parse(posts()[0].text).role).toBe("admin");
  });

  it("keeps the password a password field", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    const field = screen.getByLabelText("Initial password") as HTMLInputElement;
    expect(field.type).toBe("password");
    expect(field.autocomplete).toBe("new-password");
  });

  it("blocks a short password with no request", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    fill({ password: "short" });
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));
    expect(await screen.findByText(/at least 12 characters/)).toBeTruthy();
    expect(posts()).toHaveLength(0);
  });

  it("shows a 409 on the email and keeps what was typed", async () => {
    serve({ post: () => err(409, "Email already registered") });
    mount();
    await screen.findByRole("table");
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));
    expect(await screen.findByText("Email already registered")).toBeTruthy();
    expect((screen.getByLabelText("Initial password") as HTMLInputElement).value).toBe(
      "p".repeat(12),
    );
  });

  it("shows a backend 422 inline", async () => {
    serve({ post: () => err(422, "Password too weak") });
    mount();
    await screen.findByRole("table");
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));
    expect(await screen.findByText("Password too weak")).toBeTruthy();
  });

  it("sends one POST on a double submit", async () => {
    serve();
    mount();
    await screen.findByRole("table");
    fill();
    const button = screen.getByRole("button", { name: "Create user" });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByText("new@x.io");
    expect(posts()).toHaveLength(1);
  });
});
