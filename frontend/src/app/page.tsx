import type { Metadata } from "next";

import { Architecture } from "@/components/landing/architecture";
import { ChangeRequestShot } from "@/components/landing/change-request-shot";
import { Different } from "@/components/landing/different";
import { Faq } from "@/components/landing/faq";
import { Footer } from "@/components/landing/footer";
import { Hero } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { LandingMotion } from "@/components/landing/landing-motion";
import { Nav } from "@/components/landing/nav";
import { Problem } from "@/components/landing/problem";
import { Results } from "@/components/landing/results";
import { Security } from "@/components/landing/security";
import { HEADLINE, PITCH } from "@/lib/landing/site";

// The public landing page (F13, FE-28). Static: no API call, no headers or cookies, no theme
// provider (D-287). Its CSP comes from next.config.ts (SEC-29, D-291).
const TITLE = `Blastproof: ${HEADLINE.replace(/\.$/, "").toLowerCase()}`;

export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: PITCH,
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: "/",
    siteName: "Blastproof",
    title: TITLE,
    description: PITCH,
  },
  twitter: { card: "summary_large_image", title: TITLE, description: PITCH },
};

// Without JavaScript the hero's lines would stay at Motion's starting opacity (D-292).
const NO_SCRIPT_CSS = "[data-landing-anim]{opacity:1!important;transform:none!important}";

export default function Home() {
  return (
    <div className="landing dark flex min-h-full flex-1 flex-col overflow-x-clip">
      <noscript>
        <style>{NO_SCRIPT_CSS}</style>
      </noscript>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:ring-2 focus:ring-ring"
      >
        Skip to content
      </a>
      <LandingMotion>
        <Nav />
        <main id="main-content" tabIndex={-1} className="flex-1 outline-none">
          <Hero />
          <Problem />
          <HowItWorks />
          <ChangeRequestShot />
          <Different />
          <Results />
          <Architecture />
          <Security />
          <Faq />
        </main>
        <Footer />
      </LandingMotion>
    </div>
  );
}
