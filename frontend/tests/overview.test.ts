// F9 guards: FE-22.
import { describe, expect, it } from "vitest";

import type { Schemas } from "../src/lib/api/client";
import { LATEST_LIMIT, OVERVIEW_POLL_MS, statCards } from "../src/lib/overview";

const stats = (over: Partial<Schemas["TodayStats"]> = {}): Schemas["StatsResponse"] => ({
  pending_reviews: 99,
  by_status: {},
  by_decision: {},
  today: {
    day: "2026-10-29",
    actions: 40,
    by_status: {},
    // The sum (30) differs from reviewed (7) on purpose.
    by_decision: { allow: 20, block: 6, review: 4 },
    reviewed: 7,
    blocked: 6,
    avg_preview_ms: 1234,
    llm_cost_usd: 0.0042,
    ...over,
  },
});

const byId = (cards: ReturnType<typeof statCards>) =>
  Object.fromEntries(cards.map((c) => [c.id, c]));

describe("statCards", () => {
  it("polls every 15 s and shows 10 rows", () => {
    expect(OVERVIEW_POLL_MS).toBe(15000);
    expect(LATEST_LIMIT).toBe(10);
  });

  it("keeps a fixed order", () => {
    expect(statCards(stats(), 3).map((c) => c.id)).toEqual([
      "pending",
      "reviewed",
      "blocked",
      "preview",
      "cost",
    ]);
  });

  it("takes each value from its own field", () => {
    const c = byId(statCards(stats(), 3));
    expect(c.pending.value).toBe("3");
    expect(c.reviewed.value).toBe("7");
    expect(c.blocked.value).toBe("6");
    expect(c.preview.value).toBe("1.2 s");
    expect(c.cost.value).toBe("$0.0042");
  });

  it("uses the pending count it is given, not stats.pending_reviews", () => {
    expect(byId(statCards(stats(), 3)).pending.value).not.toBe("99");
    expect(byId(statCards(stats(), undefined)).pending.value).toBe("—");
  });

  it("handles no previews and zero cost", () => {
    const c = byId(statCards(stats({ avg_preview_ms: null, llm_cost_usd: 0 }), 0));
    expect(c.preview.value).toBe("—");
    expect(c.preview.hint).toBe("No previews today");
    expect(c.cost.value).toBe("$0.00");
  });

  it("says UTC on every card except Pending", () => {
    for (const card of statCards(stats(), 1)) {
      if (card.id === "pending") expect(card.label).not.toContain("UTC");
      else expect(card.label).toContain("(UTC)");
    }
  });

  it("links Pending to the review queue", () => {
    expect(byId(statCards(stats(), 1)).pending.href).toBe("/dashboard/review");
  });
});
