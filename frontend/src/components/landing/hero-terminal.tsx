"use client";

import { m, useReducedMotion } from "motion/react";

import { formatNumber } from "@/lib/format";
import { REPLIT } from "@/lib/landing/content";

import { EASE_OUT } from "./in-view";

// The hero's signature animation, played once: the real Replit-style delete (incidents.yaml) is
// typed by the agent, previewed, explained with the exact seeded counts (D-078) and rejected.
// Every line is in the server HTML at opacity 0 until hydration; the page's <noscript> style shows
// it without JS (D-292). With reduced motion every step appears at once.
const TYPE_START = 0.5;
const CHAR = 0.022;
const TYPED = TYPE_START + REPLIT.sql.length * CHAR;
const AT = {
  label: 0.2,
  previewing: TYPED + 0.35,
  card: TYPED + 1.1,
  press: TYPED + 2.6,
  done: TYPED + 3.0,
};

const ANIM = { "data-landing-anim": "" };

export function HeroTerminal() {
  const reduce = useReducedMotion();
  const at = (delay: number, duration = 0.35) =>
    reduce ? { duration: 0 } : { delay, duration, ease: EASE_OUT };
  const fade = (delay: number, y = 0) => ({
    initial: { opacity: 0, y },
    animate: { opacity: 1, y: 0 },
    transition: at(delay),
    ...ANIM,
  });

  return (
    <div
      aria-hidden="true"
      className="landing-edge relative overflow-hidden rounded-xl bg-card font-mono text-[12.5px] leading-relaxed shadow-2xl shadow-black/40 sm:text-[13px]"
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5 text-[11px] text-muted-foreground">
        <span>agent session · claude code</span>
        <span className="flex items-center gap-1.5">
          <span className="size-1.5 rounded-full bg-signal" />
          blastproof on
        </span>
      </div>
      <div className="space-y-3 p-4 sm:p-5">
        <m.p {...fade(AT.label)} className="text-muted-foreground">
          <span className="text-signal">agent</span> › query_database · “
          {REPLIT.intent.toLowerCase()}”
        </m.p>
        <p className="break-words text-foreground">
          <span className="text-muted-foreground">$ </span>
          {Array.from(REPLIT.sql).map((ch, i) => (
            <m.span
              key={i}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={reduce ? { duration: 0 } : { delay: TYPE_START + i * CHAR, duration: 0 }}
              {...ANIM}
            >
              {ch}
            </m.span>
          ))}
        </p>
        <m.p {...fade(AT.previewing)} className="text-muted-foreground">
          <span className="text-signal">blastproof</span> › write intercepted · previewing on a
          copy…
        </m.p>
        <m.div
          {...fade(AT.card, 8)}
          className="rounded-lg border border-signal/40 bg-signal/5 p-4 font-sans"
        >
          <p className="font-mono text-[11px] tracking-wide text-signal">
            IMPACT · HELD FOR REVIEW
          </p>
          <p className="mt-2 text-[15px] leading-snug text-foreground sm:text-base">
            Deletes{" "}
            <strong className="font-semibold text-signal">
              {formatNumber(REPLIT.users)} users
            </strong>
            , including{" "}
            <strong className="font-semibold text-signal">{REPLIT.paying} paying customers</strong>{" "}
            ({REPLIT.enterprise} enterprise).
          </p>
          <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 font-mono text-[11.5px] text-muted-foreground min-[420px]:grid-cols-2">
            {REPLIT.cascades.map((c) => (
              <div key={c.table} className="flex justify-between gap-2">
                <dt>{c.table}</dt>
                <dd className="text-foreground tabular-nums">−{formatNumber(c.deleted)}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-4 flex gap-2 text-[13px] font-medium">
            <span className="rounded-md border border-border px-3 py-1.5 text-muted-foreground">
              Approve
            </span>
            <span className="relative overflow-hidden rounded-md border border-signal/50 px-3 py-1.5 text-signal">
              Reject
              <m.span
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={at(AT.press, 0.15)}
                {...ANIM}
                className="absolute inset-0 grid place-items-center bg-signal text-signal-foreground"
              >
                Reject
              </m.span>
            </span>
          </div>
        </m.div>
        <m.p {...fade(AT.done)} className="flex items-center gap-2 text-safe">
          <span className="size-1.5 shrink-0 rounded-full bg-safe" />
          Rejected by owner. Nothing changed.
          <span className="landing-caret inline-block h-4 w-2 bg-safe/70" />
        </m.p>
      </div>
    </div>
  );
}
