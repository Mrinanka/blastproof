// @vitest-environment jsdom
// F5 guards: SEC-27 (agent text stays text), FE-12.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AuditTimeline } from "../src/components/actions/audit-timeline";
import { NumberBreakdown, countsToItems } from "../src/components/actions/number-breakdown";
import { SQLBlock } from "../src/components/actions/sql-block";

afterEach(cleanup);

describe("SQLBlock", () => {
  // F12: axe flags a scroll area that keyboard users cannot focus.
  it("is focusable and named, so its overflow can be scrolled by keyboard", () => {
    const { container } = render(<SQLBlock sql="SELECT 1" />);
    const pre = container.querySelector("pre")!;
    expect(pre.getAttribute("tabindex")).toBe("0");
    expect(pre.getAttribute("aria-label")).toBe("SQL");
  });

  it("renders markup as text and creates no element", () => {
    const { container } = render(<SQLBlock sql={"SELECT '<img src=x onerror=alert(1)>'"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("code")!.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("shows a placeholder for null", () => {
    render(<SQLBlock sql={null} />);
    expect(screen.getByText("No SQL recorded")).toBeTruthy();
  });

  it("keeps whitespace and newlines", () => {
    const { container } = render(<SQLBlock sql={"SELECT 1\n  FROM t"} />);
    expect(container.querySelector("code")!.textContent).toBe("SELECT 1\n  FROM t");
  });
});

describe("NumberBreakdown", () => {
  it("lists largest first with formatted counts", () => {
    const { container } = render(
      <NumberBreakdown items={countsToItems({ enterprise: 3, free: 48201, pro: 9 })} />,
    );
    expect(container.textContent).toBe("free48,201pro9enterprise3");
  });

  it("sorts ties by label", () => {
    expect(countsToItems({ b: 1, a: 1, c: 2 }).map((i) => i.label)).toEqual(["c", "a", "b"]);
  });

  it("shows No rows when empty", () => {
    render(<NumberBreakdown items={countsToItems({})} />);
    expect(screen.getByText("No rows")).toBeTruthy();
  });

  it("shows a hint", () => {
    render(<NumberBreakdown items={[{ label: "users", value: 2, hint: "deleted" }]} />);
    expect(screen.getByText("deleted")).toBeTruthy();
  });
});

describe("AuditTimeline", () => {
  const ev = (event_type: string, created_at: string, data = {}) => ({
    event_type,
    created_at,
    data,
  });

  it("labels known types and shows unknown ones raw", () => {
    render(
      <AuditTimeline
        events={[
          ev("shadow_done", "2026-10-27T10:00:00Z"),
          ev("fail_closed", "2026-10-27T10:00:01Z"),
          ev("decided", "2026-10-27T10:00:02Z"),
          ev("brand_new_event", "2026-10-27T10:00:03Z"),
        ]}
      />,
    );
    for (const text of ["Preview done", "Failed closed", "Decided", "brand_new_event"]) {
      expect(screen.getByText(text)).toBeTruthy();
    }
  });

  it("labels a status-named event with the status label", () => {
    render(<AuditTimeline events={[ev("awaiting_review", "2026-10-27T10:00:00Z")]} />);
    expect(screen.getByText("Awaiting review")).toBeTruthy();
  });

  it("keeps the backend order", () => {
    render(
      <AuditTimeline
        events={[ev("decided", "2026-10-27T10:00:09Z"), ev("received", "2026-10-27T10:00:01Z")]}
      />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("Decided");
    expect(items[1]).toContain("Received");
  });

  it("renders data values as text", () => {
    const { container } = render(
      <AuditTimeline
        events={[
          ev("decided", "2026-10-27T10:00:00Z", {
            note: "<b>bold</b>",
            count: 5,
            ok: true,
            nothing: null,
          }),
        ]}
      />,
    );
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("note: <b>bold</b>");
    expect(container.textContent).toContain("count: 5");
    expect(container.textContent).toContain("ok: true");
    expect(container.textContent).toContain("nothing: null");
  });

  it("shows the empty state", () => {
    render(<AuditTimeline events={[]} />);
    expect(screen.getByText("No events yet")).toBeTruthy();
  });
});
