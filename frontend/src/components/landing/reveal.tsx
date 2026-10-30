"use client";

import { animate, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";

import { EASE_OUT, isInViewport } from "./in-view";

// Fades a block up when it scrolls into view. The server HTML is the final state: only a block
// that is below the fold when the page hydrates is hidden first, so nothing visible flashes, and
// no-JS and reduced-motion visitors see the page as rendered.
export function Reveal({
  children,
  className,
  delay = 0,
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();

  useEffect(() => {
    const el = ref.current;
    if (!el || reduce || isInViewport(el)) return;
    el.style.opacity = "0";
    el.style.transform = "translateY(12px)";
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        observer.disconnect();
        animate(
          el,
          { opacity: 1, transform: "translateY(0px)" },
          { duration: 0.5, delay, ease: EASE_OUT },
        );
      },
      { rootMargin: "0px 0px -10% 0px" },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      el.style.opacity = "";
      el.style.transform = "";
    };
  }, [delay, reduce]);

  return (
    <div ref={ref} className={className}>
      {children}
    </div>
  );
}
