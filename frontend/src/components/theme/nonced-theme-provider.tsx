import { headers } from "next/headers";

import { ThemeProvider } from "@/components/theme/theme-provider";

// next-themes writes an inline script, which the nonce-only CSP (SEC-29) blocks without the proxy's
// nonce. Only per-request pages use this; the root layout stays static for the landing page (F13).
export async function NoncedThemeProvider({ children }: { children: React.ReactNode }) {
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      nonce={nonce}
    >
      {children}
    </ThemeProvider>
  );
}
