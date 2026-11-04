// F13.2 guards (D-307): the architecture diagram's geometry and packet timing, with no DOM.
import { describe, expect, it } from "vitest";

import {
  CYCLE,
  EDGES,
  GLOWS,
  JOURNEYS,
  NODE_BY_ID,
  NODES,
  ROUTES,
  VIEW,
  box,
  chevron,
  route,
  type EdgeDef,
  type Pt,
} from "../src/components/landing/architecture-geometry";

describe("route()", () => {
  it("measures a straight line", () => {
    const r = route(
      [
        [0, 0],
        [100, 0],
      ],
      10,
    );
    expect(r.d).toBe("M0 0 L100 0");
    expect(r.length).toBe(100);
    expect(r.at).toEqual([0, 100]);
  });

  it("measures one rounded corner: two 90 unit lines and a quarter circle", () => {
    const r = route(
      [
        [0, 0],
        [100, 0],
        [100, 100],
      ],
      10,
    );
    expect(r.length).toBeCloseTo(180 + (Math.PI * 10) / 2, 6);
    // Right then down turns clockwise on screen: sweep flag 1.
    expect(r.d).toBe("M0 0 L90 0 A10 10 0 0 1 100 10 L100 100");
    expect(r.at[1]).toBeCloseTo(90 + (Math.PI * 10) / 4, 6);
  });

  it("turns the other way with sweep flag 0, and shrinks a corner on a short segment", () => {
    const r = route(
      [
        [0, 0],
        [100, 0],
        [100, -12],
      ],
      10,
    );
    expect(r.d).toContain("A6 6 0 0 0");
    expect(r.length).toBeCloseTo(94 + 6 + (Math.PI * 6) / 2, 6);
  });
});

const onBorder = (p: Pt, id: keyof typeof NODE_BY_ID) => {
  const b = box(NODE_BY_ID[id]);
  const inside = p[0] >= b.left && p[0] <= b.right && p[1] >= b.top && p[1] <= b.bottom;
  const onEdge = p[0] === b.left || p[0] === b.right || p[1] === b.top || p[1] === b.bottom;
  return inside && onEdge;
};

describe("nodes and edges", () => {
  it("keeps every card inside the canvas", () => {
    for (const n of NODES) {
      const b = box(n);
      expect(b.left, n.id).toBeGreaterThanOrEqual(0);
      expect(b.top, n.id).toBeGreaterThanOrEqual(0);
      expect(b.right, n.id).toBeLessThanOrEqual(VIEW.w);
      expect(b.bottom, n.id).toBeLessThanOrEqual(VIEW.h);
    }
  });

  it("never overlaps two cards", () => {
    for (const a of NODES)
      for (const c of NODES) {
        if (a.id >= c.id) continue;
        const [p, q] = [box(a), box(c)];
        const apart =
          p.right <= q.left || q.right <= p.left || p.bottom <= q.top || q.bottom <= p.top;
        expect(apart, `${a.id} and ${c.id}`).toBe(true);
      }
  });

  it.each(EDGES)("edge $id starts and ends on the borders of its cards", (edge) => {
    expect(onBorder(edge.points[0]!, edge.from), "start").toBe(true);
    expect(onBorder(edge.points[edge.points.length - 1]!, edge.to), "end").toBe(true);
  });

  it("uses only horizontal and vertical segments", () => {
    for (const e of EDGES)
      for (let i = 1; i < e.points.length; i++) {
        const [a, b] = [e.points[i - 1]!, e.points[i]!];
        expect(a[0] === b[0] || a[1] === b[1], e.id).toBe(true);
      }
  });

  it("has a route and a chevron for every arrow", () => {
    for (const e of EDGES) {
      expect(ROUTES[e.id].length).toBeGreaterThan(0);
      if (e.arrow) expect(chevron(e.points)).toMatch(/^M[\d. -]+ L[\d. -]+ L[\d. -]+$/);
    }
  });
});

type Seg = [Pt, Pt];
const segments = (e: EdgeDef): Seg[] => e.points.slice(1).map((p, i) => [e.points[i]!, p] as Seg);

// Axis-aligned segments touch or cross when their ranges overlap on both axes.
const touch = ([a, b]: Seg, [c, d]: Seg) =>
  Math.max(Math.min(a[0], b[0]), Math.min(c[0], d[0])) <=
    Math.min(Math.max(a[0], b[0]), Math.max(c[0], d[0])) &&
  Math.max(Math.min(a[1], b[1]), Math.min(c[1], d[1])) <=
    Math.min(Math.max(a[1], b[1]), Math.max(c[1], d[1]));

