// Copies the landing page's eval numbers into committed snapshots, so the page can only publish
// measured numbers (FE-29).
// - `npm run sync:evals`: the rules-only run, from backend/evals/results/rules.json (git-ignored,
//   written by `uv run python -m evals.run`) into src/lib/landing/eval-results.json (D-293).
// - `npm run sync:evals -- --live <live.json>`: a green live run with real models, from the
//   `eval-results` artifact of the GitHub Evals workflow into src/lib/landing/live-eval-results.json
//   (D-309). A run with an error, a missed incident or no model answer is refused.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../../backend/evals/results/rules.json", import.meta.url));
const target = fileURLToPath(new URL("../src/lib/landing/eval-results.json", import.meta.url));
const liveTarget = fileURLToPath(
  new URL("../src/lib/landing/live-eval-results.json", import.meta.url),
);

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

const round1 = (n) => Math.round(n * 10) / 10;

export function liveSnapshot(results) {
  const { meta, metrics } = results;
  if (meta.mode !== "live") throw new Error(`expected a live run, got ${meta.mode}`);
  // Only a green run is published: the same gates as the eval's own exit code (D-045, D-276).
  if (metrics.errors.length > 0 || metrics.measured !== metrics.total)
    throw new Error("the live run has unmeasured scenarios");
  if (metrics.incident_misses.length > 0) throw new Error("the live run missed an incident");
  if (metrics.summaries_passed === 0) throw new Error("no model summary came back");
  const { previewed, read_overhead: reads } = metrics.latency;
  return {
    at: meta.at,
    mode: meta.mode,
    summary_model: meta.summary_model,
    judge_model: meta.judge_model,
    total: metrics.total,
    dangerous: metrics.dangerous,
    caught: metrics.caught,
    expected_allow: metrics.expected_allow,
    false_blocks: metrics.false_blocks,
    facts_checked: metrics.facts_checked,
    facts_equal: metrics.facts_equal,
    read_overhead_p95_ms: round1(reads.p95),
    reads_measured: reads.count,
    preview_p50_ms: Math.round(previewed.p50),
    preview_p95_ms: Math.round(previewed.p95),
    previewed: previewed.count,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const liveAt = process.argv.indexOf("--live");
  const [from, to, make] =
    liveAt === -1
      ? [source, target, snapshot]
      : [process.argv[liveAt + 1], liveTarget, liveSnapshot];
  if (!from) throw new Error("--live needs the path of a live.json");
  const data = make(JSON.parse(readFileSync(from, "utf8")));
  writeFileSync(to, `${JSON.stringify(data, null, 2)}\n`);
  console.log(`wrote ${to}`);
}
