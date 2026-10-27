import {
  BookOpen,
  Bot,
  History,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Users,
  type LucideIcon,
} from "lucide-react";

export type Role = "admin" | "member";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
  badge?: "pending";
}

export interface NavGroup {
  label?: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    items: [
      { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
      { href: "/dashboard/review", label: "Review", icon: Inbox, badge: "pending" },
      { href: "/dashboard/history", label: "History", icon: History },
      { href: "/dashboard/knowledge", label: "Knowledge", icon: BookOpen, adminOnly: true },
    ],
  },
  {
    label: "Settings",
    items: [
      { href: "/dashboard/settings/agents", label: "Agents", icon: Bot },
      { href: "/dashboard/settings/rules", label: "Rules", icon: ListChecks },
      { href: "/dashboard/settings/users", label: "Users", icon: Users, adminOnly: true },
    ],
  },
];

// Hiding admin links is a convenience; the backend enforces roles. While the role is unknown
// (loading or failed) the member view is shown, so a link never appears before it is known.
export function navFor(role: Role | undefined): NavGroup[] {
  return NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.adminOnly || role === "admin"),
  })).filter((group) => group.items.length > 0);
}

export function isActive(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function badgeText(total: number | undefined): string | null {
  if (!total) return null;
  return total > 99 ? "99+" : String(total);
}
