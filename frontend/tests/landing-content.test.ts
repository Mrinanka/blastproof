// F13 guards: FE-29 (only measured numbers), D-078 (exact demo counts), D-293 and D-309 (eval
// snapshots).
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { liveSnapshot, snapshot } from "../scripts/sync-eval-results.mjs";
import { COMPANION, REPLIT } from "../src/lib/landing/content";
import committed from "../src/lib/landing/eval-results.json";
import { evalStats, liveStats, modelName } from "../src/lib/landing/eval-stats";
import live from "../src/lib/landing/live-eval-results.json";
import { AEGIS_URL } from "../src/lib/landing/site";

const INCIDENTS_YAML = resolve(__dirname, "../../backend/evals/scenarios/incidents.yaml");
const RULES_JSON = resolve(__dirname, "../../backend/evals/results/rules.json");

function replitScenario(): string {
  const yaml = readFileSync(INCIDENTS_YAML, "utf8");
  const start = yaml.indexOf("id: replit-clean-up-inactive-users");
  expect(start).toBeGreaterThan(-1);
  // The checkout may have CRLF line endings (Windows).
  return yaml.slice(start, yaml.indexOf("- kind:", start)).replace(/\r\n/g, "\n");
}

describe("the Replit numbers match incidents.yaml", () => {
  const scenario = replitScenario();

  it("the SQL is copied exactly", () => {
    expect(scenario).toContain(`sql: ${REPLIT.sql}\n`);
    expect(scenario).toContain(`intent: ${REPLIT.intent}\n`);
  });

  it("the users, paying and enterprise counts", () => {
    expect(scenario).toContain(`users: {deleted: ${REPLIT.users}}`);
    expect(scenario).toContain(`users.paying: ${REPLIT.paying}`);
    expect(scenario).toMatch(new RegExp(`users\\.plan: \\{.*enterprise: ${REPLIT.enterprise}\\}`));
  });

  it.each(REPLIT.cascades.map((c) => [c.table, c.deleted]))("%s deletes %i", (table, n) => {
    expect(scenario).toContain(`${table}: {deleted: ${n}}`);
  });
});

describe("the eval snapshot", () => {
  it.skipIf(!existsSync(RULES_JSON))("equals backend/evals/results/rules.json", () => {
    // rules.json is git-ignored, so this runs where the eval has been run (locally), not in CI.
    expect(committed).toEqual(snapshot(JSON.parse(readFileSync(RULES_JSON, "utf8"))));
  });

  it("is a rules-only run that caught every incident and every dangerous action", () => {
    expect(committed.mode).toBe("rules");
    expect(committed.incidents_caught).toBe(committed.incidents);
    expect(committed.caught).toBe(committed.dangerous);
    expect(committed.dangerous + committed.expected_allow).toBe(committed.total);
  });

  it("the page's percentages are derived from it", () => {
    const s = evalStats();
    expect(s.catchRatePercent).toBe(100);
    expect(s.falseBlockPercent).toBe(
      Math.round((committed.false_blocks / committed.expected_allow) * 1000) / 10,
    );
    expect(s.day).toBe(committed.at.slice(0, 10));
  });

  it("the sync script refuses a live run and counts incidents from the outcomes", () => {
    const results = {
      meta: { at: "2026-10-28T00:00:00+00:00", mode: "live" },
      metrics: {},
      outcomes: [],
    };
    expect(() => snapshot(results)).toThrow(/rules-only/);
    const rules = {
      meta: { at: "x", mode: "rules" },
      metrics: { total: 3, dangerous: 2, caught: 2, expected_allow: 1, false_blocks: 0 },
      outcomes: [
        { incident: true, actual: "block" },
        { incident: true, actual: "allow" },
        { incident: false, actual: "allow" },
      ],
    };
    expect(snapshot(rules)).toMatchObject({ incidents: 2, incidents_caught: 1 });
  });
});

