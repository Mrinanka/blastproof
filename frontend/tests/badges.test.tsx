// @vitest-environment jsdom
// F5 guards: FE-12 (status and risk badges).
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RISK_META, RiskBadge } from "../src/components/actions/risk-badge";
import { STATUS_META, StatusBadge } from "../src/components/actions/status-badge";

afterEach(cleanup);

describe("StatusBadge", () => {
  it("gives every status a non-empty, unique label", () => {
    const labels = Object.values(STATUS_META).map((m) => m.label);
    expect(labels.every((l) => l.length > 0)).toBe(true);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it.each([
    ["awaiting_review", "Awaiting review", "warning"],
    ["committed", "Committed", "success"],
    ["blocked", "Blocked", "danger"],
    ["expired", "Expired", "muted"],
  ])("%s -> %s (%s)", (status, label, tone) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label).getAttribute("data-tone")).toBe(tone);
  });

  it("shows an unknown status raw with the neutral tone", () => {
    render(<StatusBadge status="teleported" />);
    expect(screen.getByText("teleported").getAttribute("data-tone")).toBe("neutral");
  });
});

describe("RiskBadge", () => {
  it.each(Object.entries(RISK_META))("%s", (level, meta) => {
    render(<RiskBadge level={level} />);
    expect(screen.getByText(meta.label).getAttribute("data-tone")).toBe(meta.tone);
  });

  it("shows No risk score for null", () => {
    render(<RiskBadge level={null} />);
    expect(screen.getByText("No risk score").getAttribute("data-tone")).toBe("muted");
  });

  it("shows an unknown level raw", () => {
    render(<RiskBadge level="extreme" />);
    expect(screen.getByText("extreme").getAttribute("data-tone")).toBe("neutral");
  });
});
