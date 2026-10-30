import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ImageResponse } from "next/og";

import { formatNumber } from "@/lib/format";
import { REPLIT } from "@/lib/landing/content";
import { HEADLINE } from "@/lib/landing/site";

// The link preview (FE-29), built once at build time. next/og's bundled font is Geist. The colors
// are the landing tokens from globals.css converted to hex: ImageResponse cannot read CSS
// variables or oklch().
export const alt = `Blastproof. ${HEADLINE} An agent's DELETE of ${formatNumber(REPLIT.users)} users is previewed, explained and rejected.`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const BG = "#070709";
const FG = "#f5f5f5";
const MUTED = "#a3a4a8";
const SIGNAL = "#faab3f";
const SAFE = "#61da92";

export default async function OpengraphImage() {
  const logo = await readFile(join(process.cwd(), "src/app/icon.png"));
  const logoSrc = `data:image/png;base64,${logo.toString("base64")}`;
  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: 72,
        background: BG,
        color: FG,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 34 }}>
        {/* eslint-disable-next-line @next/next/no-img-element -- next/og renders plain <img> */}
        <img src={logoSrc} width={56} height={56} alt="" />
        Blastproof
      </div>
      <div style={{ display: "flex", fontSize: 84, letterSpacing: -3, lineHeight: 1.05 }}>
        {HEADLINE}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 14, fontSize: 28 }}>
        <div style={{ display: "flex", color: MUTED }}>
          agent › DELETE FROM users WHERE last_login_at {"<"} now() - interval &apos;6 months&apos;
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <span style={{ color: SIGNAL }}>
            Deletes {formatNumber(REPLIT.users)} users, including {REPLIT.paying} paying customers.
          </span>
          <span style={{ color: SAFE }}>Rejected. Nothing changed.</span>
        </div>
      </div>
    </div>,
    size,
  );
}
