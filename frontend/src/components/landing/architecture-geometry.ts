// Geometry and timing for the landing architecture diagram (F13.2, D-307). Pure TypeScript with no
// DOM: every position is a constant in "design units" on a 1104 x 500 canvas, and every packet
// time comes from the real route lengths, so nothing is measured in the browser.

export type Pt = readonly [number, number];

export const VIEW = { w: 1104, h: 500 } as const;
// Packets travel at a constant speed (design units per second); dwells are explicit.
export const SPEED = 420;
// One shared SMIL clock for every packet and glow, in seconds.
export const CYCLE = 14;
// Corner radius of the orthogonal "metro" edges.
export const CORNER = 10;

export type Tone = "plain" | "signal" | "safe" | "core";
export type NodeId =
  "agent" | "gw" | "own" | "preview" | "rules" | "owner" | "commit" | "blocked" | "company";
export type IconName =
  | "Bot"
  | "ShieldCheck"
  | "ScrollText"
  | "GitBranch"
  | "Scale"
  | "UserCheck"
  | "GitCommitHorizontal"
  | "Ban"
  | "Database";

export interface NodeDef {
  id: NodeId;
  cx: number;
  cy: number;
  w: number;
  h: number;
  title: string;
  sub?: string;
  tone: Tone;
  icon: IconName;
}

export const NODES: readonly NodeDef[] = [
  {
    id: "agent",
    cx: 60,
    cy: 236,
    w: 104,
    h: 84,
    title: "AI agent",
    sub: "Claude Code",
    tone: "plain",
    icon: "Bot",
  },
  {
    id: "gw",
    cx: 254,
    cy: 236,
    w: 156,
    h: 100,
    title: "Blastproof backend",
    sub: "FastAPI",
    tone: "core",
    icon: "ShieldCheck",
  },
  {
    id: "own",
    cx: 254,
    cy: 84,
    w: 220,
    h: 88,
    title: "Blastproof database",
    sub: "actions · audit · knowledge",
    tone: "plain",
    icon: "ScrollText",
  },
  {
    id: "preview",
    cx: 448,
    cy: 236,
    w: 120,
    h: 84,
    title: "Preview on a branch",
    tone: "plain",
    icon: "GitBranch",
  },
  {
    id: "rules",
    cx: 608,
    cy: 236,
    w: 120,
    h: 84,
    title: "Rules, then LLM judge",
    tone: "plain",
    icon: "Scale",
  },
  {
    id: "owner",
    cx: 818,
    cy: 96,
    w: 140,
    h: 84,
    title: "Owner approves",
    sub: "in the dashboard",
    tone: "signal",
    icon: "UserCheck",
  },
  {
    id: "commit",
    cx: 818,
    cy: 236,
    w: 140,
    h: 84,
    title: "Commit what was previewed",
    tone: "safe",
    icon: "GitCommitHorizontal",
  },
  { id: "blocked", cx: 818, cy: 376, w: 140, h: 68, title: "Blocked", tone: "signal", icon: "Ban" },
  {
    id: "company",
    cx: 1000,
    cy: 236,
    w: 128,
    h: 84,
    title: "Company database",
    tone: "plain",
    icon: "Database",
  },
];

export const NODE_BY_ID = Object.fromEntries(NODES.map((n) => [n.id, n])) as Record<
  NodeId,
  NodeDef
>;

export interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export function box(node: NodeDef): Box {
  return {
    left: node.cx - node.w / 2,
    right: node.cx + node.w / 2,
    top: node.cy - node.h / 2,
    bottom: node.cy + node.h / 2,
  };
}

// The dashed credential boundary around the company database.
export const BOUNDARY = { x: 904, y: 158, w: 184, h: 178, rx: 14 } as const;

// ---------------------------------------------------------------------------------------------
// Routes

export interface Route {
  // SVG path data: straight lines joined by circular arcs at the corners.
  d: string;
  length: number;
  // Length along the route at each waypoint (the middle of the arc at a corner).
  at: number[];
}

const fmt = (n: number) => String(Math.round(n * 1000) / 1000);
const dist = (a: Pt, b: Pt) => Math.hypot(b[0] - a[0], b[1] - a[1]);

