// @vitest-environment jsdom
// D-304 guards: both packages get the redactor as beforeSend.
const { Analytics, SpeedInsights } = vi.hoisted(() => ({
  Analytics: vi.fn(() => null),
  SpeedInsights: vi.fn(() => null),
}));
vi.mock("@vercel/analytics/next", () => ({ Analytics }));
vi.mock("@vercel/speed-insights/next", () => ({ SpeedInsights }));

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VercelAnalytics } from "../src/components/analytics/vercel-analytics";
import { redactEvent } from "../src/lib/analytics";

afterEach(cleanup);

describe("VercelAnalytics", () => {
  it("passes the redactor to Web Analytics and Speed Insights", () => {
    render(<VercelAnalytics />);
    expect(Analytics).toHaveBeenCalledWith({ beforeSend: redactEvent }, undefined);
    expect(SpeedInsights).toHaveBeenCalledWith({ beforeSend: redactEvent }, undefined);
  });
});
