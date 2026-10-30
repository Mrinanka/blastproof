import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/format";
import { REPLIT } from "@/lib/landing/content";
import { DEMO_HREF, GITHUB_URL, HEADLINE, LOGIN_HREF, PITCH } from "@/lib/landing/site";

import { GitHubMark } from "./github-mark";
import { HeroTerminal } from "./hero-terminal";

export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="relative isolate overflow-hidden">
      <div aria-hidden="true" className="landing-grid absolute inset-0 -z-10" />
      <div aria-hidden="true" className="landing-spotlight absolute inset-0 -z-10" />
      <div className="mx-auto grid w-full max-w-6xl gap-12 px-4 pt-16 pb-20 sm:px-6 sm:pt-24 lg:grid-cols-[minmax(0,10fr)_minmax(0,13fr)] lg:items-center lg:gap-14 lg:pb-28">
        <div>
          <p className="font-mono text-xs tracking-wide text-muted-foreground">
            Blastproof <span aria-hidden="true">·</span> a safety gateway for AI agents
          </p>
          <h1
            id="hero-title"
            className="mt-5 text-[2.6rem] leading-[1.04] font-semibold tracking-tight text-balance sm:text-6xl"
          >
            {HEADLINE}
          </h1>
          <p className="mt-6 max-w-xl text-lg text-pretty text-muted-foreground">{PITCH}</p>
          <div className="mt-9 grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center sm:gap-3">
            <Button asChild size="lg" className="col-span-2 h-10 px-4">
              <a href={DEMO_HREF}>Watch the demo</a>
            </Button>
            <Button asChild variant="outline" size="lg" className="h-10 px-4">
              <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
                <GitHubMark className="size-4" />
                View on GitHub
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </Button>
            <Button asChild variant="ghost" size="lg" className="h-10 px-4">
              <a href={LOGIN_HREF}>Log in</a>
            </Button>
          </div>
        </div>
        <div>
          <p className="sr-only">
            Example, replaying the Replit incident: an agent sends {REPLIT.sql}. Blastproof previews
            it on a copy: it would delete {formatNumber(REPLIT.users)} users, including{" "}
            {REPLIT.paying} paying customers ({REPLIT.enterprise} enterprise). The owner rejects it,
            and nothing changes.
          </p>
          <HeroTerminal />
        </div>
      </div>
    </section>
  );
}
