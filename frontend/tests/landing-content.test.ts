// F13 guards: FE-29 (only measured numbers), D-078 (exact demo counts), D-293 (eval snapshot).
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { snapshot } from "../scripts/sync-eval-results.mjs";
import { REPLIT } from "../src/lib/landing/content";
import committed from "../src/lib/landing/eval-results.json";
import { evalStats } from "../src/lib/landing/eval-stats";

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
