// axe-core on a rendered container. jsdom has no layout, so color-contrast is off here and
// covered by contrast.test.ts. The page-level rules are off because a component test renders a
// fragment, not a page; the shell test renders the landmarks and the skip link.
import axe from "axe-core";
import { expect } from "vitest";

export async function expectNoAxeViolations(container: Element) {
  const results = await axe.run(container, {
    rules: {
      "color-contrast": { enabled: false },
      region: { enabled: false },
      "landmark-one-main": { enabled: false },
      "page-has-heading-one": { enabled: false },
    },
  });
  const report = results.violations.map(
    (v) => `${v.id}: ${v.help}\n${v.nodes.map((n) => `  ${n.target.join(" ")}`).join("\n")}`,
  );
  expect(report, report.join("\n")).toEqual([]);
}
