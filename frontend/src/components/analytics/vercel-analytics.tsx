"use client";

import { Analytics } from "@vercel/analytics/next";
import { SpeedInsights } from "@vercel/speed-insights/next";

import { redactEvent } from "@/lib/analytics";

// A client component because beforeSend is a function, which cannot pass from the server layout.
// The scripts load from the deployment's own origin, so the CSPs need no change (D-304).
export function VercelAnalytics() {
  return (
    <>
      <Analytics beforeSend={redactEvent} />
      <SpeedInsights beforeSend={redactEvent} />
    </>
  );
}
