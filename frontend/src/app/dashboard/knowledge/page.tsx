import { Knowledge } from "@/components/knowledge/knowledge";

export const metadata = { title: "Knowledge" };

export default function Page() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-xl font-semibold">Knowledge</h1>
        <p className="text-sm text-muted-foreground">
          The documents and schema the AI reads when it explains a change.
        </p>
      </div>
      <Knowledge />
    </div>
  );
}
