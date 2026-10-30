// WCAG 2.x contrast for the design tokens. jsdom has no layout, so axe cannot check color-contrast.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export type Rgb = [number, number, number];

const read = (path: string) => readFileSync(resolve(__dirname, path), "utf8");
export const GLOBALS_CSS = read("../src/app/globals.css");
const TAILWIND_THEME = read("../node_modules/tailwindcss/theme.css");

// `oklch(0.5 0.1 20)`, `oklch(50% 0.1 20)` or `oklch(1 0 0 / 10%)`; alpha is returned separately.
export function parseOklch(value: string): { l: number; c: number; h: number; alpha: number } {
  const m = /oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+)(%?))?\s*\)/.exec(
    value,
  );
  if (!m) throw new Error(`not an oklch color: ${value}`);
  const alpha = m[5] === undefined ? 1 : m[6] ? Number(m[5]) / 100 : Number(m[5]);
  return { l: m[2] ? Number(m[1]) / 100 : Number(m[1]), c: Number(m[3]), h: Number(m[4]), alpha };
}

// Björn Ottosson's oklab -> linear sRGB (the form relative luminance needs).
export function oklchToLinearRgb(value: string): Rgb {
  const { l, c, h } = parseOklch(value);
  const a = c * Math.cos((h * Math.PI) / 180);
  const b = c * Math.sin((h * Math.PI) / 180);
  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin: Rgb = [
    4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
    -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
    -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
  ];
  const clamp = (x: number) => Math.min(1, Math.max(0, x));
  return lin.map(clamp) as Rgb;
}

export function luminance([r, g, b]: Rgb): number {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(fg: string, bg: string): number {
  const [a, b] = [luminance(oklchToLinearRgb(fg)), luminance(oklchToLinearRgb(bg))];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// Reads `--name: oklch(...)` declarations from a rule such as `:root` or `.dark`.
export function parseTokens(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(`\n${selector} {`);
  if (start < 0) throw new Error(`no ${selector} rule`);
  const body = css.slice(start, css.indexOf("\n}", start));
  const tokens: Record<string, string> = {};
  for (const m of body.matchAll(/--([\w-]+):\s*(oklch\([^)]*\))/g)) tokens[m[1]!] = m[2]!;
  return tokens;
}

// `blue-100` -> its oklch value in Tailwind's palette.
export function palette(name: string): string {
  const m = new RegExp(`--color-${name}:\\s*(oklch\\([^)]*\\))`).exec(TAILWIND_THEME);
  if (!m) throw new Error(`no palette color ${name}`);
  return m[1]!;
}
