import { Suspense } from "react";

import { LoadingState } from "@/components/common/states";
import { History } from "@/components/history/history";

export const metadata = { title: "History" };

export default function Page() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-xl font-semibold">History</h1>
        <p className="text-sm text-muted-foreground">
          Every action you can see, newest first: your agents&apos; actions, or everyone&apos;s if
          you are an admin. Filters are kept in the address, so a view can be bookmarked or shared.
        </p>
      </div>
      {/* useSearchParams needs a Suspense boundary if the route is ever prerendered. */}
      <Suspense fallback={<LoadingState lines={5} label="Loading History" />}>
        <History />
      </Suspense>
    </div>
  );
}
