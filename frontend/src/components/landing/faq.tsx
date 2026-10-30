import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { FAQ } from "@/lib/landing/content";

import { Section } from "./section";

// forceMount keeps closed answers in the static HTML (crawlers, no-JS); CSS hides them (D-295).
export function Faq() {
  return (
    <Section id="faq" eyebrow="FAQ" title="Questions people ask first." split>
      <Accordion type="single" collapsible className="border-t border-border">
        {FAQ.map((item, i) => (
          <AccordionItem key={item.q} value={`q${i}`} className="border-b border-border">
            <AccordionTrigger className="items-center py-5 text-base hover:no-underline">
              {item.q}
            </AccordionTrigger>
            <AccordionContent
              forceMount
              className="pb-5 text-base text-pretty text-muted-foreground"
            >
              {item.a}
            </AccordionContent>
          </AccordionItem>
        ))}
      </Accordion>
    </Section>
  );
}
