// An in-memory stand-in for `next/navigation` on one page: push, replace, back and forward move
// through a history of query strings, and useSearchParams re-renders on each move, as in the App
// Router. Use it with `vi.mock("next/navigation", async () => (await import("./router-stub")).navigation)`.
import { useMemo, useSyncExternalStore } from "react";
import { vi } from "vitest";

export const PATH = "/dashboard/history";

let entries: string[] = [""];
let index = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());

function searchOf(href: string): string {
  const url = new URL(href, "http://localhost");
  if (url.pathname !== PATH) throw new Error(`navigated away from ${PATH}: ${href}`);
  return url.search.slice(1);
}

export const router = {
  // The mock records the options argument (`{ scroll: false }`) for the tests to check.
  push: vi.fn((href: string) => {
    entries = entries.slice(0, index + 1);
    entries.push(searchOf(href));
    index += 1;
    emit();
  }),
  replace: vi.fn((href: string) => {
    entries[index] = searchOf(href);
    emit();
  }),
  back() {
    if (index > 0) index -= 1;
    emit();
  },
  forward() {
    if (index < entries.length - 1) index += 1;
    emit();
  },
  get search() {
    return entries[index];
  },
  reset(initial = "") {
    entries = [initial];
    index = 0;
    router.push.mockClear();
    router.replace.mockClear();
  },
};

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const current = () => entries[index];

export const navigation = {
  usePathname: () => PATH,
  useRouter: () => router,
  useSearchParams: () => {
    const search = useSyncExternalStore(subscribe, current, current);
    return useMemo(() => new URLSearchParams(search), [search]);
  },
};
