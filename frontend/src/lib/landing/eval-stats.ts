import snapshot from "./eval-results.json";

export type EvalSnapshot = typeof snapshot;

// The landing page's numbers, derived from the committed rules-only eval snapshot (D-293). Refresh
// the snapshot with `npm run sync:evals` after `uv run python -m evals.run`.
export function evalStats(s: EvalSnapshot = snapshot) {
  return {
    day: s.at.slice(0, 10),
    total: s.total,
    incidents: s.incidents,
    incidentsCaught: s.incidents_caught,
    dangerous: s.dangerous,
    caught: s.caught,
    catchRatePercent: Math.round((s.caught / s.dangerous) * 1000) / 10,
    expectedAllow: s.expected_allow,
    falseBlocks: s.false_blocks,
    falseBlockPercent: Math.round((s.false_blocks / s.expected_allow) * 1000) / 10,
  };
}
