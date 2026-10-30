"use client";

import { LazyMotion, MotionConfig, domAnimation } from "motion/react";

// Motion for the landing page only (D-290): the small `domAnimation` feature set, `m` components
// (strict mode throws on a full `motion.*` import), and the visitor's reduced-motion setting.
export function LandingMotion({ children }: { children: React.ReactNode }) {
  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}
