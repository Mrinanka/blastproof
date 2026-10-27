import { connection } from "next/server";

// Minimal until F4 adds the sidebar. Rendered per request so the nonce reaches Next's scripts.
export default async function DashboardLayout({ children }: LayoutProps<"/dashboard">) {
  await connection();
  return <>{children}</>;
}
