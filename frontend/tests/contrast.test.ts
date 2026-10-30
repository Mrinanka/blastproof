// F12 guards: FE-27 (readable color contrast, light and dark).
import { describe, expect, it } from "vitest";

import { TONE_CLASSES } from "../src/components/actions/tone";
import {
  GLOBALS_CSS,
  contrast,
  oklchToLinearRgb,
  luminance,
  palette,
  parseTokens,
} from "./contrast";

const MODES = { light: ":root", dark: ".dark" } as const;

describe("contrast helpers", () => {
  it("scores black on white 21 and a color on itself 1", () => {
    expect(contrast("oklch(0 0 0)", "oklch(1 0 0)")).toBeCloseTo(21, 1);
    expect(contrast("oklch(0.5 0.1 200)", "oklch(0.5 0.1 200)")).toBeCloseTo(1, 5);
  });

  it("converts a known color: oklch(0.5 0 0) is about 21.4% luminance", () => {
    expect(luminance(oklchToLinearRgb("oklch(0.5 0 0)"))).toBeCloseTo(0.125, 2);
  });
});

describe.each(Object.entries(MODES))("%s theme tokens", (_mode, selector) => {
  const t = parseTokens(GLOBALS_CSS, selector);
  const pairs: [string, string][] = [
    ["foreground", "background"],
    ["card-foreground", "card"],
    ["popover-foreground", "popover"],
    ["primary-foreground", "primary"],
    ["secondary-foreground", "secondary"],
    ["accent-foreground", "accent"],
    ["muted-foreground", "background"],
    ["muted-foreground", "card"],
    // Not muted-foreground on muted: 4.3:1 in light mode, but nothing renders that pair (D-286).
    ["sidebar-foreground", "sidebar"],
    ["sidebar-accent-foreground", "sidebar-accent"],
    ["sidebar-primary-foreground", "sidebar-primary"],
    ["destructive", "background"],
  ];
  it.each(pairs)("%s on %s is at least 4.5:1", (fg, bg) => {
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });
});

// F13: the landing page is dark only and sets its own tokens on `.landing`.
describe(".landing tokens", () => {
  const t = parseTokens(GLOBALS_CSS, ".landing");
  const pairs: [string, string][] = [
    ["foreground", "background"],
    ["foreground", "card"],
    ["muted-foreground", "background"],
    ["muted-foreground", "card"],
    ["primary-foreground", "primary"],
    ["signal", "background"],
    ["signal", "card"],
    ["safe", "background"],
    ["safe", "card"],
    ["signal-foreground", "signal"],
  ];
  it.each(pairs)("%s on %s is at least 4.5:1", (fg, bg) => {
    expect(contrast(t[fg]!, t[bg]!)).toBeGreaterThanOrEqual(4.5);
  });
});

describe("badge tones", () => {
  const color = (name: string, tokens: Record<string, string>) => tokens[name] ?? palette(name);

  describe.each(Object.entries(MODES))("%s", (mode, selector) => {
    const tokens = parseTokens(GLOBALS_CSS, selector);
    it.each(Object.entries(TONE_CLASSES))(
      "%s text on its background is at least 4.5:1",
      (_n, cls) => {
        const words = cls.split(/\s+/);
        const pick = (prefix: string) => {
          const dark = words.find((w) => w.startsWith(`dark:${prefix}-`));
          const light = words.find((w) => w.startsWith(`${prefix}-`));
          const chosen =
            mode === "dark" && dark
              ? dark.slice(6 + prefix.length)
              : light?.slice(prefix.length + 1);
          return chosen;
        };
        const fg = pick("text");
        // The neutral tone has no fill; it sits on the page.
        const bg = pick("bg") ?? "background";
        expect(fg, `${cls} has a text color`).toBeDefined();
        expect(contrast(color(fg!, tokens), color(bg, tokens))).toBeGreaterThanOrEqual(4.5);
      },
    );
  });
});
