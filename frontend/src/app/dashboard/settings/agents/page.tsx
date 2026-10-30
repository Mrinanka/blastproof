import { Agents } from "@/components/agents/agents";

export const metadata = { title: "Agents" };

export default function Page() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-xl font-semibold">Agents</h1>
        <p className="text-sm text-muted-foreground">
          Connect Claude Code to Blastproof with one key per agent.
        </p>
      </div>
      <Agents />
    </div>
  );
}
