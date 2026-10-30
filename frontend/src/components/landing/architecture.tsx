import { FLOW } from "@/lib/landing/content";
import { cn } from "@/lib/utils";

import { Reveal } from "./reveal";
import { Section } from "./section";

// The README's Mermaid diagram, drawn on a fixed 100 x 100 grid: HTML nodes over one SVG with
// `preserveAspectRatio="none"`, so the lines stretch with the box and need no measuring or JS.
// Two CSS pulses travel the write path: to the owner (amber), then on to the commit (green).
// Desktop only; the ordered list below is the text version and the mobile view (D-297).
type Tone = "plain" | "signal" | "safe" | "core";
const NODES: { id: string; x: number; y: number; title: string; sub?: string; tone: Tone }[] = [
  { id: "agent", x: 9, y: 22, title: "AI agent", sub: "Claude Code", tone: "plain" },
  { id: "gw", x: 30, y: 22, title: "Blastproof backend", sub: "FastAPI", tone: "core" },
  { id: "preview", x: 51, y: 22, title: "Preview on a branch", tone: "plain" },
  { id: "rules", x: 71, y: 22, title: "Rules, then LLM judge", tone: "plain" },
  { id: "blocked", x: 92, y: 22, title: "Blocked", tone: "signal" },
  {
    id: "own",
    x: 30,
    y: 78,
    title: "Blastproof database",
    sub: "actions · audit · knowledge",
    tone: "plain",
  },
  { id: "company", x: 51, y: 78, title: "Company database", tone: "plain" },
  { id: "commit", x: 71, y: 78, title: "Commit what was previewed", tone: "safe" },
  { id: "owner", x: 92, y: 78, title: "Owner approves", sub: "in the dashboard", tone: "plain" },
];

const EDGES: { d: string; className?: string; dashed?: boolean }[] = [
  { d: "M9 22 Q19.5 14 30 22" },
  { d: "M9 22 Q19.5 30 30 22", dashed: true },
  { d: "M30 22 L51 78" },
  { d: "M30 22 L51 22" },
  { d: "M51 22 L71 22" },
  { d: "M71 22 L92 22", className: "stroke-signal/60" },
  { d: "M71 22 L71 78", className: "stroke-safe/60" },
  { d: "M71 22 L92 78" },
  { d: "M92 78 L71 78", className: "stroke-safe/60" },
  { d: "M71 78 L51 78", className: "stroke-safe/60" },
  { d: "M30 22 L30 78", dashed: true },
];

// Edge labels: position on the grid, and which side of the line they sit.
const LABELS: { text: string; x: number; y: number; place: "above" | "below" | "right" }[] = [
  { text: "MCP", x: 19.5, y: 22, place: "above" },
  { text: "hook", x: 19.5, y: 22, place: "below" },
  { text: "write", x: 40.5, y: 22, place: "above" },
  { text: "read", x: 40.5, y: 50, place: "right" },
  { text: "block", x: 81.5, y: 22, place: "above" },
  { text: "allow", x: 71, y: 50, place: "right" },
  { text: "review", x: 81.5, y: 50, place: "right" },
  { text: "approve", x: 81.5, y: 78, place: "above" },
];

const TONES: Record<Tone, string> = {
  plain: "",
  core: "border-signal/40",
  signal: "border-signal/50 text-signal",
  safe: "border-safe/50 text-safe",
};

export function Architecture() {
  return (
    <Section
      id="architecture"
      eyebrow="Architecture"
      title="One gateway between the agent and your data."
      intro="Reads go straight through. Writes take the long way, and the owner has the last word on anything risky."
    >
      <Reveal>
        <div aria-hidden="true" className="relative hidden h-[380px] lg:block">
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            className="absolute inset-0 size-full overflow-visible"
          >
            {EDGES.map((edge) => (
              <path
                key={edge.d}
                d={edge.d}
                fill="none"
                vectorEffect="non-scaling-stroke"
                strokeWidth={1}
                strokeDasharray={edge.dashed ? "4 4" : undefined}
                className={edge.className ?? "stroke-muted-foreground/40"}
              />
            ))}
            <path
              d="M9 22 L30 22 L51 22 L71 22 L92 78"
              pathLength={1}
              fill="none"
              vectorEffect="non-scaling-stroke"
              strokeWidth={2}
              strokeLinecap="round"
              className="landing-pulse landing-pulse-write stroke-signal"
            />
            <path
              d="M92 78 L71 78 L51 78"
              pathLength={1}
              fill="none"
              vectorEffect="non-scaling-stroke"
              strokeWidth={2}
              strokeLinecap="round"
              className="landing-pulse landing-pulse-commit stroke-safe"
            />
          </svg>
          {LABELS.map((label) => (
            <span
              key={label.text}
              style={{ left: `${label.x}%`, top: `${label.y}%` }}
              className={cn(
                "absolute bg-background px-1 font-mono text-[10.5px] text-muted-foreground",
                label.place === "above" && "-translate-x-1/2 -translate-y-[calc(100%+10px)]",
                label.place === "below" && "-translate-x-1/2 translate-y-[10px]",
                label.place === "right" && "translate-x-2 -translate-y-1/2",
              )}
            >
              {label.text}
            </span>
          ))}
          {NODES.map((node) => (
            <div
              key={node.id}
              style={{ left: `${node.x}%`, top: `${node.y}%` }}
              className={cn(
                "landing-edge absolute flex min-h-[4.5rem] w-36 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-lg bg-card px-3 py-2 text-center xl:w-40",
                TONES[node.tone],
              )}
            >
              <p className="text-[13px] leading-snug font-medium">{node.title}</p>
              {node.sub ? (
                <p className="mt-0.5 font-mono text-[10.5px] text-muted-foreground">{node.sub}</p>
              ) : null}
            </div>
          ))}
        </div>
      </Reveal>
      <ol className="space-y-3 lg:sr-only">
        {FLOW.map((line, i) => (
          <li key={line} className="flex gap-4 text-pretty text-muted-foreground">
            <span aria-hidden="true" className="font-mono text-xs leading-6 text-signal">
              {String(i + 1).padStart(2, "0")}
            </span>
            <span>{line}</span>
          </li>
        ))}
      </ol>
    </Section>
  );
}
