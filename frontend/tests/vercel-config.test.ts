// Vercel deploy guards (OPS-14, D-278): one region, a function limit above the longest backend call.
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { LONG_BACKEND_TIMEOUT_MS } from "../src/lib/server/backend";

const ROOT = resolve(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

describe("vercel.json", () => {
  it("holds only the schema and one region", () => {
    const config = JSON.parse(read("vercel.json"));
    expect(Object.keys(config).sort()).toEqual(["$schema", "regions"]);
    expect(config.regions).toEqual(["iad1"]);
  });
});

describe("the backend proxy's function limit", () => {
  it("is above the longest backend timeout and within Hobby's 300 s", () => {
    const match = /export const maxDuration = (\d+);/.exec(
      read("src/app/api/blastproof/[...path]/route.ts"),
    );
    expect(match).not.toBeNull();
    const seconds = Number(match![1]);
    expect(seconds).toBeGreaterThan(LONG_BACKEND_TIMEOUT_MS / 1000);
    expect(seconds).toBeLessThanOrEqual(300);
  });
});

describe("the Node version", () => {
  it("matches .nvmrc and is one Vercel offers", () => {
    const engines = JSON.parse(read("package.json")).engines.node as string;
    const major = Number.parseInt(engines, 10);
    expect(major).toBe(Number.parseInt(read(".nvmrc"), 10));
    expect([22, 24, 26]).toContain(major);
  });
});
