export type Tone = "neutral" | "progress" | "warning" | "success" | "danger" | "muted";

// Layered on shadcn's Badge (outline variant); the text label always carries the meaning.
export const TONE_CLASSES: Record<Tone, string> = {
  neutral: "border-border text-foreground",
  progress: "border-transparent bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-200",
  warning: "border-transparent bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  success:
    "border-transparent bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
  danger: "border-transparent bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200",
  muted: "border-transparent bg-muted text-muted-foreground",
};
