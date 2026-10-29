import type { Schemas } from "@/lib/api/client";

export type Rule = Schemas["RuleView"];

// One condition as the rules file spells it: "statement_in: delete, update".
export function conditionText(key: string, value: unknown): string {
  if (Array.isArray(value)) return `${key}: ${value.join(", ")}`;
  if (value !== null && typeof value === "object") return `${key}: ${JSON.stringify(value)}`;
  return `${key}: ${String(value)}`;
}

export function passLabel(pass: Rule["pass"]): string {
  return pass === "pre" ? "Before preview" : "After preview";
}
