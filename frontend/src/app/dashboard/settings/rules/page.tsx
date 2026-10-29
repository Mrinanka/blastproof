import { RulesTable } from "@/components/rules/rules-table";

export default function Page() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-xl font-semibold">Rules</h1>
        <p className="text-sm text-muted-foreground">
          The rules every write is checked against before the AI judge. Read-only.
        </p>
      </div>
      <RulesTable />
    </div>
  );
}
