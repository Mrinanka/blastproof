import {
  Ban,
  Bot,
  Database,
  GitBranch,
  GitCommitHorizontal,
  Lock,
  Scale,
  ScrollText,
  ShieldCheck,
  UserCheck,
} from "lucide-react";

import { FLOW } from "@/lib/landing/content";
import { cn } from "@/lib/utils";

import {
  BOUNDARY,
  CYCLE,
  EDGES,
  GLOWS,
  JOURNEYS,
  LABELS,
  NODES,
  NODE_BY_ID,
  ROUTES,
  VIEW,
  box,
  chevron,
  fmtSeries,
  glowRect,
  type EdgeDef,
  type EdgeTone,
  type IconName,
  type NodeDef,
  type PacketColor,
  type Series,
  type Tone,
} from "./architecture-geometry";
import { BorderBeam } from "./effects/border-beam";
import { Reveal } from "./reveal";
import { Section } from "./section";

// The README's Mermaid diagram as one picture on a fixed 1104 x 500 canvas (F13.2, D-307, which
// supersedes D-297). The SVG keeps its aspect ratio and the cards are placed in the same units,
// so lines meet card edges at every width and round shapes stay round. Everything is computed
// from constants in architecture-geometry.ts: nothing is measured in the browser, there is no
// client component, and the whole picture is in the server HTML. The packets and node glows are
// SVG animations on one shared clock inside `.landing-arch-motion`, which reduced motion hides,
// leaving a complete static picture. Desktop only; the ordered list below is the text version
// and the mobile view.

const ICONS: Record<IconName, typeof Bot> = {
  Bot,
  ShieldCheck,
  ScrollText,
  GitBranch,
  Scale,
  UserCheck,
  GitCommitHorizontal,
  Ban,
  Database,
};

const NODE_TONE: Record<Tone, { card: string; icon: string }> = {
  plain: { card: "", icon: "" },
  core: {
    card: "landing-node-core border-signal/40",
    icon: "border-signal/30 bg-signal/12 text-signal",
  },
  signal: { card: "border-signal/35", icon: "text-signal" },
  safe: { card: "border-safe/35", icon: "text-safe" },
};

const LABEL_TONE = {
  muted: "text-muted-foreground",
  signal: "text-signal",
  safe: "text-safe",
} as const;

// Gradient stops along each edge, from its first point to its last.
const STOPS: Record<EdgeTone, { at: number; color: string; alpha: number }[]> = {
  neutral: [
    { at: 0, color: "foreground", alpha: 0.16 },
    { at: 1, color: "foreground", alpha: 0.26 },
  ],
  write: [
    { at: 0, color: "foreground", alpha: 0.16 },
    { at: 1, color: "signal", alpha: 0.45 },
  ],
  signal: [
    { at: 0, color: "foreground", alpha: 0.22 },
    { at: 0.35, color: "signal", alpha: 0.55 },
    { at: 1, color: "signal", alpha: 0.55 },
  ],
  safe: [
    { at: 0, color: "foreground", alpha: 0.22 },
    { at: 0.35, color: "safe", alpha: 0.55 },
    { at: 1, color: "safe", alpha: 0.55 },
  ],
  approve: [
    { at: 0, color: "signal", alpha: 0.55 },
    { at: 1, color: "safe", alpha: 0.55 },
  ],
};

const CHEVRON_STROKE: Record<EdgeTone, string> = {
  neutral: "stroke-foreground/40",
  write: "stroke-signal/60",
  signal: "stroke-signal/70",
  safe: "stroke-safe/70",
  approve: "stroke-safe/70",
};

const PACKET_FILL: Record<PacketColor, string> = {
  neutral: "fill-foreground",
  signal: "fill-signal",
  safe: "fill-safe",
};

const GLOW_FILL: Record<PacketColor, string> = {
  neutral: "fill-foreground/30",
  signal: "fill-signal/60",
  safe: "fill-safe/60",
};

const pctX = (x: number) => `${(x / VIEW.w) * 100}%`;
const pctY = (y: number) => `${(y / VIEW.h) * 100}%`;
const gradientId = (edge: EdgeDef) => `arch-line-${edge.id}`;
const CLOCK = { dur: `${CYCLE}s`, repeatCount: "indefinite" } as const;

// An SVG animation of one number over the shared clock; a flat series needs none.
function Animate({
  attr,
  series,
  type,
}: {
  attr: "opacity" | "transform";
  series: Series;
  type?: "scale";
}) {
  if (series.values.every((v) => v === series.values[0])) return null;
  const common = {
    attributeName: attr,
    values: fmtSeries(series.values),
    keyTimes: fmtSeries(series.keyTimes),
    ...CLOCK,
  };
  return type ? <animateTransform {...common} type={type} /> : <animate {...common} />;
}

function NodeCard({ node }: { node: NodeDef }) {
  const b = box(node);
  const Icon = ICONS[node.icon];
  const tone = NODE_TONE[node.tone];
  return (
    <div
      style={{
        left: pctX(b.left),
        top: pctY(b.top),
        width: pctX(node.w),
        height: pctY(node.h),
      }}
      className={cn("landing-node", tone.card)}
    >
      <span className={cn("landing-node-icon", tone.icon)}>
        <Icon className="size-[56%]" strokeWidth={1.75} />
      </span>
      <p className="landing-node-title">{node.title}</p>
      {node.sub ? <p className="landing-node-sub">{node.sub}</p> : null}
      {node.tone === "core" ? <BorderBeam /> : null}
    </div>
  );
}

