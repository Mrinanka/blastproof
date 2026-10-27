// @vitest-environment jsdom
// F5 guards: CONV-17 (local time, one shared clock, no hydration mismatch).
import { act, cleanup, render } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LocalTime, TimeAgo } from "../src/components/common/time-ago";

const NOW = new Date("2026-10-27T12:00:00Z");
const ISO = "2026-10-27T11:57:00Z";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("TimeAgo", () => {
  it("shows relative text with dateTime and an absolute title", () => {
    const { container } = render(<TimeAgo iso={ISO} />);
    const el = container.querySelector("time")!;
    expect(el.textContent).toBe("3 minutes ago");
    expect(el.getAttribute("datetime")).toBe(ISO);
    expect(el.getAttribute("title")).toMatch(/Oct 27, 2026/);
  });

  it("updates after 30 s and not before", () => {
    const { container } = render(<TimeAgo iso="2026-10-27T11:59:20Z" />);
    const el = container.querySelector("time")!;
    expect(el.textContent).toBe("just now");
    act(() => void vi.advanceTimersByTime(29_000));
    expect(el.textContent).toBe("just now");
    act(() => void vi.advanceTimersByTime(2_000));
    expect(el.textContent).toBe("1 minute ago");
  });

  it("shares one interval and leaves none behind", () => {
    const a = render(<TimeAgo iso={ISO} />);
    const b = render(<LocalTime iso={ISO} />);
    expect(vi.getTimerCount()).toBe(1);
    a.unmount();
    expect(vi.getTimerCount()).toBe(1);
    b.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("renders UTC absolute text on the server, never relative text", () => {
    const html = renderToString(<TimeAgo iso={ISO} />);
    expect(html).toContain("11:57");
    expect(html).not.toContain("ago");
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("LocalTime", () => {
  it("shows seconds when asked", () => {
    const { container } = render(<LocalTime iso="2026-10-27T11:57:09Z" seconds />);
    expect(container.querySelector("time")!.textContent).toMatch(/:09/);
  });
});