describe("the live eval snapshot (D-309)", () => {
  const green = () => ({
    meta: {
      at: "2026-11-04T00:00:00+00:00",
      mode: "live",
      summary_model: "a:x/s",
      judge_model: "b:y/j",
    },
    metrics: {
      total: 3,
      measured: 3,
      errors: [],
      incident_misses: [],
      summaries_passed: 1,
      dangerous: 2,
      caught: 2,
      expected_allow: 1,
      false_blocks: 0,
      facts_checked: 2,
      facts_equal: 2,
      latency: {
        previewed: { count: 2, p50: 8602.4, p95: 18171.2 },
        read_overhead: { count: 100, p50: 7.4, p95: 9.441 },
      },
    },
    outcomes: [],
  });

  it("is a green live run with real models", () => {
    expect(live.mode).toBe("live");
    expect(live.caught).toBe(live.dangerous);
    expect(live.facts_equal).toBe(live.facts_checked);
    expect(live.summary_model).toMatch(/^[a-z_]+:\S+$/);
    expect(live.judge_model).toMatch(/^[a-z_]+:\S+$/);
  });

  it("the page's live numbers are derived from it", () => {
    const l = liveStats();
    expect(l.day).toBe(live.at.slice(0, 10));
    expect(l.readOverheadP95Ms).toBe(live.read_overhead_p95_ms);
    expect(l.previewP50Seconds).toBe(Math.round(live.preview_p50_ms / 100) / 10);
    expect(l.judgeModel).toBe(live.judge_model.slice(live.judge_model.lastIndexOf("/") + 1));
  });

  it("the model name drops the provider and the route", () => {
    expect(modelName("openai:anthropic/claude-sonnet-5.5")).toBe("claude-sonnet-5.5");
    expect(modelName("google_genai:gemini-3.8-flash")).toBe("google_genai:gemini-3.8-flash");
  });

  it("the sync script takes a green live run and rounds the latencies", () => {
    expect(liveSnapshot(green())).toMatchObject({
      judge_model: "b:y/j",
      read_overhead_p95_ms: 9.4,
      preview_p50_ms: 8602,
      preview_p95_ms: 18171,
    });
  });

  it.each([
    ["a rules-only run", (r: ReturnType<typeof green>) => (r.meta.mode = "rules"), /live run/],
    [
      "an errored scenario",
      (r: ReturnType<typeof green>) => (r.metrics.measured = 2),
      /unmeasured/,
    ],
    [
      "a missed incident",
      (r: ReturnType<typeof green>) => (r.metrics.incident_misses = ["x"] as never[]),
      /missed an incident/,
    ],
    [
      "no model summary",
      (r: ReturnType<typeof green>) => (r.metrics.summaries_passed = 0),
      /no model summary/,
    ],
  ])("the sync script refuses %s", (_name, spoil, message) => {
    const run = green();
    spoil(run);
    expect(() => liveSnapshot(run)).toThrow(message);
  });
});

describe("the AegisContext companion copy (D-306)", () => {
  it("points at the plugin's repository", () => {
    expect(AEGIS_URL).toBe("https://github.com/Mrinanka/aegiscontext");
  });

  it("has two pitch lines and three facts", () => {
    expect(COMPANION.pitch).toHaveLength(2);
    expect(COMPANION.facts).toHaveLength(3);
  });

  it("the install line names the same repository as the link", () => {
    expect(COMPANION.install).toBe(
      "/plugin install aegiscontext --marketplace Mrinanka/aegiscontext",
    );
    expect(AEGIS_URL.endsWith(`/${COMPANION.install.split("--marketplace ")[1]}`)).toBe(true);
  });

  it("the stat line carries the README's figures (135 of 138, 41 of 41), not 135/135", () => {
    expect(COMPANION.stat).toContain("135 of 138");
    expect(COMPANION.stat).toContain("41 of 41");
    expect(COMPANION.stat).not.toContain("135/135");
  });
});
