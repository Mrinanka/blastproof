"use client";

import { animate, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";

import { formatNumber } from "@/lib/format";

import { EASE_OUT, isInViewport } from "../in-view";

// Adapted from Magic UI's Number Ticker. The server HTML holds the exact final value (SEO, no-JS,
// reduced motion); the count-up runs only if the number is below the fold when the page hydrates.
const format = (n: number, decimals: number) =>
  decimals > 0 ? n.toFixed(decimals) : formatNumber(Math.round(n));

export function NumberTicker({ value, decimals = 0 }: { value: number; decimals?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const reduce = useReducedMotion();

  useEffect(() => {
    const el = ref.current;
    if (!el || reduce || isInViewport(el)) return;
    const show = (n: number) => {
      el.textContent = format(n, decimals);
    };
    show(0);
    let stop: (() => void) | undefined;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        observer.disconnect();
        const controls = animate(0, value, { duration: 0.6, ease: EASE_OUT, onUpdate: show });
        stop = () => controls.stop();
      },
      { threshold: 0.6 },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      stop?.();
      show(value);
    };
  }, [value, decimals, reduce]);

  return (
    <span ref={ref} className="tabular-nums">
      {format(value, decimals)}
    </span>
  );
}
