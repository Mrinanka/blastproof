import Link from "next/link";

import { Button } from "@/components/ui/button";
import { GITHUB_URL, LOGIN_HREF } from "@/lib/landing/site";

import { GitHubMark } from "./github-mark";
import { Wordmark } from "./wordmark";

export const NAV_LINKS = [
  { href: "#how-it-works", label: "How it works" },
  { href: "#results", label: "Results" },
  { href: "#security", label: "Security" },
  { href: "#faq", label: "FAQ" },
];

export function Nav() {
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/75 backdrop-blur-md">
      <nav
        aria-label="Main"
        className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-4 sm:px-6"
      >
        <Link href="/" className="rounded-sm text-[15px]">
          <Wordmark />
        </Link>
        <ul className="hidden items-center gap-6 text-sm text-muted-foreground md:flex">
          {NAV_LINKS.map((link) => (
            <li key={link.href}>
              <a href={link.href} className="rounded-sm transition-colors hover:text-foreground">
                {link.label}
              </a>
            </li>
          ))}
        </ul>
        <div className="ml-auto flex items-center gap-1">
          <Button asChild variant="ghost" size="lg">
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
              <GitHubMark className="size-4" />
              <span className="sr-only sm:not-sr-only">View on GitHub</span>
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </Button>
          <Button asChild variant="outline" size="lg">
            <a href={LOGIN_HREF}>Log in</a>
          </Button>
        </div>
      </nav>
    </header>
  );
}