export function Architecture() {
  const core = NODE_BY_ID.gw;
  return (
    <Section
      id="architecture"
      eyebrow="Architecture"
      title="One gateway between the agent and your data."
      intro="Reads go straight through. Writes take the long way, and the owner has the last word on anything risky."
    >
      <Reveal>
        <div aria-hidden="true" className="landing-arch relative mx-auto hidden w-full lg:block">
          <div className="landing-arch-grid" />
          <div className="landing-halo" style={{ left: pctX(core.cx), top: pctY(core.cy) }} />
          <svg
            viewBox={`0 0 ${VIEW.w} ${VIEW.h}`}
            className="absolute inset-0 size-full overflow-visible"
          >
            <defs>
              <filter id="arch-blur" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="9" />
              </filter>
              {EDGES.map((edge) => {
                const first = edge.points[0]!;
                const last = edge.points[edge.points.length - 1]!;
                return (
                  <linearGradient
                    key={edge.id}
                    id={gradientId(edge)}
                    gradientUnits="userSpaceOnUse"
                    x1={first[0]}
                    y1={first[1]}
                    x2={last[0]}
                    y2={last[1]}
                  >
                    {STOPS[edge.tone].map((stop) => (
                      <stop
                        key={stop.at}
                        offset={stop.at}
                        style={{ stopColor: `var(--${stop.color})`, stopOpacity: stop.alpha }}
                      />
                    ))}
                  </linearGradient>
                );
              })}
            </defs>
            <rect
              x={BOUNDARY.x}
              y={BOUNDARY.y}
              width={BOUNDARY.w}
              height={BOUNDARY.h}
              rx={BOUNDARY.rx}
              fill="none"
              strokeDasharray="4 4"
              className="stroke-foreground/22"
            />
            {EDGES.map((edge) => {
              const d = ROUTES[edge.id].d;
              return (
                <g key={edge.id} fill="none" strokeLinecap="round" strokeLinejoin="round">
                  {edge.glow ? (
                    <path
                      d={d}
                      stroke={`url(#${gradientId(edge)})`}
                      strokeWidth={6}
                      opacity={0.1}
                    />
                  ) : null}
                  <path
                    d={d}
                    stroke={`url(#${gradientId(edge)})`}
                    strokeWidth={1.25}
                    strokeDasharray={edge.dashed ? "5 5" : undefined}
                  />
                  {edge.arrow ? (
                    <path
                      d={chevron(edge.points)}
                      strokeWidth={1.25}
                      className={CHEVRON_STROKE[edge.tone]}
                    />
                  ) : null}
                </g>
              );
            })}
            <g className="landing-arch-motion">
              {GLOWS.map((glow) => {
                const r = glowRect(NODE_BY_ID[glow.node]);
                return (
                  <rect
                    key={`${glow.node}-${glow.tone}`}
                    x={r.x}
                    y={r.y}
                    width={r.w}
                    height={r.h}
                    rx={20}
                    opacity={0}
                    filter="url(#arch-blur)"
                    className={GLOW_FILL[glow.tone]}
                  >
                    <Animate attr="opacity" series={glow.series} />
                  </rect>
                );
              })}
              {JOURNEYS.map((packet) => (
                <g key={packet.id} opacity={0}>
                  <Animate attr="opacity" series={packet.visible} />
                  <animateMotion
                    path={packet.d}
                    keyPoints={fmtSeries(packet.motion.keyPoints)}
                    keyTimes={fmtSeries(packet.motion.keyTimes)}
                    calcMode="linear"
                    {...CLOCK}
                  />
                  <g>
                    {packet.shrink ? (
                      <Animate attr="transform" type="scale" series={packet.shrink} />
                    ) : null}
                    {(["neutral", "signal", "safe"] as const).map((color) => (
                      <g key={color} opacity={packet.colors[color].values[0]}>
                        <Animate attr="opacity" series={packet.colors[color]} />
                        <circle r={4} opacity={0.25} className={PACKET_FILL[color]} />
                        <circle r={1.5} className={PACKET_FILL[color]} />
                      </g>
                    ))}
                  </g>
                </g>
              ))}
            </g>
          </svg>
          <p
            className="landing-arch-tab"
            style={{ left: pctX(BOUNDARY.x + 12), top: pctY(BOUNDARY.y) }}
          >
            <Lock className="landing-arch-lock" strokeWidth={2} />
            credential boundary
          </p>
          <p
            className="landing-arch-caption"
            style={{ left: pctX(BOUNDARY.x + 16), top: pctY(BOUNDARY.y + 134) }}
          >
            only the backend holds the keys
          </p>
          {LABELS.map((label) => (
            <span
              key={label.text}
              style={{ left: pctX(label.x), top: pctY(label.y) }}
              className={cn("landing-arch-label", LABEL_TONE[label.tone])}
            >
              {label.text}
            </span>
          ))}
          {NODES.map((node) => (
            <NodeCard key={node.id} node={node} />
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