describe("edge crossings", () => {
  it("no two edges cross or touch, except branches leaving the same card", () => {
    for (const a of EDGES)
      for (const b of EDGES) {
        if (a.id >= b.id) continue;
        const hit = segments(a).some((s) => segments(b).some((t) => touch(s, t)));
        if (
          a.from === b.from &&
          a.points[0]![0] === b.points[0]![0] &&
          a.points[0]![1] === b.points[0]![1]
        ) {
          continue; // the review, allow and block branches share one stem
        }
        expect(hit, `${a.id} and ${b.id}`).toBe(false);
      }
  });

  it("the fork branches only share their first stretch", () => {
    for (const id of ["review", "allow", "block"] as const) {
      const e = EDGES.find((x) => x.id === id)!;
      expect(e.points[0]).toEqual([668, 236]);
    }
  });
});

describe("journeys", () => {
  it.each(JOURNEYS)("$id: key times run 0 to 1 and only go up", (j) => {
    const { keyTimes, keyPoints } = j.motion;
    expect(keyTimes).toHaveLength(keyPoints.length);
    expect(keyTimes[0]).toBe(0);
    expect(keyTimes[keyTimes.length - 1]).toBe(1);
    for (let i = 1; i < keyTimes.length; i++)
      expect(keyTimes[i]!).toBeGreaterThanOrEqual(keyTimes[i - 1]!);
  });

  it.each(JOURNEYS)("$id: key points stay in [0, 1] and never go back", (j) => {
    const { keyPoints } = j.motion;
    expect(keyPoints[0]).toBe(0);
    expect(keyPoints[keyPoints.length - 1]).toBe(1);
    for (let i = 1; i < keyPoints.length; i++) {
      expect(keyPoints[i]!).toBeGreaterThanOrEqual(keyPoints[i - 1]!);
      expect(keyPoints[i]!).toBeLessThanOrEqual(1);
    }
  });

  it.each(JOURNEYS)("$id: ends before the cycle restarts", (j) => {
    expect(j.start).toBeGreaterThanOrEqual(0);
    expect(j.end).toBeLessThan(CYCLE);
  });

  it("moves at a constant speed between dwells", () => {
    for (const j of JOURNEYS) {
      const { keyTimes, keyPoints } = j.motion;
      const speeds: number[] = [];
      for (let i = 1; i < keyTimes.length; i++) {
        const dt = (keyTimes[i]! - keyTimes[i - 1]!) * CYCLE;
        const dd = (keyPoints[i]! - keyPoints[i - 1]!) * j.length;
        if (dd > 1) speeds.push(dd / dt);
      }
      expect(speeds.length).toBeGreaterThan(0);
      for (const s of speeds) expect(s, j.id).toBeCloseTo(420, -1);
    }
  });

  it("never has more than two packets visible at once", () => {
    for (let t = 0; t < CYCLE; t += 0.05) {
      const live = JOURNEYS.filter((j) => t >= j.start && t <= j.end).length;
      expect(live, `t=${t.toFixed(2)}`).toBeLessThanOrEqual(2);
    }
  });

  it("every colour series and glow series is a valid SMIL value list", () => {
    const all = [
      ...GLOWS.map((g) => g.series),
      ...JOURNEYS.flatMap((j) => [
        j.visible,
        ...Object.values(j.colors),
        ...(j.shrink ? [j.shrink] : []),
      ]),
    ];
    for (const s of all) {
      expect(s.values).toHaveLength(s.keyTimes.length);
      expect(s.keyTimes[0]).toBe(0);
      expect(s.keyTimes[s.keyTimes.length - 1]).toBe(1);
      for (let i = 1; i < s.keyTimes.length; i++)
        expect(s.keyTimes[i]!).toBeGreaterThanOrEqual(s.keyTimes[i - 1]!);
      for (const v of s.values) expect(v >= 0 && v <= 1).toBe(true);
    }
  });

  it("each packet shows exactly one colour at a time, apart from the crossfades", () => {
    for (const j of JOURNEYS) {
      const sum = (i: number) =>
        j.colors.neutral.values[i]! + j.colors.signal.values[i]! + j.colors.safe.values[i]!;
      // Series share breakpoints only at transitions, so compare the starting value instead.
      expect(sum(0), j.id).toBe(1);
    }
  });
});
