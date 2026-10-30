import Link from "next/link";

import { Button } from "@/components/ui/button";

export function NotFoundMessage() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        We could not find that page. It may have moved, or the address may have a typo.
      </p>
      <Button asChild>
        <Link href="/dashboard">Go to the dashboard</Link>
      </Button>
    </div>
  );
}