// An orthogonal polyline with rounded corners. A corner's radius shrinks to half of a short
// neighbouring segment so two corners never overlap.
export function route(points: readonly Pt[], radius: number): Route {
  const first = points[0];
  if (!first || points.length < 2) throw new Error("a route needs at least two points");
  const unit = (a: Pt, b: Pt): Pt => {
    const len = dist(a, b);
    return [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
  };
  let d = `M${fmt(first[0])} ${fmt(first[1])}`;
  let length = 0;
  let cursor: Pt = first;
  const at: number[] = [0];
  for (let i = 1; i < points.length; i++) {
    const p = points[i]!;
    const next = points[i + 1];
    if (!next) {
      d += ` L${fmt(p[0])} ${fmt(p[1])}`;
      length += dist(cursor, p);
      at.push(length);
      break;
    }
    const r = Math.min(radius, dist(cursor, p) / 2, dist(p, next) / 2);
    const u1 = unit(cursor, p);
    const u2 = unit(p, next);
    const entry: Pt = [p[0] - u1[0] * r, p[1] - u1[1] * r];
    const exit: Pt = [p[0] + u2[0] * r, p[1] + u2[1] * r];
    // In SVG's y-down space a positive cross product turns clockwise, which is sweep-flag 1.
    const sweep = u1[0] * u2[1] - u1[1] * u2[0] > 0 ? 1 : 0;
    d += ` L${fmt(entry[0])} ${fmt(entry[1])} A${fmt(r)} ${fmt(r)} 0 0 ${sweep} ${fmt(exit[0])} ${fmt(exit[1])}`;
    length += dist(cursor, entry);
    at.push(length + (Math.PI * r) / 4);
    length += (Math.PI * r) / 2;
    cursor = exit;
  }
  return { d, length, at };
}

// ---------------------------------------------------------------------------------------------
// Edges

export type EdgeId =
  | "mcp"
  | "hook"
  | "own"
  | "write"
  | "pr"
  | "review"
  | "allow"
  | "block"
  | "approve"
  | "commit"
  | "read";
// How an edge is painted: neutral, the write path fading to amber, review/block amber,
// allow/commit green, and approve running from amber into green.
export type EdgeTone = "neutral" | "write" | "signal" | "safe" | "approve";

export interface EdgeDef {
  id: EdgeId;
  from: NodeId;
  to: NodeId;
  points: readonly Pt[];
  tone: EdgeTone;
  dashed?: boolean;
  // Mermaid's `---` link has no arrow.
  arrow: boolean;
  // Accent edges get a soft wide copy under the line.
  glow?: boolean;
}

export const EDGES: readonly EdgeDef[] = [
  {
    id: "mcp",
    from: "agent",
    to: "gw",
    points: [
      [112, 222],
      [176, 222],
    ],
    tone: "neutral",
    arrow: true,
  },
  {
    id: "hook",
    from: "agent",
    to: "gw",
    points: [
      [112, 250],
      [176, 250],
    ],
    tone: "neutral",
    dashed: true,
    arrow: true,
  },
  {
    id: "own",
    from: "gw",
    to: "own",
    points: [
      [254, 186],
      [254, 128],
    ],
    tone: "neutral",
    dashed: true,
    arrow: false,
  },
  {
    id: "write",
    from: "gw",
    to: "preview",
    points: [
      [332, 236],
      [388, 236],
    ],
    tone: "write",
    arrow: true,
    glow: true,
  },
  {
    id: "pr",
    from: "preview",
    to: "rules",
    points: [
      [508, 236],
      [548, 236],
    ],
    tone: "write",
    arrow: true,
    glow: true,
  },
  {
    id: "review",
    from: "rules",
    to: "owner",
    points: [
      [668, 236],
      [692, 236],
      [692, 96],
      [748, 96],
    ],
    tone: "signal",
    arrow: true,
    glow: true,
  },
  {
    id: "allow",
    from: "rules",
    to: "commit",
    points: [
      [668, 236],
      [748, 236],
    ],
    tone: "safe",
    arrow: true,
    glow: true,
  },
  {
    id: "block",
    from: "rules",
    to: "blocked",
    points: [
      [668, 236],
      [692, 236],
      [692, 376],
      [748, 376],
    ],
    tone: "signal",
    arrow: true,
    glow: true,
  },
  {
    id: "approve",
    from: "owner",
    to: "commit",
    points: [
      [818, 138],
      [818, 194],
    ],
    tone: "approve",
    arrow: true,
    glow: true,
  },
  {
    id: "commit",
    from: "commit",
    to: "company",
    points: [
      [888, 236],
      [936, 236],
    ],
    tone: "safe",
    arrow: true,
    glow: true,
  },
  {
    id: "read",
    from: "gw",
    to: "company",
    points: [
      [254, 286],
      [254, 468],
      [1040, 468],
      [1040, 278],
    ],
    tone: "neutral",
    arrow: true,
  },
];

export const ROUTES = Object.fromEntries(
  EDGES.map((e) => [e.id, route(e.points, CORNER)]),
) as Record<EdgeId, Route>;

// Edge labels, centred on the given point. Horizontal edges carry theirs just above the line.
export const LABELS: readonly {
  text: string;
  x: number;
  y: number;
  tone: "muted" | "signal" | "safe";
}[] = [
  { text: "MCP", x: 144, y: 210, tone: "muted" },
  { text: "hook", x: 144, y: 262, tone: "muted" },
  { text: "write", x: 360, y: 223, tone: "muted" },
  { text: "review", x: 692, y: 166, tone: "signal" },
  { text: "allow", x: 720, y: 223, tone: "safe" },
  { text: "block", x: 692, y: 306, tone: "signal" },
  { text: "approve", x: 818, y: 166, tone: "muted" },
  { text: "read", x: 640, y: 468, tone: "muted" },
];

// A small chevron whose tip sits on the last point of the edge, pointing along its last segment.
export function chevron(points: readonly Pt[], size = 4.5): string {
  const tip = points[points.length - 1]!;
  const prev = points[points.length - 2]!;
  const len = dist(prev, tip);
  const ux = (tip[0] - prev[0]) / len;
  const uy = (tip[1] - prev[1]) / len;
  const a: Pt = [tip[0] - ux * size - uy * size, tip[1] - uy * size + ux * size];
  const b: Pt = [tip[0] - ux * size + uy * size, tip[1] - uy * size - ux * size];
  return `M${fmt(a[0])} ${fmt(a[1])} L${fmt(tip[0])} ${fmt(tip[1])} L${fmt(b[0])} ${fmt(b[1])}`;
}

// ---------------------------------------------------------------------------------------------
// Journeys: four writes and reads, each with one outcome, on a shared clock.

export type PacketColor = "neutral" | "signal" | "safe";
export type GlowTone = PacketColor;

interface GlowAt {
  node: NodeId;
  tone: GlowTone;
  sec?: number;
}
interface Hold {
  // Units into the step's route where the packet stops (negative: before it, on the connector).
  at: number;
  sec: number;
  glow?: GlowAt;
  // The colour the packet turns to as it leaves.
  color?: PacketColor;
}
interface Step {
  edge: EdgeId;
  hold?: Hold;
  // A node that lights up as the packet reaches the end of this route.
  arrive?: GlowAt;
}
interface JourneyDef {
  id: string;
  start: number;
  from: Pt;
  steps: readonly Step[];
  to: Pt;
  color: PacketColor;
  // A blocked write shrinks to nothing; the others fade out at the end.
  end: "fade" | "shrink";
  endGlow?: GlowAt;
}

const WRITE_TO_RULES: Step[] = [
  { edge: "write", arrive: { node: "preview", tone: "neutral" } },
  { edge: "pr" },
];

export const JOURNEY_DEFS: readonly JourneyDef[] = [
  {
    id: "read",
    start: 0,
    from: [60, 236],
    steps: [{ edge: "mcp" }, { edge: "read", arrive: { node: "company", tone: "neutral" } }],
    to: [1000, 236],
    color: "neutral",
    end: "fade",
  },
  {
    id: "write-allow",
    start: 2.2,
    from: [60, 236],
    steps: [
      { edge: "mcp" },
      ...WRITE_TO_RULES,
      {
        edge: "allow",
        hold: { at: 14, sec: 0.5, glow: { node: "rules", tone: "neutral" }, color: "safe" },
        arrive: { node: "commit", tone: "safe" },
      },
      { edge: "commit", arrive: { node: "company", tone: "safe" } },
    ],
    to: [1000, 236],
    color: "neutral",
    end: "fade",
  },
  {
    id: "write-review",
    start: 5.6,
    from: [60, 236],
    steps: [
      { edge: "hook" },
      ...WRITE_TO_RULES,
      {
        edge: "review",
        hold: { at: 14, sec: 0.5, glow: { node: "rules", tone: "neutral" }, color: "signal" },
      },
      {
        edge: "approve",
        hold: { at: -20, sec: 1, glow: { node: "owner", tone: "signal", sec: 1.1 }, color: "safe" },
        arrive: { node: "commit", tone: "safe" },
      },
      { edge: "commit", arrive: { node: "company", tone: "safe" } },
    ],
    to: [1000, 236],
    color: "neutral",
    end: "fade",
  },
  {
    id: "write-block",
    start: 10.4,
    from: [60, 236],
    steps: [
      { edge: "mcp" },
      ...WRITE_TO_RULES,
      {
        edge: "block",
        hold: { at: 14, sec: 0.5, glow: { node: "rules", tone: "neutral" }, color: "signal" },
      },
    ],
    to: [818, 376],
    color: "neutral",
    end: "shrink",
    endGlow: { node: "blocked", tone: "signal" },
  },
];

const FADE_IN = 0.2;
const FADE_OUT = 0.3;
const END_HOLD = 0.25;
const SHRINK = 0.6;
const CROSSFADE = 0.3;
const GLOW_SEC = 0.8;
const GLOW_PEAK = 0.8;

export interface Series {
  keyTimes: number[];
  values: number[];
}

// Builds a series for the shared clock from transitions: the value holds until `t`, then moves
// to `to` over `dur`. It starts at `initial` and returns to it at the end of the cycle.
function series(initial: number, transitions: { t: number; dur: number; to: number }[]): Series {
  const pts: [number, number][] = [[0, initial]];
  let prev = initial;
  for (const tr of [...transitions].sort((a, b) => a.t - b.t)) {
    pts.push([tr.t, prev], [tr.t + tr.dur, tr.to]);
    prev = tr.to;
  }
  pts.push([CYCLE, initial]);
  const keyTimes: number[] = [];
  const values: number[] = [];
  for (const [t, v] of pts) {
    const k = r4(t / CYCLE);
    if (
      keyTimes.length > 0 &&
      k === keyTimes[keyTimes.length - 1] &&
      v === values[values.length - 1]
    )
      continue;
    keyTimes.push(k);
    values.push(v);
  }
  return { keyTimes, values };
}

function r4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

export interface PacketPlan {
  id: string;
  d: string;
  length: number;
  start: number;
  // When the packet is gone again, in seconds on the shared clock.
  end: number;
  motion: { keyTimes: number[]; keyPoints: number[] };
  visible: Series;
  colors: Record<PacketColor, Series>;
  shrink?: Series;
}

export interface GlowEvent {
  node: NodeId;
  tone: GlowTone;
  t: number;
  sec: number;
}

function buildJourney(def: JourneyDef): { packet: PacketPlan; glows: GlowEvent[] } {
  let d = `M${fmt(def.from[0])} ${fmt(def.from[1])}`;
  let cursor: Pt = def.from;
  let total = 0;
  const holds: { dist: number; hold: Hold }[] = [];
  const arrivals: { dist: number; glow: GlowAt }[] = [];
  for (const step of def.steps) {
    const r = ROUTES[step.edge];
    const startPt = EDGES.find((e) => e.id === step.edge)!.points[0]!;
    total += dist(cursor, startPt);
    if (step.hold) holds.push({ dist: total + step.hold.at, hold: step.hold });
    d += ` L${r.d.slice(1)}`;
    total += r.length;
    if (step.arrive) arrivals.push({ dist: total, glow: step.arrive });
    const pts = EDGES.find((e) => e.id === step.edge)!.points;
    cursor = pts[pts.length - 1]!;
  }
  total += dist(cursor, def.to);
  d += ` L${fmt(def.to[0])} ${fmt(def.to[1])}`;

  // Distance to time: constant speed plus the sum of the holds passed so far.
  holds.sort((a, b) => a.dist - b.dist);
  const timeAt = (x: number) =>
    x / SPEED + holds.filter((h) => h.dist < x).reduce((sum, h) => sum + h.hold.sec, 0);
  const arrival = timeAt(total);

  const keyTimes: number[] = [0];
  const keyPoints: number[] = [0];
  const push = (t: number, x: number) => {
    keyTimes.push(r4((def.start + t) / CYCLE));
    keyPoints.push(Math.round((x / total) * 100000) / 100000);
  };
  push(0, 0);
  for (const h of holds) {
    push(timeAt(h.dist), h.dist);
    push(timeAt(h.dist) + h.hold.sec, h.dist);
  }
  push(arrival, total);
  // Before the start the packet waits at the beginning; after the end it waits at the end.
  if (keyTimes[1] === 0) {
    keyTimes.shift();
    keyPoints.shift();
  }
  keyTimes.push(1);
  keyPoints.push(1);

  const glows: GlowEvent[] = [];
  const colorChanges: { t: number; to: PacketColor }[] = [];
  for (const a of arrivals) {
    glows.push({
      node: a.glow.node,
      tone: a.glow.tone,
      t: def.start + timeAt(a.dist),
      sec: a.glow.sec ?? GLOW_SEC,
    });
  }
  for (const h of holds) {
    const t0 = def.start + timeAt(h.dist);
    if (h.hold.glow) {
      glows.push({
        node: h.hold.glow.node,
        tone: h.hold.glow.tone,
        t: t0,
        sec: h.hold.glow.sec ?? GLOW_SEC,
      });
    }
    if (h.hold.color) colorChanges.push({ t: t0 + h.hold.sec - CROSSFADE, to: h.hold.color });
  }
  if (def.endGlow) {
    glows.push({
      node: def.endGlow.node,
      tone: def.endGlow.tone,
      t: def.start + arrival,
      sec: def.endGlow.sec ?? GLOW_SEC,
    });
  }

  const gone = def.start + arrival + (def.end === "shrink" ? SHRINK : END_HOLD + FADE_OUT);
  const fadeOutAt = def.end === "shrink" ? def.start + arrival : def.start + arrival + END_HOLD;
  const visible = series(0, [
    { t: def.start, dur: FADE_IN, to: 1 },
    { t: fadeOutAt, dur: gone - fadeOutAt, to: 0 },
  ]);

  const colors = {} as Record<PacketColor, Series>;
  for (const c of ["neutral", "signal", "safe"] as const) {
    const trs: { t: number; dur: number; to: number }[] = [];
    let current: PacketColor = def.color;
    for (const ch of colorChanges.sort((a, b) => a.t - b.t)) {
      if (current === c) trs.push({ t: ch.t, dur: CROSSFADE, to: 0 });
      if (ch.to === c) trs.push({ t: ch.t, dur: CROSSFADE, to: 1 });
      current = ch.to;
    }
    colors[c] = series(def.color === c ? 1 : 0, trs);
  }

  const packet: PacketPlan = {
    id: def.id,
    d,
    length: total,
    start: def.start,
    end: gone,
    motion: { keyTimes, keyPoints },
    visible,
    colors,
  };
  if (def.end === "shrink") {
    packet.shrink = series(1, [{ t: def.start + arrival, dur: SHRINK, to: 0 }]);
  }
  return { packet, glows };
}

const BUILT = JOURNEY_DEFS.map(buildJourney);
export const JOURNEYS: readonly PacketPlan[] = BUILT.map((b) => b.packet);
export const GLOW_EVENTS: readonly GlowEvent[] = BUILT.flatMap((b) => b.glows);

export interface GlowPlan {
  node: NodeId;
  tone: GlowTone;
  series: Series;
}

// One blurred highlight per node and tone, lit by the events that carry that pair.
export const GLOWS: readonly GlowPlan[] = (() => {
  const keys = new Map<string, GlowEvent[]>();
  for (const ev of GLOW_EVENTS) {
    const k = `${ev.node}:${ev.tone}`;
    keys.set(k, [...(keys.get(k) ?? []), ev]);
  }
  return [...keys.values()].map((events) => ({
    node: events[0]!.node,
    tone: events[0]!.tone,
    series: series(
      0,
      events.flatMap((ev) => [
        { t: ev.t, dur: 0.25, to: GLOW_PEAK },
        { t: ev.t + ev.sec - 0.35, dur: 0.35, to: 0 },
      ]),
    ),
  }));
})();

export const glowRect = (node: NodeDef, grow = 6) => {
  const b = box(node);
  return { x: b.left - grow, y: b.top - grow, w: node.w + grow * 2, h: node.h + grow * 2 };
};

export const fmtSeries = (values: readonly number[]) => values.join(";");
