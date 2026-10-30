import { ExternalLink } from "lucide-react";

import { CURRENT_OPTIONS, INCIDENTS } from "@/lib/landing/content";

import { Reveal } from "./reveal";
import { Section } from "./section";

export function Problem() {
  return (
    <Section
      id="problem"
      eyebrow="The problem"
      title="Agents already have write access. When they get it wrong, the damage is fast and real."
    >
      <Reveal>
        <ol className="landing-edge divide-y divide-border overflow-hidden rounded-xl bg-card/60">
          {INCIDENTS.map((incident) => (
            <li
              key={incident.url}
              className="grid gap-2 px-4 py-4 sm:grid-cols-[6.5rem_minmax(0,1fr)_auto] sm:items-baseline sm:gap-6 sm:px-5"
            >
              <time dateTime={incident.date} className="font-mono text-xs text-signal">
                {incident.when}
              </time>
              <p className="text-pretty text-foreground">{incident.text}</p>
              <a
                href={incident.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-sm font-mono text-xs whitespace-nowrap text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline"
              >
                {incident.source}
                <ExternalLink aria-hidden="true" className="size-3" />
                <span className="sr-only">
                  {" "}
                  (source for the {incident.when} incident, opens in a new tab)
                </span>
              </a>
            </li>
          ))}
        </ol>
      </Reveal>
      <Reveal className="mt-14">
        <h3 className="text-sm font-medium text-muted-foreground">Why the usual options fail</h3>
        <dl className="mt-4 grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-3">
          {CURRENT_OPTIONS.map((item) => (
            <div key={item.option} className="bg-background p-5">
              <dt className="font-mono text-sm text-foreground">{item.option}</dt>
              <dd className="mt-2 text-sm text-pretty text-muted-foreground">
                <span aria-hidden="true">→ </span>
                {item.result}
              </dd>
            </div>
          ))}
        </dl>
      </Reveal>
    </Section>
  );
}
