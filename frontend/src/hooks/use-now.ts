"use client";

import { useSyncExternalStore } from "react";

const TICK_MS = 30_000;

// One clock shared by every component: a single interval, started by the first subscriber and
// cleared by the last.
let current: Date | null = null;
let timer: ReturnType<typeof setInterval> | undefined;
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    current = new Date();
    timer = setInterval(() => {
      current = new Date();
      listeners.forEach((l) => l());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      clearInterval(timer);
      timer = undefined;
      current = null;
    }
  };
}

function getSnapshot(): Date {
  return (current ??= new Date());
}

// null on the server and during hydration, so both render the same text; the client then gets a Date.
export function useNow(): Date | null {
  return useSyncExternalStore(subscribe, getSnapshot, () => null);
}
