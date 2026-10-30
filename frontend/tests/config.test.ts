// F1 scaffold guards: DW-F01, CONV-11, CONV-16, SEC-28 (headers), SEC-31 (env file), CLAUDE.md rules.
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import nextConfig from "../next.config";
import { buildLandingCsp } from "../src/lib/csp";
import {
  envExampleProblems,
  forbiddenSource,
  headerProblems,
  inexactPins,
  type HeaderRule,
} from "./config-checks";

const ROOT = resolve(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const readJson = (path: string) => JSON.parse(read(path));

function sourceFiles(dir: string): Record<string, string> {
  const files: Record<string, string> = {};
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) Object.assign(files, sourceFiles(path));
    else if (/\.(ts|tsx|css)$/.test(name)) {
      files[path.slice(ROOT.length + 1)] = readFileSync(path, "utf8");
    }
  }
  return files;
}

const PLANNED_COMPONENTS = [
  "button",
  "card",
  "table",
  "badge",
  "dialog",
  "input",
  "textarea",
  "field",
  "sonner",
  "dropdown-menu",
  "sidebar",
  "skeleton",
];

describe("tsconfig (CONV-11)", () => {
  const tsconfig = readJson("tsconfig.json");
  it("is strict", () => expect(tsconfig.compilerOptions.strict).toBe(true));
  it("maps @/* to src", () => expect(tsconfig.compilerOptions.paths["@/*"]).toEqual(["./src/*"]));
});

describe("package.json (CONV-16)", () => {
  const pkg = readJson("package.json");
  it("pins every dependency exactly", () => expect(inexactPins(pkg)).toEqual([]));
  it("targets Node 24 and .nvmrc agrees", () => {
    expect(pkg.engines.node).toBe("24.x");
    expect(read(".nvmrc").trim().split(".")[0]).toBe("24");
  });
  it.each(["lint", "typecheck", "test", "build", "format:check", "gen:api"])(
    "has the %s script",
    (name) => {
      expect(pkg.scripts[name]).toBeTruthy();
    },
  );
  it("has a lockfile", () => expect(existsSync(join(ROOT, "package-lock.json"))).toBe(true));
});

describe("security headers (SEC-28)", () => {
  it("covers every route with the three headers", async () => {
    const rules = (await nextConfig.headers?.()) as HeaderRule[];
    expect(headerProblems(rules)).toEqual([]);
  });

  // F13 (SEC-29): the static landing page gets its own CSP here; the proxy covers the rest.
  it("gives / exactly the landing CSP, and no other route a CSP", async () => {
    const rules = (await nextConfig.headers?.()) as HeaderRule[];
    const withCsp = rules.filter((r) =>
      r.headers.some((h) => h.key.toLowerCase() === "content-security-policy"),
    );
    expect(withCsp.map((r) => r.source)).toEqual(["/"]);
    expect(withCsp[0]!.headers).toEqual([
      { key: "Content-Security-Policy", value: buildLandingCsp(false) },
    ]);
  });
});

describe("env example (SEC-31)", () => {
  it("holds the three names and nothing public", () => {
    expect(envExampleProblems(read(".env.example"))).toEqual([]);
  });
  const ignored = (file: string) => {
    try {
      execFileSync("git", ["check-ignore", "-q", file], { cwd: ROOT });
      return true;
    } catch {
      return false;
    }
  };
  it("ignores .env.local but not .env.example", () => {
    expect(ignored(".env.local")).toBe(true);
    expect(ignored(".env.example")).toBe(false);
  });
});

describe("source rules (CLAUDE.md)", () => {
  it("has no dangerouslySetInnerHTML, NEXT_PUBLIC_ or localStorage under src/", () => {
    expect(forbiddenSource(sourceFiles(join(ROOT, "src")))).toEqual([]);
  });
});

describe("shadcn (FE-01)", () => {
  const components = readJson("components.json");
  it("uses the Radix base and src aliases", () => {
    expect(components.style).toMatch(/^radix-/);
    expect(components.aliases.ui).toBe("@/components/ui");
    expect(components.aliases.utils).toBe("@/lib/utils");
  });
  it.each(PLANNED_COMPONENTS)("has the %s component", (name) => {
    expect(existsSync(join(ROOT, "src/components/ui", `${name}.tsx`))).toBe(true);
  });
});

describe("the checks catch bad input", () => {
  it("a caret pin", () => {
    expect(inexactPins({ dependencies: { next: "^16.4.3" } })).not.toEqual([]);
  });
  it("a missing header", () => {
    const rules = [{ source: "/(.*)", headers: [{ key: "X-Frame-Options", value: "DENY" }] }];
    expect(headerProblems(rules)).not.toEqual([]);
  });
  it("a wrong header value and a missing rule", () => {
    const wrong = [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
    expect(headerProblems(wrong)).not.toEqual([]);
    expect(headerProblems([])).not.toEqual([]);
  });
  it("a public variable, a fourth name and a wrong max age", () => {
    const good =
      "BLASTPROOF_API_URL=http://localhost:8000\nSESSION_COOKIE_NAME=x\nSESSION_MAX_AGE_SECONDS=28800\n";
    expect(envExampleProblems(good)).toEqual([]);
    expect(envExampleProblems(`${good}NEXT_PUBLIC_X=1\n`)).not.toEqual([]);
    expect(envExampleProblems(`${good}EXTRA=1\n`)).not.toEqual([]);
    expect(envExampleProblems(good.replace("28800", "60"))).not.toEqual([]);
  });
  it("forbidden source patterns", () => {
    for (const bad of [
      "dangerouslySetInnerHTML",
      "process.env.NEXT_PUBLIC_A",
      "localStorage.get",
    ]) {
      expect(forbiddenSource({ "src/x.tsx": bad })).not.toEqual([]);
    }
  });
});
