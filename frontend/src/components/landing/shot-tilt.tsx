"use client";

import { m, useReducedMotion, useScroll, useTransform } from "motion/react";
import { useRef, useSyncExternalStore } from "react";

const noop = () => () => {};

// The screenshot leans back slightly and flattens as it scrolls into place. The server HTML and
// reduced motion render it flat: the tilt only starts after hydration (D-292).
export function ShotTilt({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const hydrated = useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "center center"] });
  const rotateX = useTransform(scrollYProgress, [0, 1], [14, 0]);
  const scale = useTransform(scrollYProgress, [0, 1], [0.94, 1]);
  const live = hydrated && !reduce;

  return (
    <div ref={ref} className="[perspective:1600px]">
      <m.div style={live ? { rotateX, scale, transformOrigin: "50% 0%" } : undefined}>
        {children}
      </m.div>
    </div>
  );
}
