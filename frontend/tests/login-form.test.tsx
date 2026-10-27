// @vitest-environment jsdom
// F3 guards: FE-04 (form half).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "../src/components/auth/login-form";

const assign = vi.fn();

beforeEach(() => {
  vi.stubGlobal("location", { assign });
});
afterEach(() => {
  cleanup();
  assign.mockReset();
  vi.unstubAllGlobals();
});

function submit(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

const respond = (body: unknown, status = 200) => vi.fn(async () => Response.json(body, { status }));

describe("LoginForm", () => {
  it("shows an error and sends nothing for an invalid email", async () => {
    const fetchSpy = respond({});
    vi.stubGlobal("fetch", fetchSpy);
    render(<LoginForm />);
    submit("not-an-email", "pw");
    expect(await screen.findByText("Enter a valid email address.")).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows an error and sends nothing for an empty password", async () => {
    const fetchSpy = respond({});
    vi.stubGlobal("fetch", fetchSpy);
    render(<LoginForm />);
    submit("a@example.com", "");
    expect(await screen.findByText("Enter your password.")).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("posts the credentials and goes to next on success", async () => {
    const fetchSpy = respond({ display_name: "D", role: "member" });
    vi.stubGlobal("fetch", fetchSpy);
    render(<LoginForm next="/dashboard/review?x=1" />);
    submit("a@example.com", "pw");
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/dashboard/review?x=1"));
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(init.body as string)).toEqual({ email: "a@example.com", password: "pw" });
  });

  it("falls back to /dashboard for a hostile next", async () => {
    vi.stubGlobal("fetch", respond({}));
    render(<LoginForm next="//evil.com" />);
    submit("a@example.com", "pw");
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/dashboard"));
  });

  it("shows the backend message on a 401 and does not navigate", async () => {
    const body = { error: { code: "invalid_credentials", message: "Wrong email or password." } };
    vi.stubGlobal("fetch", respond(body, 401));
    render(<LoginForm />);
    submit("a@example.com", "pw");
    expect(await screen.findByText("Wrong email or password.")).toBeTruthy();
    expect(assign).not.toHaveBeenCalled();
  });

  it("shows a generic message for other failures", async () => {
    vi.stubGlobal("fetch", respond({ error: { message: "secret detail" } }, 500));
    render(<LoginForm />);
    submit("a@example.com", "pw");
    expect(await screen.findByText("Could not sign in. Try again.")).toBeTruthy();
    expect(screen.queryByText("secret detail")).toBeNull();
  });
});
