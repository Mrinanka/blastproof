import { formatDay } from "@/lib/format";
import { evalStats } from "@/lib/landing/eval-stats";

import { NumberTicker } from "./effects/number-ticker";
import { Reveal } from "./reveal";
import { Section } from "./section";

// Only measured numbers (FE-29): the committed snapshot of the rules-only eval (D-293). No
// live-model or latency numbers until those runs are green.
export function Results() {
  const s = evalStats();
  const stats = [
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

  return (
    <Section
      id="results"
      eyebrow="Results"
      title="Measured on replays of real incidents."
      intro="Every scenario is a labelled action: dangerous ones must be blocked or held, safe ones must go through."
    >
      <Reveal>
        <dl className="grid gap-px overflow-hidden rounded-xl border border-border bg-border md:grid-cols-3">
          {stats.map((stat) => (
            <div key={stat.label} className="flex flex-col bg-background p-6 sm:p-8">
              <dt className="order-2 mt-3 font-medium">{stat.label}</dt>
              <dd className="order-1 font-mono text-5xl font-medium tracking-tight sm:text-6xl">
                {stat.value}
                <span className="text-muted-foreground">{stat.of}</span>
              </dd>
              <dd className="order-3 mt-2 text-sm text-pretty text-muted-foreground">
                {stat.detail}
              </dd>
            </div>
          ))}
        </dl>
      </Reveal>
      <p className="mt-5 font-mono text-xs text-muted-foreground">
        Source: rules-only eval, {s.total} scenarios, run {formatDay(s.day)}, also run in CI. Model
        and latency numbers are not published yet.
      </p>
    </Section>
  );
}
