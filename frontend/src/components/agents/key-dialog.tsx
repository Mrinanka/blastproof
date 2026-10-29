"use client";

import { CopyField } from "@/components/agents/copy-field";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { HOOK_STEPS_URL, type CreatedAgent } from "@/lib/agents";

// Open while it holds a created agent. Only the button closes it: a stray click or Escape must not
// lose a key that is shown once.
export function KeyDialog({ agent, onDone }: { agent: CreatedAgent | null; onDone: () => void }) {
  return (
    <Dialog open={agent !== null}>
      <DialogContent
        showCloseButton={false}
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Agent created</DialogTitle>
          <DialogDescription>
            This key is shown once. Copy it now: it cannot be shown again.
          </DialogDescription>
        </DialogHeader>
        {agent ? (
          <div className="space-y-3">
            <CopyField label="Key" value={agent.key} />
            <CopyField label="Claude Code setup" value={agent.claude_mcp_add} />
            <CopyField label="Hook environment" value={agent.agent_key_env} />
            <p className="text-sm text-muted-foreground">
              Run the setup line in your project folder. Put the environment line in the
              workspace&apos;s <code>.claude/settings.local.json</code> env so the hook can use it.
              The hook itself is installed by <code>workspace.py create</code>; see the{" "}
              <a
                href={HOOK_STEPS_URL}
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2"
              >
                hook steps
              </a>
              .
            </p>
          </div>
        ) : null}
        <DialogFooter>
          <Button type="button" onClick={onDone}>
            Done, I have copied it
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
