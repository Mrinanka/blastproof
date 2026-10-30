import type { Metadata } from "next";

import { NotFoundMessage } from "@/components/common/not-found-message";
import { ThemeProvider } from "@/components/theme/theme-provider";

export const metadata: Metadata = { title: "Page not found" };

// Unmatched URLs are outside the proxy matcher, so they have no CSP and the theme script needs no
// nonce. Reading the nonce here would make `/` dynamic (the landing page must stay static, F13).
export default function NotFound() {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <main className="flex flex-1">
        <NotFoundMessage />
      </main>
    </ThemeProvider>
  );
}
