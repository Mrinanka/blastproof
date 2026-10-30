import { DIFFERENCES } from "@/lib/landing/content";

import { MonoText } from "./mono-text";
import { Reveal } from "./reveal";
import { Section } from "./section";

export function Different() {
  return (
    <Section
      id="different"
      eyebrow="What makes it different"
      title="Built around what the change does."
    >
      <ul className="grid gap-10 lg:grid-cols-3 lg:gap-0 lg:divide-x lg:divide-border">
        {DIFFERENCES.map((item, i) => (
          <li key={item.title} className="lg:px-8 lg:first:pl-0 lg:last:pr-0">
            <Reveal delay={i * 0.06}>
              <h3 className="text-xl font-medium tracking-tight text-balance">
                <MonoText text={item.title} />
              </h3>
              <p className="mt-3 leading-relaxed text-pretty text-muted-foreground">
                <MonoText text={item.text} />
              </p>
            </Reveal>
          </li>
        ))}
      </ul>
    </Section>
  );
}
