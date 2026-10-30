import { STEPS } from "@/lib/landing/content";

import { MonoText } from "./mono-text";
import { Reveal } from "./reveal";
import { Section } from "./section";

export function HowItWorks() {
  return (
    <Section
      id="how-it-works"
      eyebrow="How it works"
      title="Every write is tried on a copy before it touches production."
      intro="Reads take a fast path. Writes go through four steps, and only the last one can touch your data."
    >
      <ol className="relative grid gap-10 lg:grid-cols-4 lg:gap-8">
        {/* The line that joins the step numbers: vertical on mobile, horizontal on desktop. */}
        <span
          aria-hidden="true"
          className="absolute top-2 bottom-2 left-[15px] w-px bg-border lg:top-[15px] lg:right-4 lg:bottom-auto lg:left-4 lg:h-px lg:w-auto"
        />
        {STEPS.map((step, i) => (
          <li key={step.title} className="relative pl-12 lg:pl-0">
            <span className="absolute top-0 left-0 grid size-8 place-items-center rounded-md border border-border bg-background font-mono text-xs text-signal lg:static">
              {String(i + 1).padStart(2, "0")}
            </span>
            <Reveal delay={i * 0.06}>
              <h3 className="text-lg font-medium lg:mt-6">{step.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-pretty text-muted-foreground">
                <MonoText text={step.text} />
              </p>
            </Reveal>
          </li>
        ))}
      </ol>
    </Section>
  );
}
