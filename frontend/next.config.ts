import type { NextConfig } from "next";

import { buildLandingCsp } from "./src/lib/csp";

const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // These three do not depend on a nonce (SEC-28). The logged-in pages get their nonce CSP from
  // src/proxy.ts; the static landing page gets its own CSP here (SEC-29, D-028).
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
      {
        source: "/",
        headers: [
          {
            key: "Content-Security-Policy",
            value: buildLandingCsp(process.env.NODE_ENV === "development"),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
