// Nonce-based CSP for the logged-in pages (SEC-29). Styles keep 'unsafe-inline': sonner and the
// Query devtools inject <style> tags and Radix sets style attributes, and a style nonce would make
// the browser ignore 'unsafe-inline' (D-224). Scripts, the real XSS risk, are nonce-only.
export function buildCsp(nonce: string, isDev: boolean): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  if (!isDev) directives.push("upgrade-insecure-requests");
  return directives.join("; ");
}

export function newNonce(): string {
  return btoa(crypto.randomUUID());
}

// The public landing page is static, so it cannot carry a per-request nonce (D-028). Its inline
// flight-data scripts have no stable hash (D-219), so scripts need 'unsafe-inline' here. That is
// acceptable because the page renders no user or agent content and loads no third-party script
// (Vercel Analytics and Speed Insights load from the deployment's own origin, D-304).
// Served from next.config.ts for `/` only; the proxy never runs there.
export function buildLandingCsp(isDev: boolean): string {
  const directives = [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ];
  if (!isDev) directives.push("upgrade-insecure-requests");
  return directives.join("; ");
}
