import { SECURITY } from "@/lib/landing/content";

import { Reveal } from "./reveal";
import { Section } from "./section";

export function Security() {
  return (
    <Section
      id="security"
      eyebrow="Security"
      title="The safety does not depend on the model."
      intro="The AI explains and can make a decision stricter. Everything that keeps your data safe is plain code."
    >
      <ol className="grid gap-x-12 gap-y-10 md:grid-cols-2">
        {SECURITY.map((item, i) => (
          <li key={item.title} className="border-t border-border pt-5">
            <Reveal delay={(i % 2) * 0.06}>
              <h3 className="flex items-baseline gap-3 font-medium">
                <span aria-hidden="true" className="font-mono text-xs text-signal">
                  {String(i + 1).padStart(2, "0")}
                </span>
                {item.title}
              </h3>
              <p className="mt-2 pl-8 text-sm leading-relaxed text-pretty text-muted-foreground">
                {item.text}
              </p>
            </Reveal>
          </li>
        ))}
      </ol>
    </Section>
  );
}
