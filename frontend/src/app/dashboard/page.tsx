import { Overview } from "@/components/overview/overview";

export default function Page() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-xl font-semibold">Overview</h1>
        <p className="text-sm text-muted-foreground">
          Your agents&apos; actions, or everyone&apos;s if you are an admin. Today means the UTC
          day.
        </p>
      </div>
      <Overview />
    </div>
  );
}
