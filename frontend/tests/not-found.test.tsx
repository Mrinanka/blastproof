// @vitest-environment jsdom
// F12 guards: FE-27 (friendly 404).
const { notFound } = vi.hoisted(() => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));
vi.mock("next/navigation", () => ({ notFound }));

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RootNotFound, { metadata } from "../src/app/not-found";
import DashboardNotFound from "../src/app/dashboard/not-found";
import MissingPage from "../src/app/dashboard/[...missing]/page";
import { installDomStubs } from "./dom-stubs";

beforeEach(() => installDomStubs());
afterEach(cleanup);

describe("404 pages", () => {
  it("the dashboard 404 has a heading and a link back", () => {
    render(<DashboardNotFound />);
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("/dashboard");
  });

  it("the root 404 has a heading and a link back, and a title", () => {
    render(<RootNotFound />);
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("/dashboard");
    expect(metadata.title).toBe("Page not found");
  });

  it("the catch-all calls notFound()", () => {
    expect(() => MissingPage()).toThrow("NEXT_NOT_FOUND");
    expect(notFound).toHaveBeenCalled();
  });
});
