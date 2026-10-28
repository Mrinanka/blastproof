import type { ActionDetail } from "../src/hooks/use-action";

export const ACTION_ID = "11111111-1111-4111-8111-111111111111";

// Replit-like numbers. Typed, so a schema change breaks typecheck.
export function makeAction(overrides: Partial<ActionDetail> = {}): ActionDetail {
  return {
    id: ACTION_ID,
    status: "awaiting_review",
    decision: "review",
    risk_level: "high",
    agent: { id: "ag1", name: "claude-1" },
    owner: { id: "u1", display_name: "Demo One" },
    intent: "Downgrade all users to free",
    sql_display: "UPDATE users SET plan = 'free'",
    pinned_values: {},
    original_sql_display: null,
    statement_type: "UPDATE",
    tables: ["users", "orders"],
    impact_report: {
      headline: "Changes the plan of 48,213 users",
      key_points: ["Every user is touched"],
      concerns: ["12 paying customers lose access"],
      source: "template",
    },
    table_changes: [
      { table: "users", deleted: 0, updated: 48213, inserted: 0, is_cascade: false },
      { table: "orders", deleted: 0, updated: 7, inserted: 0, is_cascade: true },
    ],
    breakdowns: [
      { table: "users", column: "plan", counts: { free: 48201, pro: 9, enterprise: 3 } },
    ],
    derived_groups: [{ table: "users", name: "paying", count: 12, definition: "plan != free" }],
    samples: [
      {
        table: "users",
        change: "updated",
        side: "before",
        values: { id: 1, email: "a***@example.com", plan: "pro" },
      },
      {
        table: "users",
        change: "updated",
        side: "after",
        values: { id: 1, email: "a***@example.com", plan: "free" },
      },
    ],
    judge: {
      provider: "fake",
      model: "judge-1",
      recommendation: "review",
      risk_level: "high",
      confidence: 0.87,
      reason: "Bulk change to paying users",
      failure: null,
    },
    matched_rules: [
      { name: "bulk-update", outcome: "review", reason: "Touches most of the table", pass: "pre" },
    ],
    knowledge_used: [
      {
        chunk_id: "c1",
        source: "schema.md",
        table_names: ["users"],
        first_line: "users holds one row per customer",
        match: "table",
        score: null,
      },
    ],
    policy_reason: null,
    reviewer_note: null,
    retry_of: null,
    retried_by: null,
    timeline: [
      { event_type: "received", created_at: "2026-10-29T10:00:00Z", data: {} },
      { event_type: "awaiting_review", created_at: "2026-10-29T10:00:05Z", data: {} },
    ],
    timings: { preview_ms: 900, total_ms: 1200 },
    llm_cost_usd: 0.001,
    decided_by: null,
    created_at: "2026-10-29T10:00:00Z",
    decided_at: null,
    expires_at: "2026-10-30T10:00:00Z",
    can_decide: true,
    ...overrides,
  };
}
