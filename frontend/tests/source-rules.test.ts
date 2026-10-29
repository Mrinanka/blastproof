// F3 guards: CONV-14, SEC-23.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../src");

const files = readdirSync(SRC, { withFileTypes: true, recursive: true })
  .filter((e) => e.isFile() && /\.(ts|tsx)$/.test(e.name) && !e.name.endsWith(".d.ts"))
  .map((e) => {
    const path = join(e.parentPath, e.name);
    return { name: relative(SRC, path).replaceAll("\\", "/"), text: readFileSync(path, "utf8") };
  });

describe("source rules", () => {
  it("reads BLASTPROOF_API_URL in exactly one file", () => {
    const readers = files.filter((f) => f.text.includes("BLASTPROOF_API_URL")).map((f) => f.name);
    expect(readers).toEqual(["lib/server/backend.ts"]);
  });

  it.each(["document.cookie", "sessionStorage", "localStorage", "NEXT_PUBLIC_"])(
    "never uses %s",
    (bad) => {
      // shadcn's sidebar keeps its own open/closed state in a cookie; it is not a credential.
      const found = files.filter(
        (f) => f.text.includes(bad) && f.name !== "components/ui/sidebar.tsx",
      );
      expect(found.map((f) => f.name)).toEqual([]);
    },
  );

  it("never uses dangerouslySetInnerHTML", () => {
    expect(files.filter((f) => f.text.includes("dangerouslySetInnerHTML"))).toEqual([]);
  });

  // F5 (CONV-17): number, money and date formatting lives in lib/format.ts.
  it("formats with Intl and toLocale* only in lib/format.ts", () => {
    const found = files.filter(
      (f) =>
        f.name !== "lib/format.ts" &&
        !f.name.startsWith("components/ui/") &&
        /toLocale(String|DateString|TimeString)|new Intl\./.test(f.text),
    );
    expect(found.map((f) => f.name)).toEqual([]);
  });

  // F4: shadcn's SidebarProvider no longer wraps a TooltipProvider; without one the sidebar crashes.
  it("wraps the dashboard layout in a TooltipProvider", () => {
    const layout = files.find((f) => f.name === "app/dashboard/layout.tsx");
    expect(layout?.text).toMatch(/<TooltipProvider>[\s\S]*<SidebarProvider/);
  });

  // F8: a flex child grows to fit its content; without min-w-0 a wide table widened the page.
  it("lets the dashboard's main area shrink below its content width", () => {
    const layout = files.find((f) => f.name === "app/dashboard/layout.tsx");
    expect(layout?.text).toMatch(/<SidebarInset className="[^"]*\bmin-w-0\b/);
  });

  // F11: the key dialog's copy field is the one place that writes to the clipboard.
  it("uses navigator.clipboard only in the agents copy field", () => {
    const found = files.filter((f) => f.text.includes("navigator.clipboard"));
    expect(found.map((f) => f.name)).toEqual(["components/agents/copy-field.tsx"]);
  });

  // F8: collapsed to icons, the name ran under the sidebar trigger.
  it("hides the product name when the sidebar is collapsed to icons", () => {
    const sidebar = files.find((f) => f.name === "components/layout/app-sidebar.tsx");
    expect(sidebar?.text).toMatch(/group-data-\[collapsible=icon\]:hidden">Blastproof</);
  });
});
