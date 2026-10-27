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
