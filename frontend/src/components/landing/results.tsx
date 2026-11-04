import type { ReactNode } from "react";

import { formatDay } from "@/lib/format";
import { evalStats, liveStats } from "@/lib/landing/eval-stats";

import { NumberTicker } from "./effects/number-ticker";
import { Reveal } from "./reveal";
import { Section } from "./section";

type Stat = { value: ReactNode; of: string; label: string; detail: string };

// Only measured numbers (FE-29): the committed snapshots of the rules-only eval (D-293) and of a
// green live run with real models (D-309).
export function Results() {
  const s = evalStats();
  const l = liveStats();
  const stats: Stat[] = [
    {
      value: <NumberTicker value={s.incidentsCaught} />,
      of: `/${s.incidents}`,
      label: "real-incident replays caught",
      detail:
        "Replit-style mass delete, DROP TABLE, delete without WHERE, a psql bypass, pg_dump, hook-settings edits, credential hunting in .env and .pgpass.",
    },
    {
      value: <NumberTicker value={s.caught} />,
      of: `/${s.dangerous}`,
      label: "dangerous actions blocked or sent to review",
      detail: `A ${s.catchRatePercent}% catch rate across ${s.total} scenarios.`,
    },
    {
      value: <NumberTicker value={s.falseBlockPercent} decimals={1} />,
      of: "%",
      label: "false block rate",
      detail: `${s.falseBlocks} of ${s.expectedAllow} safe actions held for review instead of allowed.`,
    },
  ];
  const live: Stat[] = [
    {
      value: <NumberTicker value={l.factsEqual} />,
      of: `/${l.factsChecked}`,
      label: "write previews with the exact row counts",
      detail:
        "Every previewed impact matched its labelled counts. Code counts the rows; the model only explains them.",
    },
    {
      value: <NumberTicker value={l.readOverheadP95Ms} decimals={1} />,
      of: " ms",
      label: "added to a read, at p95",
      detail: `Measured over ${l.readsMeasured} reads. Reads take a fast path and never wait for a model.`,
    },
    {
      value: <NumberTicker value={l.previewP50Seconds} decimals={1} />,
      of: " s",
      label: "to preview a write, typically",
      detail: `The median of ${l.previewed} previewed writes (${l.previewP95Seconds} s at p95), including the AI summary and judge.`,
    },
  ];

  return (
    <Section
      id="results"
      eyebrow="Results"
      title="Measured on replays of real incidents."
      intro="Every scenario is a labelled action: dangerous ones must be blocked or held, safe ones must go through."
    >
      <Reveal>
        <h3 className="mb-3 font-mono text-xs tracking-wide text-muted-foreground uppercase">
          Rules only
        </h3>
        <StatGrid stats={stats} />
        <h3 className="mt-8 mb-3 font-mono text-xs tracking-wide text-muted-foreground uppercase">
          With real models
        </h3>
        <StatGrid stats={live} />
      </Reveal>
      <p className="mt-5 font-mono text-xs text-muted-foreground">
        Sources: rules-only eval, {s.total} scenarios, run {formatDay(s.day)}, also run in CI. Live
        eval with real models ({l.summaryModel} summaries, {l.judgeModel} judge), {l.total}{" "}
        scenarios, run {formatDay(l.day)} on GitHub Actions: it also caught {l.caught}/{l.dangerous}{" "}
        dangerous actions and held {l.falseBlocks} of {l.expectedAllow} safe ones for review.
      </p>
    </Section>
  );
}

function StatGrid({ stats }: { stats: Stat[] }) {
  return (
    <dl className="grid gap-px overflow-hidden rounded-xl border border-border bg-border md:grid-cols-3">
      {stats.map((stat) => (
        <div key={stat.label} className="flex flex-col bg-background p-6 sm:p-8">
          <dt className="order-2 mt-3 font-medium">{stat.label}</dt>
          <dd className="order-1 font-mono text-5xl font-medium tracking-tight sm:text-6xl">
            {stat.value}
            <span className="text-muted-foreground">{stat.of}</span>
          </dd>
          <dd className="order-3 mt-2 text-sm text-pretty text-muted-foreground">{stat.detail}</dd>
        </div>
      ))}
    </dl>
  );
}
