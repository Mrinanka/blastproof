// F3 guards: SEC-29.
import { describe, expect, it } from "vitest";

import { buildCsp, newNonce } from "../src/lib/csp";

const scriptSrc = (csp: string) => csp.split("; ").find((d) => d.startsWith("script-src"))!;

describe("buildCsp", () => {
  const csp = buildCsp("abc123", false);

  it("puts the nonce and strict-dynamic in script-src, without unsafe-inline", () => {
    expect(scriptSrc(csp)).toContain("'nonce-abc123'");
    expect(scriptSrc(csp)).toContain("'strict-dynamic'");
    expect(scriptSrc(csp)).not.toContain("'unsafe-inline'");
  });

  it("allows unsafe-eval only in dev", () => {
    expect(scriptSrc(csp)).not.toContain("'unsafe-eval'");
    expect(scriptSrc(buildCsp("n", true))).toContain("'unsafe-eval'");
  });

  it("forbids framing and plugins", () => {
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("connect-src 'self'");
  });

  it("upgrades insecure requests outside dev only", () => {
    expect(csp).toContain("upgrade-insecure-requests");
    expect(buildCsp("n", true)).not.toContain("upgrade-insecure-requests");
  });
});

describe("newNonce", () => {
  it("is unique and CSP-safe", () => {
    const nonces = new Set(Array.from({ length: 50 }, newNonce));
    expect(nonces.size).toBe(50);
    for (const n of nonces) expect(n).toMatch(/^[A-Za-z0-9+/=]+$/);
  });
});
