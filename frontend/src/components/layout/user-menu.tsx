"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ChevronsUpDown, LogOut } from "lucide-react";
import { toast } from "sonner";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar";
import { useMe } from "@/hooks/use-me";
import { logout } from "@/lib/logout";

export function UserMenu() {
  const queryClient = useQueryClient();
  const { data: me } = useMe();

  async function onLogout() {
    if (!(await logout())) {
      toast.error("Could not log out. Try again.");
      return;
    }
    queryClient.clear();
    // A full page load to a fixed path, not a Next.js route change.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" tooltip={me?.display_name ?? "Account"}>
              <span className="grid flex-1 text-left leading-tight">
                <span className="truncate text-sm font-medium">{me?.display_name ?? "…"}</span>
                <span className="truncate text-xs text-muted-foreground">{me?.role ?? ""}</span>
              </span>
              <ChevronsUpDown className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="start" className="min-w-56">
            {me ? (
              <>
                <DropdownMenuLabel className="font-normal">
                  <div className="truncate text-sm font-medium">{me.display_name}</div>
                  <div className="truncate text-xs text-muted-foreground">{me.email}</div>
                  <div className="text-xs text-muted-foreground">{me.role}</div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
              </>
            ) : null}
            <DropdownMenuItem onSelect={() => void onLogout()}>
              <LogOut />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
