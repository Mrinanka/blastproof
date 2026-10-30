import { ReviewQueue } from "@/components/review/review-queue";

export const metadata = { title: "Review queue" };

export default function Page() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-xl font-semibold">Review queue</h1>
        <p className="text-sm text-muted-foreground">
          Writes held for your approval, oldest first.
        </p>
      </div>
      <ReviewQueue />
    </div>
  );
}
