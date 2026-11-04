import snapshot from "./eval-results.json";
import liveSnapshot from "./live-eval-results.json";

export type EvalSnapshot = typeof snapshot;
export type LiveEvalSnapshot = typeof liveSnapshot;

const percent = (part: number, whole: number) => Math.round((part / whole) * 1000) / 10;

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
    catchRatePercent: percent(s.caught, s.dangerous),
    expectedAllow: s.expected_allow,
    falseBlocks: s.false_blocks,
    falseBlockPercent: percent(s.false_blocks, s.expected_allow),
  };
}

// "openai:anthropic/claude-sonnet-5.5" -> "claude-sonnet-5.5": the provider prefix and the route
// are config details; the model's own name is what a reader recognises.
export const modelName = (model: string) => model.slice(model.lastIndexOf("/") + 1);

// The numbers only a run with real models measures (D-309), from the committed snapshot of a green
// GitHub Evals run. Refresh with `npm run sync:evals -- --live <live.json>`.
export function liveStats(s: LiveEvalSnapshot = liveSnapshot) {
  return {
    day: s.at.slice(0, 10),
    total: s.total,
    summaryModel: modelName(s.summary_model),
    judgeModel: modelName(s.judge_model),
    dangerous: s.dangerous,
    caught: s.caught,
    expectedAllow: s.expected_allow,
    falseBlocks: s.false_blocks,
    factsChecked: s.facts_checked,
    factsEqual: s.facts_equal,
    readOverheadP95Ms: s.read_overhead_p95_ms,
    readsMeasured: s.reads_measured,
    previewP50Seconds: Math.round(s.preview_p50_ms / 100) / 10,
    previewP95Seconds: Math.round(s.preview_p95_ms / 100) / 10,
    previewed: s.previewed,
  };
}
