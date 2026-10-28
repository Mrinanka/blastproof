import { cookies } from "next/headers";
import { connection } from "next/server";

import { AppSidebar } from "@/components/layout/app-sidebar";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

// Rendered per request so the nonce reaches Next's scripts.
export default async function DashboardLayout({ children }: LayoutProps<"/dashboard">) {
  await connection();
  // shadcn's UI preference (open or collapsed), not a credential.
  const defaultOpen = (await cookies()).get("sidebar_state")?.value !== "false";
  return (
    <TooltipProvider>
      <SidebarProvider defaultOpen={defaultOpen}>
        <AppSidebar />
        {/* min-w-0: a flex child grows to fit wide content; without it a wide table widens the
            whole page instead of scrolling inside its own container. */}
        <SidebarInset className="min-w-0">
          <header className="flex h-12 items-center gap-2 border-b px-4">
            <SidebarTrigger />
          </header>
          {children}
        </SidebarInset>
        <Toaster />
      </SidebarProvider>
    </TooltipProvider>
  );
}
