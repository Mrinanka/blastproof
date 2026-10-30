import { DOCS_URL, GITHUB_URL, LOGIN_HREF, PITCH } from "@/lib/landing/site";

import { Wordmark } from "./wordmark";

// No status indicator: the page is static and cannot know whether anything is up.
export function Footer() {
  const external = { target: "_blank", rel: "noopener noreferrer" } as const;
  return (
    <footer className="border-t border-border">
      <div className="mx-auto grid w-full max-w-6xl gap-8 px-4 py-12 sm:px-6 md:grid-cols-[1fr_auto]">
        <div className="max-w-md">
          <Wordmark />
          <p className="mt-3 text-sm text-pretty text-muted-foreground">{PITCH}</p>
        </div>
        <nav aria-label="Footer">
          <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground">
            <li>
              <a href={GITHUB_URL} {...external} className="rounded-sm hover:text-foreground">
                GitHub<span className="sr-only"> (opens in a new tab)</span>
              </a>
            </li>
            <li>
              <a href={DOCS_URL} {...external} className="rounded-sm hover:text-foreground">
                Docs<span className="sr-only"> (opens in a new tab)</span>
              </a>
            </li>
            <li>
              <a href={LOGIN_HREF} className="rounded-sm hover:text-foreground">
                Log in
              </a>
            </li>
          </ul>
        </nav>
        <p className="font-mono text-xs text-muted-foreground md:col-span-2">
          © {new Date().getUTCFullYear()} Blastproof
        </p>
      </div>
    </footer>
  );
}
