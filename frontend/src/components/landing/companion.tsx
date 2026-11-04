import { ExternalLink } from "lucide-react";

import { COMPANION } from "@/lib/landing/content";
import { AEGIS_URL } from "@/lib/landing/site";

import { Reveal } from "./reveal";
import { Section } from "./section";

// The owner's other tool, in one card (F13.1, D-306). Static, server-rendered, no client code.
export function Companion() {
  return (
    <Section id="aegiscontext" eyebrow={COMPANION.eyebrow} title={COMPANION.title}>
      <Reveal>
        <div className="landing-edge grid gap-8 rounded-xl bg-card/60 p-5 sm:p-8 md:grid-cols-2 md:gap-12">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-xl font-semibold">{COMPANION.name}</h3>
              <span className="rounded-full border border-border px-2.5 py-0.5 font-mono text-xs text-muted-foreground">
                {COMPANION.tag}
              </span>
            </div>
            {COMPANION.pitch.map((line) => (
              <p key={line} className="mt-4 text-pretty text-muted-foreground">
                {line}
              </p>
            ))}
            <a
              href={AEGIS_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-6 inline-flex items-center gap-1.5 rounded-sm text-sm text-foreground underline-offset-4 hover:underline"
            >
              {COMPANION.linkLabel}
              <ExternalLink aria-hidden="true" className="size-3.5" />
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </div>
          <div>
            <ul className="space-y-3">
              {COMPANION.facts.map((fact) => (
                <li key={fact} className="flex gap-3 text-sm text-pretty text-muted-foreground">
                  <span aria-hidden="true" className="font-mono text-signal">
                    ›
                  </span>
                  <span>{fact}</span>
                </li>
              ))}
            </ul>
            <p className="mt-6 font-mono text-sm text-foreground">{COMPANION.stat}</p>
            <p className="mt-1 text-xs text-pretty text-muted-foreground">{COMPANION.statNote}</p>
            <p className="mt-6 text-xs text-muted-foreground">{COMPANION.installLabel}</p>
            <div className="landing-edge mt-2 rounded-lg bg-background px-3 py-2.5">
              <code className="font-mono text-xs break-words text-foreground">
                {COMPANION.install}
              </code>
            </div>
          </div>
        </div>
      </Reveal>
    </Section>
  );
}
