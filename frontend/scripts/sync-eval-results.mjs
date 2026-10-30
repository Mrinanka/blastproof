// Copies the landing page's eval numbers from backend/evals/results/rules.json (git-ignored, written
// by `uv run python -m evals.run`) into src/lib/landing/eval-results.json (committed), so the page
// can only publish measured numbers (FE-29). Run: npm run sync:evals
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../../backend/evals/results/rules.json", import.meta.url));
const target = fileURLToPath(new URL("../src/lib/landing/eval-results.json", import.meta.url));

export function snapshot(results) {
  const { meta, metrics, outcomes } = results;
  if (meta.mode !== "rules") throw new Error(`expected a rules-only run, got ${meta.mode}`);
  const incidents = outcomes.filter((o) => o.incident);
  return {
    at: meta.at,
    mode: meta.mode,
    total: metrics.total,
    measured: metrics.measured,
    dangerous: metrics.dangerous,
    caught: metrics.caught,
    expected_allow: metrics.expected_allow,
    false_blocks: metrics.false_blocks,
    incidents: incidents.length,
    incidents_caught: incidents.filter((o) => o.actual !== "allow").length,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const data = snapshot(JSON.parse(readFileSync(source, "utf8")));
  writeFileSync(target, `${JSON.stringify(data, null, 2)}\n`);
  console.log(`wrote ${target}`);
}
